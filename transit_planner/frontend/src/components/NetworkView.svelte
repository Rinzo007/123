<script lang="ts">
  export let summary={lines:0,stops:0,dailyDepartures:0};
  export let routeRows:Array<{route:{name:string;mode:string;stop_ids:string[]};vehicle?:{capacity?:number};service?:{headway_by_period:Record<string,number>}}>=[]; 
  export let periods:Array<{id:string}>=[];
  export let modeLabels:Record<string,string>={};
  export let onGenerateTimetable:()=>void=()=>{};
</script>
<div class="network-view">
  <div class="network-header">
    <div>
      <h2>Сеть</h2><p>Линии, интервалы и остановки</p>
      <button on:click={onGenerateTimetable}>Сформировать расписание</button>
    </div>
    <div class="network-kpis">
      <div><span>Линий</span><b>{summary.lines}</b></div>
      <div><span>Отправлений/сутки</span><b>{summary.dailyDepartures}</b></div>
      <div><span>Остановок</span><b>{summary.stops}</b></div>
    </div>
  </div>
  {#if routeRows.length===0}
    <div class="network-empty">Добавьте минимум две остановки.</div>
  {:else}
    <div class="table-wrap"><table><thead><tr><th>Линия</th><th>Вид транспорта</th><th>Остановки</th><th>Вместимость</th>{#each periods as period}<th>{period.id}</th>{/each}</tr></thead><tbody>
      {#each routeRows as row}
        <tr><td><strong>{row.route.name}</strong></td><td>{modeLabels[row.route.mode] ?? row.route.mode}</td><td>{row.route.stop_ids.length}</td><td>{row.vehicle?.capacity??"—"}</td>{#each periods as period}<td>{row.service?.headway_by_period[period.id]??"—"}</td>{/each}</tr>
      {/each}
    </tbody></table></div>
  {/if}
</div>
<style>
.network-view{height:100%;overflow:auto;padding:20px;box-sizing:border-box}
.network-header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:16px}
.network-header h2{margin:0 0 4px}.network-header p{color:#6b7280;margin:0}
.network-kpis{display:grid;grid-template-columns:repeat(3,minmax(110px,1fr));gap:8px}
.network-kpis>div{background:#fff;border:1px solid #e5e7eb;padding:10px;border-radius:8px}
.network-kpis span{display:block;color:#6b7280;font-size:11px}.network-kpis b{display:block;margin-top:4px}
.network-empty,.table-wrap{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:12px}
.table-wrap{overflow:auto;padding:0}table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid #f0f0f0;white-space:nowrap}th{background:#f9fafb}
</style>