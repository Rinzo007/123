/**
 * Operating periods and purpose layers of the reference model.
 *
 * Literals are kept here instead of a runtime fetch so the demand pipeline has
 * no server dependency. `tests/test_reference_model_parity.py` asserts they
 * match `model.json` exactly, so changing the model without updating the
 * frontend fails the suite rather than silently diverging.
 */
export interface ReferencePeriodRow {
  key: string;
  startMinute: number;
  endMinute: number;
  outboundShare: number;
  returnShare: number;
}

export interface ReferencePurposeRow {
  key: string;
  label: string;
  tripsPerResource: number;
  attractionDistanceM: number;
  maxDestinations: number;
  outboundShares: number[];
  returnShares: number[];
}

export const REFERENCE_PERIODS: readonly ReferencePeriodRow[] = [
  { key: "early", startMinute: 240, endMinute: 360, outboundShare: 0.06, returnShare: 0.01 },
  { key: "am", startMinute: 360, endMinute: 540, outboundShare: 0.6, returnShare: 0.06 },
  { key: "mid", startMinute: 540, endMinute: 900, outboundShare: 0.2, returnShare: 0.18 },
  { key: "pm", startMinute: 900, endMinute: 1140, outboundShare: 0.1, returnShare: 0.55 },
  { key: "eve", startMinute: 1140, endMinute: 1440, outboundShare: 0.04, returnShare: 0.2 },
] as const;

export const REFERENCE_PURPOSES: readonly ReferencePurposeRow[] = [
  {
    key: "edu",
    label: "School or campus",
    tripsPerResource: 0.16,
    attractionDistanceM: 1600,
    maxDestinations: 6,
    outboundShares: [0.04, 0.76, 0.14, 0.05, 0.01],
    returnShares: [0, 0.02, 0.6, 0.32, 0.06],
  },
  {
    key: "health",
    label: "Hospital or clinic",
    tripsPerResource: 0.06,
    attractionDistanceM: 3000,
    maxDestinations: 6,
    outboundShares: [0.08, 0.34, 0.36, 0.16, 0.06],
    returnShares: [0.04, 0.12, 0.36, 0.32, 0.16],
  },
  {
    key: "shop",
    label: "Shops",
    tripsPerResource: 0.34,
    attractionDistanceM: 2000,
    maxDestinations: 6,
    outboundShares: [0.01, 0.07, 0.44, 0.36, 0.12],
    returnShares: [0.01, 0.03, 0.36, 0.42, 0.18],
  },
  {
    key: "air",
    label: "Airport",
    tripsPerResource: 0.03,
    attractionDistanceM: 14000,
    maxDestinations: 2,
    outboundShares: [0.18, 0.24, 0.26, 0.2, 0.12],
    returnShares: [0.06, 0.14, 0.26, 0.28, 0.26],
  },
  {
    key: "night",
    label: "Bar, café or venue",
    tripsPerResource: 0.22,
    attractionDistanceM: 3000,
    maxDestinations: 6,
    outboundShares: [0, 0.02, 0.14, 0.32, 0.52],
    returnShares: [0.02, 0.02, 0.08, 0.24, 0.64],
  },
] as const;

