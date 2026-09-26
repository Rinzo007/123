<script lang="ts">
  export let mapElement: HTMLDivElement;
  export let drawMode = false;
</script>
<div class="map-wrap">
  <div class="map" bind:this={mapElement}></div>
  {#if drawMode}<div class="map-hint">Кликайте по карте, чтобы добавлять остановки</div>{/if}
</div>
<style>
  .map-wrap,.map{width:100%;height:100%;min-height:640px}
  .map-hint{position:absolute;top:12px;left:12px;padding:8px 10px;background:rgba(17,24,39,.9);color:#fff;border-radius:7px;font-size:12px}
</style>