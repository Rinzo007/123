export {};

export {};

type SolveMessage = {
  type: "solve";
  job: number;
  start: number;
  end: number;
  stops: number;
  offsets: Int32Array;
  targets: Int32Array;
  costs: Float64Array;
};

let cancelledBefore = -1;

class QuadHeap {
  private nodes: Int32Array;
  private values: Float64Array;
  private size = 0;

  constructor(capacity: number) {
    const initial = Math.max(32, capacity);
    this.nodes = new Int32Array(initial);
    this.values = new Float64Array(initial);
  }

  clear(): void {
    this.size = 0;
  }

  push(node: number, value: number): void {
    if (this.size >= this.nodes.length) this.grow();
    let index = this.size++;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 4);
      if (this.values[parent] <= value) break;
      this.nodes[index] = this.nodes[parent];
      this.values[index] = this.values[parent];
      index = parent;
    }
    this.nodes[index] = node;
    this.values[index] = value;
  }

  pop(): [number, number] | null {
    if (!this.size) return null;
    const node = this.nodes[0];
    const value = this.values[0];
    const last = --this.size;
    if (last < 0) return null;
    if (last > 0) {
      const lastNode = this.nodes[last];
      const lastValue = this.values[last];
      let index = 0;
      while (true) {
        const firstChild = index * 4 + 1;
        if (firstChild >= last) break;
        let best = firstChild;
        const lastChild = Math.min(last, firstChild + 4);
        for (let child = firstChild + 1; child < lastChild; child += 1) {
          if (this.values[child] < this.values[best]) best = child;
        }
        if (this.values[best] >= lastValue) break;
        this.nodes[index] = this.nodes[best];
        this.values[index] = this.values[best];
        index = best;
      }
      this.nodes[index] = lastNode;
      this.values[index] = lastValue;
    }
    return [node, value];
  }

  private grow(): void {
    const next = new Int32Array(this.nodes.length * 2);
    next.set(this.nodes);
    this.nodes = next;
    const nextValues = new Float64Array(this.values.length * 2);
    nextValues.set(this.values);
    this.values = nextValues;
  }
}

function solve(message: SolveMessage) {
  const { job, start, end, stops, offsets, targets, costs } = message;
  const rowCount = Math.max(0, end - start);
  const times = new Float64Array(rowCount * stops);
  times.fill(Number.POSITIVE_INFINITY);
  const previous = new Int32Array(rowCount * stops * 2);
  previous.fill(-1);

  const dist = new Float64Array(stops);
  const settled = new Uint8Array(stops);
  const heap = new QuadHeap(Math.max(32, targets.length + 1));

  for (let origin = start; origin < end; origin += 1) {
    if (job <= cancelledBefore) break;
    dist.fill(Number.POSITIVE_INFINITY);
    settled.fill(0);
    heap.clear();
    dist[origin] = 0;
    heap.push(origin, 0);

    while (true) {
      if (job <= cancelledBefore) break;
      const item = heap.pop();
      if (!item) break;
      const [node, current] = item;
      if (settled[node]) continue;
      if (current !== dist[node]) continue;
      settled[node] = 1;

      const row = (origin - start) * stops;
      times[row + node] = current;

      for (let edgeIndex = offsets[node]; edgeIndex < offsets[node + 1]; edgeIndex += 1) {
        const target = targets[edgeIndex];
        const candidate = current + costs[edgeIndex];
        if (candidate >= dist[target]) continue;
        dist[target] = candidate;
        const previousBase = ((origin - start) * stops + target) * 2;
        previous[previousBase] = node;
        previous[previousBase + 1] = edgeIndex;
        heap.push(target, candidate);
      }
    }
  }

  self.postMessage(
    { type: "solved", job, start, end, stops, times, previous },
    { transfer: [times.buffer, previous.buffer] },
  );
}

self.onmessage = (event: MessageEvent<SolveMessage | { type: "cancel"; before: number }>) => {
  const message = event.data;
  if (message.type === "cancel") {
    cancelledBefore = message.before;
    return;
  }
  if (message.type === "solve") solve(message);
};
