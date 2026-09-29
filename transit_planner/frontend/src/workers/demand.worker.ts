import {
  buildDailyDemand,
  buildReferenceDemandLayers,
  buildTemporalDemand,
  type CityPlace,
  type DemandZone,
  type ReferencePeriod,
  type ReferencePurposeLayer,
} from "../demand-model";

export interface DemandZoneInput {
  ids: string[];
  x: Float64Array;
  y: Float64Array;
  population: Float64Array;
  jobs: Float64Array;
  /** Share of households without a car, per zone. */
  attractionKeys: string[];
  attractions: Float64Array;
}

export interface DemandPlaceInput {
  ids: string[];
  lon: Float64Array;
  lat: Float64Array;
  categories: string[];
  importance: Float64Array;
}

export interface DemandPurposeInput {
  keys: string[];
  labels: string[];
  tripsPerResource: Float64Array;
  attractionDistanceM: Float64Array;
  maxDestinations: Int32Array;
  outboundShares: Float64Array;
  returnShares: Float64Array;
}

export interface DemandPeriodInput {
  keys: string[];
  startMinute: Int32Array;
  endMinute: Int32Array;
  outboundShare: Float64Array;
  returnShare: Float64Array;
}

export interface DemandWorkerRequest {
  type: "build";
  job: number;
  zones: DemandZoneInput;
  places: DemandPlaceInput;
  purposes: DemandPurposeInput;
  periods: DemandPeriodInput;
  originLon: number;
  originLat: number;
}

export interface DemandWorkerResult {
  type: "built";
  job: number;
  odOrigin: Int32Array;
  odDestination: Int32Array;
  odTrips: Float64Array;
  odBaseTime: Float64Array;
  layerPurpose: string[];
  layerOrigin: Int32Array;
  layerDestination: Int32Array;
  layerTrips: Float64Array;
  layerBaseTime: Float64Array;
  /** Period-keyed demand: same gravity commuter rows plus purpose layers. */
  temporalPeriod: string[];
  temporalOrigin: Int32Array;
  temporalDestination: Int32Array;
  temporalTrips: Float64Array;
  temporalPurpose: string[];
}

export type DemandWorkerMessage = DemandWorkerRequest | { type: "cancel"; before: number };

let requestId = 0;

function toDemandZones(input: DemandZoneInput): DemandZone[] {
  const zones: DemandZone[] = [];
  for (let index = 0; index < input.ids.length; index += 1) {
    const attractions: Record<string, number> = {};
    for (let keyIndex = 0; keyIndex < input.attractionKeys.length; keyIndex += 1) {
      const key = input.attractionKeys[keyIndex];
      const value = input.attractions[index * input.attractionKeys.length + keyIndex];
      if (value > 0) attractions[key] = value;
    }
    zones.push({
      id: input.ids[index],
      centroidX: input.x[index],
      centroidY: input.y[index],
      population: input.population[index],
      jobs: input.jobs[index],
      attractions,
    });
  }
  return zones;
}

function toCityPlaces(input: DemandPlaceInput): CityPlace[] {
  const places: CityPlace[] = [];
  for (let index = 0; index < input.ids.length; index += 1) {
    places.push({
      id: input.ids[index],
      name: input.ids[index],
      lon: input.lon[index],
      lat: input.lat[index],
      basicCategory: input.categories[index] ?? null,
      taxonomyPrimary: null,
      taxonomyHierarchy: [],
      importance: input.importance[index],
    });
  }
  return places;
}

function toPurposeLayers(input: DemandPurposeInput): ReferencePurposeLayer[] {
  const layers: ReferencePurposeLayer[] = [];
  for (let index = 0; index < input.keys.length; index += 1) {
    layers.push({
      key: input.keys[index],
      label: input.labels[index],
      tripsPerResource: input.tripsPerResource[index],
      attractionDistanceM: input.attractionDistanceM[index],
      maxDestinations: input.maxDestinations[index],
      outboundShares: Array.from(input.outboundShares.slice(index * 5, (index + 1) * 5)),
      returnShares: Array.from(input.returnShares.slice(index * 5, (index + 1) * 5)),
    });
  }
  return layers;
}

function toPeriods(input: DemandPeriodInput): ReferencePeriod[] {
  const periods: ReferencePeriod[] = [];
  for (let index = 0; index < input.keys.length; index += 1) {
    periods.push({
      key: input.keys[index],
      startMinute: input.startMinute[index],
      endMinute: input.endMinute[index],
      outboundShare: input.outboundShare[index],
      returnShare: input.returnShare[index],
    });
  }
  return periods;
}

self.onmessage = (event: MessageEvent<DemandWorkerMessage>) => {
  const input = event.data;
  if (input.type !== "build") return;

  const zones = toDemandZones(input.zones);
  const places = toCityPlaces(input.places);
  const purposes = toPurposeLayers(input.purposes);
  const periods = toPeriods(input.periods);

  const daily = buildDailyDemand(zones, purposes);
  const referenceLayers = buildReferenceDemandLayers(zones, places, purposes, input.originLon, input.originLat);
  const temporal = buildTemporalDemand(zones, periods, purposes);

  const zoneIndex = new Map(zones.map((zone, index) => [zone.id, index]));
  const odOrigin: number[] = [];
  const odDestination: number[] = [];
  const odTrips: number[] = [];
  const odBaseTime: number[] = [];
  for (const pair of daily.pairs) {
    const origin = zoneIndex.get(pair.originZoneId);
    const destination = zoneIndex.get(pair.destinationZoneId);
    if (origin === undefined || destination === undefined) continue;
    odOrigin.push(origin);
    odDestination.push(destination);
    odTrips.push(pair.tripsPerDay);
    odBaseTime.push(pair.baseTimeMin ?? 0);
  }

  const layerPurpose: string[] = [];
  const layerOrigin: number[] = [];
  const layerDestination: number[] = [];
  const layerTrips: number[] = [];
  const layerBaseTime: number[] = [];
  for (const layer of referenceLayers.layers) {
    for (const pair of layer.demand.pairs) {
      const origin = zoneIndex.get(pair.originZoneId);
      const destination = zoneIndex.get(pair.destinationZoneId);
      if (origin === undefined || destination === undefined) continue;
      layerPurpose.push(layer.purpose);
      layerOrigin.push(origin);
      layerDestination.push(destination);
      layerTrips.push(pair.trips);
      layerBaseTime.push(pair.baseTimeMin ?? 0);
    }
  }

  const temporalPeriod: string[] = [];
  const temporalOrigin: number[] = [];
  const temporalDestination: number[] = [];
  const temporalTrips: number[] = [];
  const temporalPurpose: string[] = [];
  for (const pair of temporal.pairs) {
    const origin = zoneIndex.get(pair.originZoneId);
    const destination = zoneIndex.get(pair.destinationZoneId);
    if (origin === undefined || destination === undefined) continue;
    temporalPeriod.push(pair.periodId);
    temporalOrigin.push(origin);
    temporalDestination.push(destination);
    temporalTrips.push(pair.trips);
    temporalPurpose.push(pair.purpose);
  }

  const result: DemandWorkerResult = {
    type: "built",
    job: input.job,
    odOrigin: Int32Array.from(odOrigin),
    odDestination: Int32Array.from(odDestination),
    odTrips: Float64Array.from(odTrips),
    odBaseTime: Float64Array.from(odBaseTime),
    layerPurpose,
    layerOrigin: Int32Array.from(layerOrigin),
    layerDestination: Int32Array.from(layerDestination),
    layerTrips: Float64Array.from(layerTrips),
    layerBaseTime: Float64Array.from(layerBaseTime),
    temporalPeriod,
    temporalOrigin: Int32Array.from(temporalOrigin),
    temporalDestination: Int32Array.from(temporalDestination),
    temporalTrips: Float64Array.from(temporalTrips),
    temporalPurpose,
  };

  self.postMessage(result, {
    transfer: [
      result.odOrigin.buffer,
      result.odDestination.buffer,
      result.odTrips.buffer,
      result.odBaseTime.buffer,
      result.layerOrigin.buffer,
      result.layerDestination.buffer,
      result.layerTrips.buffer,
      result.layerBaseTime.buffer,
      result.temporalOrigin.buffer,
      result.temporalDestination.buffer,
      result.temporalTrips.buffer,
    ],
  });
};
