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

The legacy launcher `scripts/run_takt_p6_local.py` remains as a compatibility entry point and invokes the same JS/JSON pipeline.

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
