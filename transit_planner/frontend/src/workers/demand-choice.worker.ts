export {};

export {};

type Batch = {
  counts: Uint8Array;
  costs: Float64Array;
  frequencies: Float64Array;
  rideBase: Float64Array;
  crowd: Float64Array;
  restWait: Float64Array;
  transfer: Float64Array;
};

function solve(batch: Batch) {
  const count = batch.counts.length;
  const activeCounts = new Uint8Array(count);
  const order = new Uint8Array(count * 4);
  const totals = new Float64Array(count * 6);

  const candidates = new Uint8Array(4);

  for (let row = 0; row < count; row += 1) {
    const active = batch.counts[row];
    if (!active) continue;
    const offset = row * 4;

    for (let i = 0; i < active; i += 1) candidates[i] = i;
    for (let i = 1; i < active; i += 1) {
      const candidate = candidates[i];
      const cost = batch.costs[offset + candidate];
      let j = i - 1;
      while (j >= 0 && batch.costs[offset + candidates[j]] > cost) {
        candidates[j + 1] = candidates[j];
        j -= 1;
      }
      candidates[j + 1] = candidate;
    }

    let frequency = 0;
    let weightedCost = 0;
    let bestMean = Number.POSITIVE_INFINITY;
    let selected = 0;

    for (let i = 0; i < active; i += 1) {
      const candidate = candidates[i];
      const nextFrequency = frequency + batch.frequencies[offset + candidate];
      const mean =
        (1 + weightedCost + batch.frequencies[offset + candidate] * batch.costs[offset + candidate]) /
        nextFrequency;
      if (selected > 0 && mean >= bestMean) break;
      frequency = nextFrequency;
      weightedCost += batch.frequencies[offset + candidate] * batch.costs[offset + candidate];
      bestMean = mean;
      selected += 1;
    }

    let ride = 0;
    let crowd = 0;
    let restWait = frequency > 0 ? 1 / frequency : 0;
    let transfer = 0;

    for (let i = 0; i < selected; i += 1) {
      const candidate = candidates[i];
      const share = batch.frequencies[offset + candidate] / frequency;
      order[offset + i] = candidate;
      ride += share * batch.rideBase[offset + candidate];
      crowd += share * batch.crowd[offset + candidate];
      restWait += batch.restWait[offset + candidate];
      transfer += share * batch.transfer[offset + candidate];
    }

    activeCounts[row] = selected;
    const totalsOffset = row * 6;
    totals[totalsOffset] = frequency;
    totals[totalsOffset + 1] = bestMean;
    totals[totalsOffset + 2] = ride;
    totals[totalsOffset + 3] = crowd;
    totals[totalsOffset + 4] = restWait;
    totals[totalsOffset + 5] = transfer;
  }

  return { activeCounts, order, totals };
}

self.onmessage = (event: MessageEvent<{ id: string; batch: Batch }>) => {
  const start = performance.now();
  const result = solve(event.data.batch);
  self.postMessage(
    { id: event.data.id, output: result, computeMs: performance.now() - start },
    { transfer: [result.activeCounts.buffer, result.order.buffer, result.totals.buffer] },
  );
};
