# Stable direction keys

Deduplication can identify a direction with the canonical `DirectionKey`:

```python
(route_id, direction_index)
```

The legacy integer `unit_id` remains a local positional index for dense arrays and
spatial structures. It must not be used as a persistent identifier in exported or
cached analysis results.

`dedup_policy.dedup_compute_removals()` accepts both integer IDs and `DirectionKey`
values during the migration period. New analysis producers should prefer
`DirectionKey`.
