#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const bundlePath = process.env.TAKT_BUNDLE_PATH
  ? path.resolve(process.env.TAKT_BUNDLE_PATH)
  : path.join(ROOT, "scripts", "bd956ff0a1875604740f7.js");
const bundle = fs.readFileSync(bundlePath, "utf8");
const marker = "})();";
const markerIndex = bundle.lastIndexOf(marker);
if (markerIndex < 0) {
  throw new Error("Could not locate Takt bundle IIFE terminator");
}

const sandbox = {
  console,
  performance,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  TextEncoder,
  TextDecoder,
  URL,
  URLSearchParams,
  Uint8Array,
  Uint16Array,
  Uint32Array,
  Int32Array,
  Float32Array,
  Float64Array,
  DataView,
  ArrayBuffer,
  SharedArrayBuffer,
  BigInt64Array,
  BigUint64Array,
  Math,
  Date,
  JSON,
  Map,
  Set,
  WeakMap,
  WeakSet,
  Promise,
  Error,
  TypeError,
  RangeError,
  Symbol,
  Reflect,
  Object,
  Array,
  Number,
  String,
  Boolean,
  RegExp,
  parseInt,
  parseFloat,
  isFinite,
  isNaN,
  atob: globalThis.atob,
  btoa: globalThis.btoa,
};

sandbox.globalThis = sandbox;
sandbox.self = {
  location: { hostname: "localhost" },
  addEventListener() {},
  postMessage() {},
};

const instrumented =
  bundle.slice(0, markerIndex) +
  "\nglobalThis.__TAKT_Bs = Bs;\n" +
  bundle.slice(markerIndex);

vm.runInNewContext(instrumented, sandbox, {
  filename: bundlePath,
  displayErrors: true,
});

if (typeof sandbox.__TAKT_Bs !== "function") {
  throw new Error("Takt Bs() was not exported by the harness");
}

const city = {
  pts: [
    [0.0000, 52.3700, 1000],
    [0.0050, 52.3700, 1000],
  ],
  od: [
    [0, 1, 100],
    [1, 0, 100],
  ],
  model: {},
};

const lines = [{
  id: 1,
  mode: "bus",
  row: "mixed",
  stops: [
    [0.0000, 52.3700],
    [0.0050, 52.3700],
  ],
  headways: [10, 10, 10, 10, 10],
  bothWays: false,
}];

const geoms = [{
  stops: [
    [0.0000, 52.3700],
    [0.0050, 52.3700],
  ],
  cum: [0, 340],
  cumT: [0, 68],
  segLen: [340],
  segCostMul: [1],
}];
const baseTimeS = [
  [300, 300],
  [300, 300],
  [300, 300],
  [300, 300],
  [300, 300],
];
const fare = { base: 0.6, perKm: 0.12 };

async function main() {
  const result = await sandbox.__TAKT_Bs(
    city,
    lines,
    geoms,
    baseTimeS,
    [],
    false,
    undefined,
    undefined,
    fare,
  );

  const line = result.lines?.[0];
  const snapshot = {
    reference: {
      engine: "Takt web bundle",
      bundle: "bd956ff0a1875604740f.js",
    },
    case: {
      name: "synthetic-2-stop-bus",
      points: city.pts,
      od: city.od,
      line: lines[0],
    },
    result: {
      ridersPerDay: result.ridersPerDay,
      transferTrips: result.transferTrips,
      coveredCommuters: result.coveredCommuters,
      totalCommuters: result.totalCommuters,
      capitalCostM: result.capitalCostM,
      revenueDay: result.revenueDay,
      opexDay: result.opexDay,
      modeSplit: result.modeSplit,
      line: line
        ? {
            id: line.id,
            mode: line.mode,
            ridersPerDay: line.ridersPerDay,
            lengthKm: line.lengthKm,
            capitalCostM: line.capitalCostM,
            fleet: line.fleet,
            revenueDay: line.revenueDay,
            opexDay: line.opexDay,
            periods: line.periods?.map((p) => ({
              riders: p.riders,
              fleet: p.fleet,
              plf: p.plf,
              effHeadway: p.effHeadway,
              minHeadway: p.minHeadway,
              minStationHeadway: p.minStationHeadway,
            })),
          }
        : null,
    },
  };

  const output = process.argv[2]
    ? path.resolve(process.argv[2])
    : path.join(ROOT, "tests", "fixtures", "takt_browser_snapshot.json");
  fs.writeFileSync(output, JSON.stringify(snapshot, null, 2) + "\n", "utf8");
  process.stdout.write(JSON.stringify(snapshot, null, 2) + "\n");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
