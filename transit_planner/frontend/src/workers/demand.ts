import type { FeatureCollection } from "../geojson";
import type { ReferenceDemandResponse } from "../api";
import { toLocalMeters } from "../projection";
import {
  CITY_DEMAND_DECAY,
  CITY_DEMAND_REFERENCE_SPEED_KPH,
  CITY_DEMAND_TRIP_RATE,
  REFERENCE_PERIODS,
  REFERENCE_PURPOSES,
} from "../reference-model";
import type {
  DemandPeriodInput,
  DemandPlaceInput,
  DemandPurposeInput,
  DemandWorkerResult,
  DemandZoneInput,
  DemandWorkerMessage,
} from "./demand.worker";

export type { DemandWorkerMessage };

export interface DemandWorkerRequestInput {
  zones: DemandZoneInput;
  places: DemandPlaceInput;
  purposes: DemandPurposeInput;
  periods: DemandPeriodInput;
  originLon: number;
  originLat: number;
  tripRate: number;
  decay: number;
  speedKph: number;
}

let requestId = 0;

export function buildReferenceDemand(
  input: DemandWorkerRequestInput,
): Promise<ReferenceDemandResponse> {
  const worker = new Worker(new URL("./demand.worker.ts", import.meta.url), { type: "module" });
  const job = ++requestId;
  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<DemandWorkerResult>) => {
      if (event.data.job !== job) return;
      worker.terminate();
      const result = event.data;
      const zoneCount = input.zones.ids.length;
      const pts: Array<[number, number, number, number]> = input.zones.ids.map((_, index) => [
        input.places.lon[index] ?? 0,
        input.places.lat[index] ?? 0,
        input.zones.population[index],
        Math.max(input.zones.jobs[index], input.zones.population[index]),
      ]);
      const od: Array<[number, number, number, number]> = [];
      for (let index = 0; index < result.odOrigin.length; index += 1) {
        od.push([
          result.odOrigin[index],
          result.odDestination[index],
          result.odTrips[index],
          Math.max(120, result.odBaseTime[index]),
        ]);
      }
      const layers: ReferenceDemandResponse["layers"] = [];
      for (let index = 0; index < result.layerPurpose.length; index += 1) {
        const purpose = result.layerPurpose[index];
        const existing = layers.find((layer) => layer.purpose === purpose);
        const row: [number, number, number, number] = [
          result.layerOrigin[index],
          result.layerDestination[index],
          result.layerTrips[index],
          Math.max(120, result.layerBaseTime[index]),
        ];
        if (existing) existing.od.push(row);
        else layers.push({ purpose, label: purpose, od: [row], out: [], ret: [] });
      }
      resolve({
        city: "dynamic",
        source: "TS demand worker",
        pts,
        od,
        baselineT: null,
        layers,
        meta: {
          zones: zoneCount,
          commuter_od_pairs: od.length,
          purpose_layers: layers.length,
          purpose_od_pairs: od.length,
          baselineT_included: false,
        },
      });
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "demand worker failed"));
    };
    const message: DemandWorkerMessage = {
      type: "build",
      job,
      zones: input.zones,
      places: input.places,
      purposes: input.purposes,
      periods: input.periods,
      originLon: input.originLon,
      originLat: input.originLat,
      tripRate: input.tripRate,
      decay: input.decay,
      speedKph: input.speedKph,
    };
    worker.postMessage(message, {
      transfer: [
        input.zones.x.buffer,
        input.zones.y.buffer,
        input.zones.population.buffer,
        input.zones.jobs.buffer,
        input.zones.attractions.buffer,
        input.places.lon.buffer,
        input.places.lat.buffer,
        input.places.importance.buffer,
        input.purposes.tripsPerResource.buffer,
        input.purposes.attractionDistanceM.buffer,
        input.purposes.maxDestinations.buffer,
        input.purposes.outboundShares.buffer,
        input.purposes.returnShares.buffer,
        input.periods.startMinute.buffer,
        input.periods.endMinute.buffer,
        input.periods.outboundShare.buffer,
        input.periods.returnShare.buffer,
      ],
    });
  });
}

export function purposesToWorkerInput(): DemandPurposeInput {
  const count = REFERENCE_PURPOSES.length;
  const tripsPerResource = new Float64Array(count);
  const attractionDistanceM = new Float64Array(count);
  const maxDestinations = new Int32Array(count);
  const outboundShares = new Float64Array(count * REFERENCE_PERIODS.length);
  const returnShares = new Float64Array(count * REFERENCE_PERIODS.length);
  REFERENCE_PURPOSES.forEach((purpose, index) => {
    tripsPerResource[index] = purpose.tripsPerResource;
    attractionDistanceM[index] = purpose.attractionDistanceM;
    maxDestinations[index] = purpose.maxDestinations;
    purpose.outboundShares.forEach((share, periodIndex) => {
      outboundShares[index * REFERENCE_PERIODS.length + periodIndex] = share;
    });
    purpose.returnShares.forEach((share, periodIndex) => {
      returnShares[index * REFERENCE_PERIODS.length + periodIndex] = share;
    });
  });
  return {
    keys: REFERENCE_PURPOSES.map((purpose) => purpose.key),
    labels: REFERENCE_PURPOSES.map((purpose) => purpose.label),
    tripsPerResource,
    attractionDistanceM,
    maxDestinations,
    outboundShares,
    returnShares,
  };
}

export function periodsToWorkerInput(): DemandPeriodInput {
  const count = REFERENCE_PERIODS.length;
  const startMinute = new Int32Array(count);
  const endMinute = new Int32Array(count);
  const outboundShare = new Float64Array(count);
  const returnShare = new Float64Array(count);
  REFERENCE_PERIODS.forEach((period, index) => {
    startMinute[index] = period.startMinute;
    endMinute[index] = period.endMinute;
    outboundShare[index] = period.outboundShare;
    returnShare[index] = period.returnShare;
  });
  return {
    keys: REFERENCE_PERIODS.map((period) => period.key),
    startMinute,
    endMinute,
    outboundShare,
    returnShare,
  };
}

/**
 * Zone columns from the WorldPop zones GeoJSON.
 *
 * Zone centroids arrive in WGS84 and are projected into the network's local
 * metre space, because the worker measures place-to-zone distances in the same
 * metric. Purpose attractions come from the zone properties, not from zeros.
 */
export function populationZonesToWorkerInput(
  populationZones: FeatureCollection,
  originLon: number,
  originLat: number,
): DemandZoneInput {
  const features = (populationZones.features ?? []).filter(
    (feature) => feature.geometry?.type === "Point",
  );
  const count = features.length;
  const attractionKeys = REFERENCE_PURPOSES.map((purpose) => purpose.key);
  const x = new Float64Array(count);
  const y = new Float64Array(count);
  const population = new Float64Array(count);
  const jobs = new Float64Array(count);
  const attractions = new Float64Array(count * attractionKeys.length);
  const ids: string[] = [];
  features.forEach((feature, index) => {
    const coordinates = (feature.geometry as { coordinates: [number, number] }).coordinates;
    const local = toLocalMeters(coordinates[0], coordinates[1], originLon, originLat);
    const properties = (feature.properties ?? {}) as Record<string, unknown>;
    x[index] = local.x;
    y[index] = local.y;
    population[index] = Number(properties.population ?? 0);
    jobs[index] = Number(properties.jobs ?? 0);
    const zoneAttractions = (properties.purpose_attractions ?? {}) as Record<string, unknown>;
    attractionKeys.forEach((key, keyIndex) => {
      attractions[index * attractionKeys.length + keyIndex] = Number(zoneAttractions[key] ?? 0);
    });
    ids.push(String(properties.id ?? `zone-${index}`));
  });
  return { ids, x, y, population, jobs, attractionKeys, attractions };
}

export function placesToWorkerInput(places: FeatureCollection): DemandPlaceInput {
  const features = places.features ?? [];
  const count = features.length;
  const lon = new Float64Array(count);
  const lat = new Float64Array(count);
  const importance = new Float64Array(count);
  const categories: string[] = [];
  features.forEach((feature, index) => {
    const geometry = feature.geometry;
    if (geometry?.type === "Point") {
      lon[index] = geometry.coordinates[0];
      lat[index] = geometry.coordinates[1];
    }
    const properties = (feature.properties ?? {}) as Record<string, unknown>;
    importance[index] = Number(properties.importance ?? 1);
    categories[index] = String(properties.basic_category ?? properties.taxonomy_primary ?? "");
  });
  return { ids: features.map((_, index) => `place-${index}`), lon, lat, categories, importance };
}

export interface ReferenceDemandWorkerRequest {
  populationZones: FeatureCollection;
  places: FeatureCollection;
  originLon: number;
  originLat: number;
  tripRate?: number;
  decay?: number;
  speedKph?: number;
}

/** Assemble the worker request from already loaded city datasets. */
export function referenceDemandWorkerRequest(
  request: ReferenceDemandWorkerRequest,
): DemandWorkerRequestInput {
  const zones = populationZonesToWorkerInput(
    request.populationZones,
    request.originLon,
    request.originLat,
  );
  if (zones.ids.length === 0) {
    throw new Error("Нет зон населения: загрузите растр WorldPop через «Данные Overture»");
  }
  return {
    zones,
    places: placesToWorkerInput(request.places),
    purposes: purposesToWorkerInput(),
    periods: periodsToWorkerInput(),
    originLon: request.originLon,
    originLat: request.originLat,
    tripRate: request.tripRate ?? CITY_DEMAND_TRIP_RATE,
    decay: request.decay ?? CITY_DEMAND_DECAY,
    speedKph: request.speedKph ?? CITY_DEMAND_REFERENCE_SPEED_KPH,
  };
}
