export type Position = [number, number];

export interface Point {
  type: "Point";
  coordinates: Position;
}

export interface LineString {
  type: "LineString";
  coordinates: Position[];
}

export type Geometry = Point | LineString;

export interface Feature<G extends Geometry = Geometry, P = Record<string, unknown>> {
  type: "Feature";
  geometry: G;
  properties: P;
}

export interface FeatureCollection<
  G extends Geometry = Geometry,
  P = Record<string, unknown>,
> {
  type: "FeatureCollection";
  features: Array<Feature<G, P>>;
}
