<script lang="ts">
  export let cityMeta:Record<string, unknown>|null=null;
  export let assignment:any=null; export let economics:any=null; export let scenario:any=null; export let timetable:any=null;
  export let periodsData:Array<any>=[];
  const formatNumber=(v:unknown)=> typeof v==="number" ? v.toLocaleString("ru-RU",{maximumFractionDigits:1}) : String(v);
</script>

{#if cityMeta}
<section class="panel"><h3>Городской расчёт</h3>
  <div class="grid">{#each Object.entries(cityMeta) as [key,value]}<div><span>{key}</span><b>{formatNumber(value)}</b></div>{/each}</div>
</section>
{/if}

{#if assignment}
<section class="panel"><h3>Пассажиропоток</h3>
  <div class="grid">
    <div>Общий спрос <b>{formatNumber(assignment.metrics.total_trips)}</b></div>
    <div>Общественный транспорт <b>{formatNumber(assignment.metrics.transit_trips)}</b></div>
    <div>Автомобиль <b>{formatNumber(assignment.metrics.car_trips)}</b></div>
    <div>Пешком / велосипед <b>{formatNumber(assignment.metrics.walk_trips + assignment.metrics.bike_trips)}</b></div>
  </div>
</section>
{/if}

{#if periodsData.length}
<section class="panel"><h3>Линия × период</h3>
  {#each periodsData as period}
    <div class="period-card">
      <strong>{period.period_id}</strong>
      <span>спрос {formatNumber(period.demand_trips)}</span>
      <span>общественный транспорт {(period.transit_share*100).toFixed(1)}%</span>
      <span>максимальная загрузка {(period.max_load_ratio*100).toFixed(1)}%</span>
      <span>эксплуатация {formatNumber(period.economics.daily_operating_cost)}</span>
      {#each period.services as service}
        <span>{service.route_id}: {formatNumber(service.riders)} пасс. · PLF {(service.peak_load_factor*100).toFixed(1)}% · парк {service.fleet}</span>
      {/each}
    </div>
  {/each}
</section>
{/if}

{#if economics}
<section class="panel"><h3>Экономика</h3>
  <div class="grid">
    <div>Транспортная работа <b>{formatNumber(economics.economics.daily_vehicle_km)} км/сутки</b></div>
    <div>Эксплуатация <b>{formatNumber(economics.economics.daily_operating_cost)}</b></div>
    <div>Парк <b>{formatNumber(economics.economics.daily_fleet_cost)}</b></div>
    <div>Выручка <b>{formatNumber(economics.economics.daily_fare_revenue)}</b></div>
  </div>
</section>
{/if}

{#if scenario}
<section class="panel"><h3>Сравнение сценариев</h3>
  <p>Участков: {scenario.comparison.sections.length} · линий-периодов: {scenario.comparison.services.length}</p>
</section>
{/if}

{#if timetable}
<section class="panel"><h3>Расписание {timetable.service_id}</h3>
  {#each timetable.periods as period}<div class="row"><strong>{period.period_id}</strong><span>{period.departures_minute.length} отправлений</span></div>{/each}
</section>
{/if}

<style>
.panel{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px}
.grid>div{padding:10px;background:#f9fafb;border-radius:7px}
.grid span{display:block;color:#6b7280;font-size:11px}
.grid b{display:block;margin-top:4px}
.period-card{display:flex;flex-wrap:wrap;gap:8px;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:12px}
.period-card span{color:#4b5563}
.row{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid #eee}
</style>