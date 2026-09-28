import { fromLocalMeters } from "./projection";

export interface GravityParameters {
  speedKph: number;
  decay: number;
  intrazonalFactor: number;
}

export const DEFAULT_GRAVITY_PARAMETERS: GravityParameters = {
  speedKph: 30.0,
  decay: 0.08,
  intrazonalFactor: 0.5,
};

export interface DemandZone {
  id: string;
  centroidX: number;
  centroidY: number;
  population: number;
  jobs: number;
  attractions: Record<string, number>;
  noCarShare: number;
}

export interface ODPairDemand {
  originZoneId: string;
  destinationZoneId: string;
  tripsPerDay: number;
  purpose: string;
  baseTimeMin: number | null;
}

export interface PeriodODPairDemand {
  originZoneId: string;
  destinationZoneId: string;
  periodId: string;
  trips: number;
  purpose: string;
  baseTimeMin: number | null;
}

export interface TemporalDemandMatrix {
  pairs: PeriodODPairDemand[];
}

export interface DemandMatrix {
  pairs: ODPairDemand[];
}

export interface ReferencePeriod {
  key: string;
  startMinute: number;
  endMinute: number;
  outboundShare: number;
  returnShare: number;
}

export interface ReferencePurposeLayer {
  key: string;
  label: string;
  tripsPerResource: number;
  attractionDistanceM: number;
  maxDestinations: number;
  outboundShares: number[];
  returnShares: number[];
}

export interface ReferenceDemandLayerResult {
  purpose: string;
  label: string;
  demand: TemporalDemandMatrix;
  odPairs: Array<[string, string, number, number]>;
}

export interface ReferenceDemandLayers {
  layers: ReferenceDemandLayerResult[];
}

export interface CityPlace {
  id: string;
  name: string;
  lon: number;
  lat: number;
  basicCategory: string | null;
  taxonomyPrimary: string | null;
  taxonomyHierarchy: string[];
  importance: number;
}

export type PlacePurpose = "work" | "edu" | "shop" | "health" | "night" | "air";

const DEFAULT_PLACE_PURPOSE_CATEGORIES: Record<PlacePurpose, string[]> = {
  work: ["office", "corporate_office", "government_office", "business_center", "industrial_company"],
  edu: ["school", "university", "college", "kindergarten"],
  shop: ["shop", "shopping_mall", "supermarket", "department_store", "grocery_store"],
  health: ["hospital", "clinic", "medical_center", "pharmacy"],
  night: ["cinema", "theater", "museum", "stadium", "nightclub", "bar", "restaurant", "park", "attraction"],
  air: ["airport", "international_airport", "airport_terminal"],
};

export class PlacePurposeMapper {
  private readonly categories: Record<PlacePurpose, string[]>;

  constructor(categories: Record<PlacePurpose, string[]> = DEFAULT_PLACE_PURPOSE_CATEGORIES) {
    this.categories = categories;
  }

  purposeFor(place: CityPlace): PlacePurpose | null {
    const categories = new Set<string>([
      place.basicCategory ?? "",
      place.taxonomyPrimary ?? "",
      ...place.taxonomyHierarchy,
    ]);
    for (const [purpose, values] of Object.entries(this.categories) as Array<[PlacePurpose, string[]]>) {
      if (values.some((value) => categories.has(value))) return purpose;
    }
    return null;
  }
}

export function aggregatePlaceAttractions(
  zones: DemandZone[],
  places: CityPlace[],
  mapper: PlacePurposeMapper = new PlacePurposeMapper(),
): DemandZone[] {
  const assigned = new Map(zones.map((zone) => [zone.id, { ...zone.attractions }]));
  for (const place of places) {
    const purpose = mapper.purposeFor(place);
    if (!purpose) continue;
    let nearest: DemandZone | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const zone of zones) {
      const distance = (place.lon - zone.centroidX) ** 2 + (place.lat - zone.centroidY) ** 2;
      if (distance < bestDistance) {
        bestDistance = distance;
        nearest = zone;
      }
    }
    if (!nearest) continue;
    const values = assigned.get(nearest.id)!;
    values[purpose] = (values[purpose] ?? 0) + Math.max(0, place.importance);
  }
  return zones.map((zone) => ({
    ...zone,
    attractions: assigned.get(zone.id)!,
  }));
}

export function gravityOd(
  zones: DemandZone[],
  parameters: GravityParameters = DEFAULT_GRAVITY_PARAMETERS,
  tripRate = 0.12,
): DemandMatrix {
  if (tripRate < 0) throw new Error("trip_rate cannot be negative");
  if (parameters.speedKph <= 0) throw new Error("speed_kph must be positive");
  if (parameters.decay <= 0) throw new Error("decay must be positive");
  if (!(parameters.intrazonalFactor > 0 && parameters.intrazonalFactor <= 1)) {
    throw new Error("intrazonal_factor must be in (0, 1]");
  }

  const productions = new Map(zones.map((zone) => [zone.id, Math.max(0, zone.population * tripRate)]));
  const attractionBase = new Map(zones.map((zone) => [zone.id, Math.max(0, zone.jobs)]));
  let totalAttraction = [...attractionBase.values()].reduce((sum, value) => sum + value, 0);
  if (totalAttraction <= 0) {
    for (const zone of zones) attractionBase.set(zone.id, Math.max(0, zone.population));
    totalAttraction = [...attractionBase.values()].reduce((sum, value) => sum + value, 0);
  }
  if (zones.length === 0 || totalAttraction <= 0) return { pairs: [] };

  const raw: Array<{ origin: string; destination: string; weight: number; baseTimeMin: number }> = [];
  for (const origin of zones) {
    for (const destination of zones) {
      const distanceM = Math.hypot(destination.centroidX - origin.centroidX, destination.centroidY - origin.centroidY);
      const impedanceMinutes = distanceM === 0
        ? parameters.intrazonalFactor
        : (distanceM / 1000 / parameters.speedKph) * 60;
      const friction = Math.exp(-parameters.decay * impedanceMinutes);
      const weight = (attractionBase.get(destination.id) ?? 0) * friction;
      raw.push({ origin: origin.id, destination: destination.id, weight, baseTimeMin: impedanceMinutes });
    }
  }

  const byOrigin = new Map<string, number>();
  for (const item of raw) {
    byOrigin.set(item.origin, (byOrigin.get(item.origin) ?? 0) + item.weight);
  }

  const pairs: ODPairDemand[] = [];
  for (const item of raw) {
    const total = byOrigin.get(item.origin) ?? 0;
    const trips = Math.abs(total) < 1e-12 ? 0 : (productions.get(item.origin) ?? 0) * item.weight / total;
    pairs.push({
      originZoneId: item.origin,
      destinationZoneId: item.destination,
      tripsPerDay: trips,
      purpose: "work",
      baseTimeMin: item.baseTimeMin,
    });
  }
  return { pairs };
}

function jsRound(value: number): number {
  return Math.floor(value + 0.5);
}

function haversineM(aLon: number, aLat: number, bLon: number, bLat: number): number {
  const radius = 6_371_000.0;
  const lat1 = (aLat * Math.PI) / 180;
  const lat2 = (bLat * Math.PI) / 180;
  const dlat = ((bLat - aLat) * Math.PI) / 180;
  const dlon = ((bLon - aLon) * Math.PI) / 180;
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dlon / 2) ** 2;
  return 2 * radius * Math.asin(Math.min(1, Math.sqrt(Math.max(0, h))));
}

const ATTRACTION_ALIASES: Record<string, string[]> = {
  edu: ["edu", "education"],
  health: ["health"],
  shop: ["shop", "shopping"],
  air: ["air", "airport"],
  night: ["night", "leisure"],
};

function purposeAttraction(zone: DemandZone, purpose: ReferencePurposeLayer): number {
  const keys = ATTRACTION_ALIASES[purpose.key] ?? [purpose.key];
  return keys.reduce((sum, key) => sum + (zone.attractions[key] ?? 0), 0);
}

function selectCandidates(
  candidates: Array<{ zone: DemandZone; weight: number; distance: number }>,
  maxDestinations: number,
  distanceScaleM: number,
): Array<{ zone: DemandZone; weight: number; distance: number }> {
  const bands: Array<[number, number]> = [
    [0, distanceScaleM],
    [distanceScaleM, 2.5 * distanceScaleM],
    [2.5 * distanceScaleM, 6 * distanceScaleM],
    [6 * distanceScaleM, Number.POSITIVE_INFINITY],
  ];
  const perBand = Math.max(1, Math.round(maxDestinations / bands.length));
  const selected: Array<{ zone: DemandZone; weight: number; distance: number }> = [];

  for (const [lower, upper] of bands) {
    const band = candidates.filter((item) => lower <= item.distance && item.distance < upper);
    band.sort((a, b) => b.weight - a.weight || a.distance - b.distance || a.zone.id.localeCompare(b.zone.id));
    selected.push(...band.slice(0, perBand));
  }

  if (selected.length < maxDestinations) {
    const existing = new Set(selected.map((item) => item.zone.id));
    const remainder = candidates.filter((item) => !existing.has(item.zone.id));
    remainder.sort((a, b) => b.weight - a.weight || a.distance - b.distance || a.zone.id.localeCompare(b.zone.id));
    selected.push(...remainder.slice(0, maxDestinations - selected.length));
  }
  return selected.slice(0, maxDestinations);
}

export function generatePurposeLayer(
  zones: DemandZone[],
  purpose: ReferencePurposeLayer,
  minPopulation = 40,
  minDistanceM = 50,
  reachMultiplier = 8,
): ReferenceDemandLayerResult {
  const result: PeriodODPairDemand[] = [];
  const odPairs: Array<[string, string, number, number]> = [];
  const scale = purpose.attractionDistanceM;
  const maxDistance = reachMultiplier * scale;

  for (const origin of zones) {
    if (origin.population < minPopulation) continue;
    const production = origin.population * purpose.tripsPerResource;
    if (production <= 0) continue;

    const candidates: Array<{ zone: DemandZone; weight: number; distance: number }> = [];
    for (const destination of zones) {
      if (destination.id === origin.id) continue;
      const attraction = purposeAttraction(destination, purpose);
      if (attraction <= 0) continue;
      const distance = Math.hypot(
        destination.centroidX - origin.centroidX,
        destination.centroidY - origin.centroidY,
      );
      if (distance < minDistanceM || distance > maxDistance) continue;
      const weight = attraction * Math.exp(-distance / scale);
      if (weight > 0) candidates.push({ zone: destination, weight, distance });
    }
    if (candidates.length === 0) continue;

    const selected = selectCandidates(candidates, purpose.maxDestinations, purpose.attractionDistanceM);
    const totalWeight = selected.reduce((sum, item) => sum + item.weight, 0);
    if (totalWeight <= 0) continue;

    for (const { zone, weight, distance } of selected) {
      const baseDaily = production * weight / totalWeight;
      const baseTimeS = Math.max(120, Math.round((distance * 1.35) / 7.5 + 240));
      odPairs.push([origin.id, zone.id, baseDaily, baseTimeS]);
      purpose.outboundShares.forEach((share, index) => {
        const outbound = baseDaily * share;
        if (outbound > 0) {
          result.push({
            originZoneId: origin.id,
            destinationZoneId: zone.id,
            periodId: `period-${index}`,
            trips: outbound,
            purpose: purpose.key,
            baseTimeMin: null,
          });
        }
        const inbound = baseDaily * purpose.returnShares[index];
        if (inbound > 0) {
          result.push({
            originZoneId: zone.id,
            destinationZoneId: origin.id,
            periodId: `period-${index}`,
            trips: inbound,
            purpose: purpose.key,
            baseTimeMin: null,
          });
        }
      });
    }
  }

  return {
    purpose: purpose.key,
    label: purpose.label,
    demand: { pairs: result },
    odPairs,
  };
}

function referenceGeneratorWeights(
  zones: DemandZone[],
  places: CityPlace[],
  purpose: string,
  originLon: number,
  originLat: number,
  mapper: PlacePurposeMapper = new PlacePurposeMapper(),
): Map<string, number> {
  const demandPoints = zones.map((zone) => fromLocalMeters(zone.centroidX, zone.centroidY, originLon, originLat));
  const pointCells = new Map<string, number[]>();
  demandPoints.forEach((point, index) => {
    const key = `${Math.floor(point[0] / 0.01)},${Math.floor(point[1] / (0.01 * 0.62))}`;
    const bucket = pointCells.get(key);
    if (bucket) bucket.push(index);
    else pointCells.set(key, [index]);
  });

  const generators = new Map<string, { total: number; representative: number; bestImportance: number }>();
  for (const place of places) {
    const placePurpose = mapper.purposeFor(place);
    if (!placePurpose || placePurpose !== purpose) continue;
    const cellX = Math.floor(place.lon / 0.01);
    const cellY = Math.floor(place.lat / (0.01 * 0.62));
    let bestIndex = -1;
    let bestDistance = 900;
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (const index of pointCells.get(`${cellX + dx},${cellY + dy}`) ?? []) {
          const point = demandPoints[index];
          const distance = haversineM(place.lon, place.lat, point[0], point[1]);
          if (distance < bestDistance) {
            bestDistance = distance;
            bestIndex = index;
          }
        }
      }
    }
    if (bestIndex < 0) continue;
    const key = `${cellX},${cellY}`;
    const importance = Math.max(0, place.importance);
    const current = generators.get(key);
    if (!current) {
      generators.set(key, { total: importance, representative: bestIndex, bestImportance: importance });
    } else {
      generators.set(key, {
        total: current.total + importance,
        representative: importance > current.bestImportance ? bestIndex : current.representative,
        bestImportance: Math.max(current.bestImportance, importance),
      });
    }
  }

  const result = new Map<string, number>();
  for (const { total, representative } of generators.values()) {
    if (total > 0 && representative >= 0) {
      const zoneId = zones[representative].id;
      result.set(zoneId, (result.get(zoneId) ?? 0) + total);
    }
  }
  return result;
}

export function generateReferencePurposeLayer(
  zones: DemandZone[],
  places: CityPlace[],
  purpose: ReferencePurposeLayer,
  originLon: number,
  originLat: number,
): ReferenceDemandLayerResult {
  const points = zones.map((zone) => fromLocalMeters(zone.centroidX, zone.centroidY, originLon, originLat));
  const attractions = referenceGeneratorWeights(zones, places, purpose.key, originLon, originLat);
  const bands = [0, purpose.attractionDistanceM, 2.5 * purpose.attractionDistanceM, 6 * purpose.attractionDistanceM, Number.POSITIVE_INFINITY];
  const maxDistance = 8 * purpose.attractionDistanceM;
  const perBand = Math.max(1, jsRound(purpose.maxDestinations / 4));
  const result: Array<[string, string, number, number]> = [];

  zones.forEach((originZone, originIndex) => {
    if (originZone.population < 40) return;
    const production = originZone.population * purpose.tripsPerResource;
    if (production <= 0) return;

    const candidates: Array<{ band: number; items: Array<{ index: number; weight: number; distance: number }> }> = [];
    const bandWeight = [0, 0, 0, 0];
    let totalWeight = 0;
    const origin = points[originIndex];

    for (const [destinationIndex, attraction] of attractions) {
      if (destinationIndex === String(originIndex)) continue;
      const destination = points[Number(destinationIndex)];
      const distance = haversineM(origin[0], origin[1], destination[0], destination[1]);
      if (distance < 50 || distance > maxDistance) continue;
      const weight = attraction * Math.exp(-distance / purpose.attractionDistanceM);
      if (weight <= 0) continue;
      let band = 0;
      while (band < 3 && distance >= bands[band + 1]) band += 1;
      candidates.push({ band, items: [{ index: Number(destinationIndex), weight, distance }] });
      bandWeight[band] += weight;
      totalWeight += weight;
    }
    if (totalWeight <= 0) return;

    let roundingCarry = 0;
    for (let band = 0; band < 4; band += 1) {
      if (bandWeight[band] <= 0) continue;
      const bandCandidates = candidates
        .filter((item) => item.band === band)
        .flatMap((item) => item.items);
      bandCandidates.sort((a, b) => b.weight - a.weight || a.distance - b.distance || a.index - b.index);
      const selected = bandCandidates.slice(0, perBand);
      const selectedWeight = selected.reduce((sum, item) => sum + item.weight, 0);
      if (selectedWeight <= 0) continue;
      const bandTrips = (production * bandWeight[band]) / totalWeight;
      for (const { index, weight, distance } of selected) {
        const exact = (bandTrips * weight) / selectedWeight + roundingCarry;
        const trips = jsRound(exact);
        roundingCarry = exact - trips;
        if (trips > 0) {
          result.push([originZone.id, zones[index].id, trips, jsRound((distance * 1.35) / 7.5 + 240)]);
        }
      }
    }
  });

  const periodRows: PeriodODPairDemand[] = [];
  for (const [originId, destinationId, trips, baseS] of result) {
    purpose.outboundShares.forEach((share, index) => {
      const outbound = trips * share;
      if (outbound > 0) {
        periodRows.push({ originZoneId: originId, destinationZoneId: destinationId, periodId: `period-${index}`, trips: outbound, purpose: purpose.key, baseTimeMin: null });
      }
      const inbound = trips * purpose.returnShares[index];
      if (inbound > 0) {
        periodRows.push({ originZoneId: destinationId, destinationZoneId: originId, periodId: `period-${index}`, trips: inbound, purpose: purpose.key, baseTimeMin: null });
      }
    });
  }

  return { purpose: purpose.key, label: purpose.label, demand: { pairs: periodRows }, odPairs: result };
}

export function buildDemandLayers(zones: DemandZone[], purposes: ReferencePurposeLayer[]): ReferenceDemandLayers {
  return { layers: purposes.map((purpose) => generatePurposeLayer(zones, purpose)) };
}

export function buildReferenceDemandLayers(
  zones: DemandZone[],
  places: CityPlace[],
  purposes: ReferencePurposeLayer[],
  originLon: number,
  originLat: number,
): ReferenceDemandLayers {
  return {
    layers: purposes.map((purpose) => generateReferencePurposeLayer(zones, places, purpose, originLon, originLat)),
  };
}

export function buildTemporalDemand(
  zones: DemandZone[],
  periods: ReferencePeriod[],
  purposes: ReferencePurposeLayer[],
  tripRate = 0.12,
  decay = 0.08,
  speedKph = 30,
): TemporalDemandMatrix {
  const commuter = gravityOd(zones, { speedKph, decay, intrazonalFactor: 0.5 }, tripRate);
  const rows: PeriodODPairDemand[] = [];
  const commuterPeriodShare = periods.map((period) => (period.outboundShare + period.returnShare) / 2);
  for (const pair of commuter.pairs) {
    periods.forEach((period, index) => {
      const trips = pair.tripsPerDay * commuterPeriodShare[index];
      if (trips > 0) {
        rows.push({
          originZoneId: pair.originZoneId,
          destinationZoneId: pair.destinationZoneId,
          periodId: period.key,
          trips,
          purpose: "work",
          baseTimeMin: pair.baseTimeMin,
        });
      }
    });
  }
  const layers = buildDemandLayers(zones, purposes);
  for (const layer of layers.layers) rows.push(...layer.demand.pairs);
  return { pairs: rows };
}

export function buildDailyDemand(
  zones: DemandZone[],
  purposes: ReferencePurposeLayer[],
  tripRate = 0.12,
  decay = 0.08,
  referenceSpeedKph = 30,
): DemandMatrix {
  if (tripRate < 0) throw new Error("trip_rate cannot be negative");
  if (decay <= 0) throw new Error("decay must be positive");
  if (referenceSpeedKph <= 0) throw new Error("reference_speed_kph must be positive");
  const commuter = gravityOd(zones, { speedKph: referenceSpeedKph, decay, intrazonalFactor: 0.5 }, tripRate);
  const layers = buildDemandLayers(zones, purposes);
  const pairs: ODPairDemand[] = commuter.pairs
    .filter((pair) => pair.tripsPerDay > 0)
    .map((pair) => ({
      originZoneId: pair.originZoneId,
      destinationZoneId: pair.destinationZoneId,
      tripsPerDay: pair.tripsPerDay,
      purpose: "work",
      baseTimeMin: pair.baseTimeMin,
    }));
  for (const layer of layers.layers) {
    const totals = new Map<string, number>();
    for (const pair of layer.demand.pairs) {
      const key = `${pair.originZoneId}|${pair.destinationZoneId}`;
      totals.set(key, (totals.get(key) ?? 0) + pair.trips);
    }
    for (const [key, trips] of totals) {
      if (trips > 0) {
        const [origin, destination] = key.split("|");
        pairs.push({ originZoneId: origin, destinationZoneId: destination, tripsPerDay: trips, purpose: layer.purpose, baseTimeMin: null });
      }
    }
  }
  return { pairs };
}
