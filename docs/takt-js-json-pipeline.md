# Takt JS/JSON pipeline

The Takt city pipeline uses the pinned JavaScript bundle as the calculation engine and JSON files as its input/output contract.

## Runtime contract

```text
tests/fixtures/takt_release_city_cases.json
        |
        v
scripts/takt_city_python_snapshot.py
        |
        v
scripts/takt_city_browser_snapshot.js
        |
        v
scripts/bd956ff0a1875604740f.js
        |
        v
takt_result.json
```

Python is an orchestration layer only. It does not import `passenger_flow` or `od` for the city calculation.

The manifest pins one canonical bundle and four JSON inputs for each city case:
`model`, `demand`, `baseline`, and `purposes`.

## Pinned cities

- `amsterdam-v8`
- `berlin-v5`
- `hong-kong-v6`

The checked-in city golden is the canonical JS snapshot. Generated Python-orchestrator output is temporary CI data and is compared against that JS golden.

## Local execution

One city:

```bash
python scripts/takt_city_python_snapshot.py --city berlin-v5
```

All pinned cities:

```bash
python scripts/takt_city_python_snapshot.py --all
```


## Release checks

```bash
python scripts/takt_release_gate.py --require-city-snapshots --check-city-schemas
```

The P7 workflow also:
- validates the pinned bundle and JSON inputs;
- checks Python orchestration syntax;
- checks the canonical JS syntax;
- regenerates all three cities;
- compares generated results with the checked-in JS goldens;
- validates generated JSON.

The old Takt workflows that installed and executed the Python `passenger_flow` implementation are retired.

## Direct source adapter (P9)

For a city that is not already packaged, the repository now has a single upstream
adapter that can start from WorldPop + OSM instead of requiring hand-written
Takt JSON:

```bash
python scripts/takt_city_sources.py \
  --city voronezh-v1 \
  --place Voronezh \
  --worldpop /data/rus_pop_2025_CN_100m_R2025A_v1.tif \
  --boundary /data/voronezh-boundary.geojson \
  --osm \
  --trips-per-resident 1.0 \
  --d0-m 5000 \
  --k 12 \
  --output-dir /data/voronezh
```

The adapter performs these source steps:

```
WorldPop GeoTIFF + boundary
        -> aggregated demand points
        -> Takt-compatible gravity OD
        -> demand.json

OSM Overpass route relations
        -> route directions + stops
        -> baseline.json

optional purpose OD
        -> purposes.json

all four JSON inputs + pinned bundle
        -> takt_city_manifest.json
```

The resulting package is validated automatically. Add `--run` to invoke the
same JavaScript engine used by the release pipeline and write
`results/<city>/takt_result.json`.

WorldPop processing requires `rasterio` and `numpy`. OSM mode uses the public
Nominatim/Overpass endpoints and can be avoided entirely with the offline
`--population` + `--routes` inputs.

The gravity stage is deliberately parameterized (`--trips-per-resident`,
`--d0-m`, `--k`) rather than silently claiming a city calibration. For a
calibrated OD matrix, pass `--od`; for purpose-specific layers, pass
`--purposes`. The Takt runtime itself remains JavaScript-only.

## External OSM/WorldPop/OD integration

Upstream geodata processing should produce four JSON products:

- **demand** — zone points `pts` plus OD rows `od`; population from WorldPop is used upstream when building the demand/OD model.
- **baseline** — route lines and stops derived from OSM/GTFS/network processing.
- **purposes** — purpose-specific OD layers in the Takt JSON format.
- **model** — city/model parameters consumed by the Takt bundle.

Create a portable manifest for those products:

```bash
python scripts/takt_build_city_package.py \
  --name voronezh-v1 \
  --version v1 \
  --model /data/voronezh/model.json \
  --demand /data/voronezh/demand.json \
  --baseline /data/voronezh/baseline.json \
  --purposes /data/voronezh/purposes.json \
  --bundle scripts/bd956ff0a1875604740f.js \
  --output /data/voronezh/takt_city_manifest.json
```

Validate the package before running the engine:

```bash
python scripts/takt_validate_package.py /data/voronezh/takt_city_manifest.json
```

Run the external package directly:

```bash
python scripts/takt_city_python_snapshot.py \
  --manifest /data/voronezh/takt_city_manifest.json \
  --city voronezh-v1 \
  --output-dir /data/voronezh/takt-results
```

Relative paths inside a custom manifest are resolved from the manifest directory. Absolute paths are also supported, so the OSM/WorldPop/OD pipeline can keep its source data outside the repository.
