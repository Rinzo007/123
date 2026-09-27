# Transit Planner frontend architecture

## Layers

- core/ — application infrastructure: project lifecycle, command history, geometry, cache and jobs.
- network-editor.ts — transactional editing of physical network topology.
- api.ts — transport boundary to the FastAPI backend.
- workers/ — CPU-heavy browser computations.
- simulation/ — lightweight analytical tools.
- app.ts — current UI composition layer; reusable domain logic belongs in core or feature modules.

## Rules

1. Overture is the primary external geodata provider.
2. Physical infrastructure is operational data only. Construction lifecycle and CAPEX are absent.
3. Network topology is authoritative; length and slope are derived from nodes.
4. Editing is command-based and supports undo/redo.
5. Expensive calculations use JobManager and ComputationCache.
6. API contracts use the single NetworkPayload.
7. Project files are versioned and do not contain copied Overture datasets.
8. UI code does not implement routing, assignment or economics algorithms.

The existing map UI remains the composition layer during incremental migration.
