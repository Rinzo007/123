# Transit Planner frontend architecture

## Layers

- core/ — command history, geometry helpers, topology validation.
- network-editor.ts / map-network-editor.ts — transactional editing of physical network topology.
- api.ts — transport boundary to the FastAPI backend (data preparation only).
- workers/ — CPU-heavy browser computations: matrix, routing, demand-choice, evaluation runtime; all heavy input/output uses typed arrays and transferable ArrayBuffers.
- simulation/ — analytical planning preview over the network model.
- storage.ts — IndexedDB `takt/kv` with gzip records and localStorage UI settings.
- line-cache.ts — TLC1 binary cache of road geometry.
- app.ts — composition/bootstrap layer; reusable domain logic belongs in feature modules.

## Rules

1. Overture is the single authoritative geodata provider.
2. Physical infrastructure is operational data only. Construction lifecycle and CAPEX are absent.
3. Network topology is authoritative; length and slope are derived from nodes.
4. Editing is command-based and supports undo/redo.
5. Expensive calculations run in Web Workers; a missing worker or required source is an explicit error, never a silent substitute or simplified model.
6. API contracts use the single NetworkPayload.
7. Project files are versioned and do not contain copied Overture datasets.
8. UI code does not implement routing, assignment or economics algorithms.
9. Each computational layer has exactly one implementation; fallback paths and duplicate models are removed (Этап 1).

The existing map UI remains the composition layer during incremental migration.
