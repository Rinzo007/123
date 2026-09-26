<script lang="ts">
  import type {StopDraft,TransitMode} from "../types";
  export let routeName="Новый маршрут"; export let mode:TransitMode="bus";
  export let modeLabels:Record<TransitMode,string>={bus:"Автобус",tram:"Трамвай",metro:"Метро",rail:"Железная дорога"}; export let previewTrips=1000;
  export let farePerTransitTrip=0; export let annualDays=365;
  export let periods:Array<{id:string;start_minute:number;end_minute:number}>=[];
  export let headways:Record<string,number>={}; export let stops:StopDraft[]=[];
  export let busy=false; export let message="Готово";
  export let showRoads=true; export let showRoadSpeed=false; export let showStops=true; export let showPlaces=true;
  export let showConnectors=false; export let showPopulation=false; export let showDemandStreets=true;
  export let showPassengerFlow=true; export let showStationLoads=true; export let drawMode=false;
  export let onPreview:()=>void=()=>{}; export let onEconomics:()=>void=()=>{}; export let onCityAssignment:()=>void=()=>{};
  export let onRemoveStop:(id:string)=>void=()=>{}; export let onClear:()=>void=()=>{}; export let onChange:()=>void=()=>{};
</script>
<aside class="sidebar">
 <section>
  <div class="section-title">Маршрут</div>
  <label>Название<input bind:value={routeName} on:input={onChange}/></label>
  <label>Вид транспорта<select bind:value={mode} on:change={onChange}>{#each Object.entries(modeLabels) as [value,label]}<option value={value}>{label}</option>{/each}</select></label>
  <div class="preview-demand"><div class="section-title">Проверочный расчёт</div>
   <label>Спрос, поездок/сутки<input type="number" min="1" max="100000" bind:value={previewTrips} on:input={onChange}/></label>
   <button class="primary" on:click={onPreview} disabled={busy||stops.length<2}>Рассчитать пассажиропоток</button>
   <button on:click={onEconomics} disabled={busy||stops.length<2}>Рассчитать экономику</button>
   <button on:click={onCityAssignment} disabled={busy||stops.length<2}>Рассчитать городскую сеть</button>
   <label>Тариф за поездку<input type="number" min="0" step="0.01" bind:value={farePerTransitTrip} on:input={onChange}/></label>
   <label>Дней в году<input type="number" min="1" max="366" bind:value={annualDays} on:input={onChange}/></label>
  </div>
  <div class="period-headways"><div class="section-title">Интервалы</div>
   {#each periods as period}<label>{period.id}<input type="number" min="1" max="120" step="1" bind:value={headways[period.id]} on:input={onChange}/></label>{/each}
  </div>
 </section>
 <section><div class="section-title">Остановки ({stops.length})</div>
  {#if stops.length===0}<div class="empty">Включите «Добавить остановки» и кликайте по карте.</div>
  {:else}{#each stops as stop,index}<div class="stop-row"><div><strong>{index+1}. {stop.name}</strong><small>{stop.lon.toFixed(5)}, {stop.lat.toFixed(5)}</small></div><button on:click={()=>onRemoveStop(stop.id)}>Удалить</button></div>{/each}<button on:click={onClear}>Очистить маршрут</button>{/if}
 </section>
 <section><div class="section-title">Слои</div>
  <label class="check"><input type="checkbox" bind:checked={showRoads}/> Дороги</label>
  <label class="check"><input type="checkbox" bind:checked={showRoadSpeed}/> Скорости дорог</label>
  <label class="check"><input type="checkbox" bind:checked={showStops}/> Остановки Overture</label>
  <label class="check"><input type="checkbox" bind:checked={showPlaces}/> Places Overture</label>
  <label class="check"><input type="checkbox" bind:checked={showConnectors}/> Connectors</label>
  <label class="check"><input type="checkbox" bind:checked={showPopulation}/> WorldPop</label>
  <label class="check"><input type="checkbox" bind:checked={showDemandStreets}/> Demand streets</label>
  <label class="check"><input type="checkbox" bind:checked={showPassengerFlow}/> Пассажиропоток</label>
  <label class="check"><input type="checkbox" bind:checked={showStationLoads}/> Нагрузка остановок</label>
 </section>
 <div class="status" class:busy>{message}</div>
</aside>