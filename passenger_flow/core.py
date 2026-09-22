    agg["seg_forward_totals"] = smoothed_seg_forward.as_dict()
    agg["seg_reverse_totals"] = smoothed_seg_reverse.as_dict()
    agg["seq_stop_totals"] = smoothed_stop.as_dict()
    if perf_stats is not None:
        perf_stats.setdefault("msa_s", 0.0)
        perf_stats["msa_s"] += perf_counter() - msa_started
    return agg, iteration, final_gap


def _assigned_demand_trips(
    period_flows: Sequence[PeriodFlow],
    fallback_interzonal_trips: float,
) -> float:
    """Return the demand actually expanded into period/directional assignments."""
    assigned = float(sum(p.total_trips for p in period_flows))
    return assigned if assigned > 0.0 else float(fallback_interzonal_trips)

def _run_period(
    ctx: _AssignContext,
    period: Period | None,
    *,
    layer_contexts: Sequence[tuple[_AssignContext, float, float]] | None = None,
    period_index: int,
    period_hours: float,
    wait_crowding_per_100_min: float,
    reliability_extra: Mapping[int, float] | None,
    msa_max_iterations: int | None,
    msa_gap: float,
    line: Any,
    perf_stats: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Один проход (или MSA-серия) для одного периода.

    Режим выбирается автоматически: MSA при заданных
    ``msa_max_iterations`` + crowding; иначе crowding (двухпроходный)
    или обычный (один проход).
    """
    out_factor = period.out if period is not None else 1.0
    ret_factor = period.ret if period is not None else 1.0
    contexts = list(layer_contexts or ((ctx, out_factor, ret_factor),))
    journey_cache = {} if len(contexts) > 1 else None

    if wait_crowding_per_100_min > 0 and msa_max_iterations is not None:
        pass_agg, msa_iters, msa_final_gap = _run_msa_period_layers(
            contexts,
            wait_crowding_per_100_min=wait_crowding_per_100_min,
            reliability_extra=reliability_extra,
            max_iterations=msa_max_iterations,
            gap_tol=msa_gap,
            period_index=period_index,
            period_hours=period_hours,
            perf_stats=perf_stats,
        )
        pass_agg["_msa_iterations"] = msa_iters
        pass_agg["_msa_gap"] = msa_final_gap
        line(
            f"  MSA {period.key if period else 'общий'}: "
            f"{msa_iters} итераций, разрыв {msa_final_gap*100:.2f}%"
        )
        return pass_agg
    if len(contexts) > 1:
        if wait_crowding_per_100_min > 0:
            pass_one = _assign_layer_contexts(
                contexts, period_index=period_index, wait_extra=reliability_extra,
                perf_stats=perf_stats,
                journey_cache=journey_cache,
            )
            crowd_started = perf_counter() if perf_stats is not None else 0.0
            crowd_state = _build_crowd_state(
                ctx.route_sequences, pass_one.get("seg_forward_totals", {}), pass_one.get("seg_reverse_totals", {}),
                pass_one.get("seq_stop_totals", {}), ctx.seq_headway_min, ctx.vehicle_specs, period_hours, period_index=period_index,
            )
            if perf_stats is not None:
                perf_stats.setdefault("crowd_state_s", 0.0)
                perf_stats["crowd_state_s"] += perf_counter() - crowd_started
            return _assign_layer_contexts(
                contexts, period_index=period_index, wait_extra=reliability_extra,
                crowd_state=crowd_state, perf_stats=perf_stats,
                journey_cache=journey_cache,
            )
        return _assign_layer_contexts(
            contexts, period_index=period_index, wait_extra=reliability_extra,
            perf_stats=perf_stats, journey_cache=journey_cache,
        )

    if wait_crowding_per_100_min > 0:
        pass_one = ctx.assign(
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=reliability_extra,
            period_index=period_index,
            perf_stats=perf_stats,
            journey_cache=journey_cache,
        )
        crowd_started = perf_counter() if perf_stats is not None else 0.0
        crowd_state = _build_crowd_state(
            ctx.route_sequences,
            pass_one.get("seg_forward_totals", {}),
            pass_one.get("seg_reverse_totals", {}),
            pass_one.get("seq_stop_totals", {}),
            ctx.seq_headway_min,
            ctx.vehicle_specs,
            period_hours,
        )
        if perf_stats is not None:
            perf_stats.setdefault("crowd_state_s", 0.0)
            perf_stats["crowd_state_s"] += perf_counter() - crowd_started
        return ctx.assign(
            out_factor=out_factor,
            ret_factor=ret_factor,
            wait_extra=reliability_extra,
            period_index=period_index,
            crowd_state=crowd_state,
            perf_stats=perf_stats,
            journey_cache=journey_cache,
        )