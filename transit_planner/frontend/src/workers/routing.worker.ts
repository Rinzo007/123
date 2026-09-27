type RouteRequest = {
  type: "route";
  job: number;
  start: number;
  targets: Int32Array;
  offsets: Int32Array;
  costs: Float64Array;
  edgeIds: string[];
};

class QuadHeap {
  private nodes = new Int32Array(32);
  private values = new Float64Array(32);
  private size = 0;

  clear(): void { this.size = 0; }

  push(node: number, value: number): void {
    if (this.size >= this.nodes.length) {
      const nodes = new Int32Array(this.nodes.length * 2); nodes.set(this.nodes); this.nodes = nodes;
      const values = new Float64Array(this.values.length * 2); values.set(this.values); this.values = values;
    }
    let i = this.size++;
    while (i > 0) {
      const p = Math.floor((i - 1) / 4);
      if (this.values[p] <= value) break;
      this.nodes[i] = this.nodes[p];
      this.values[i] = this.values[p];
      i = p;
    }
    this.nodes[i] = node;
    this.values[i] = value;
  }

  pop(): [number, number] | null {
    if (!this.size) return null;
    const node = this.nodes[0], value = this.values[0];
    const last = --this.size;
    if (last > 0) {
      const lastNode = this.nodes[last], lastValue = this.values[last];
      let i = 0;
      while (true) {
        const first = i * 4 + 1;
        if (first >= last) break;
        let best = first;
        const end = Math.min(last, first + 4);
        for (let c = first + 1; c < end; c += 1) if (this.values[c] < this.values[best]) best = c;
        if (this.values[best] >= lastValue) break;
        this.nodes[i] = this.nodes[best];
        this.values[i] = this.values[best];
        i = best;
      }
      this.nodes[i] = lastNode;
      this.values[i] = lastValue;
    }
    return [node, value];
  }
}

self.onmessage = (event: MessageEvent<RouteRequest>) => {
  const message = event.data;
  if (message.type !== "route") return;

  const nodeCount = message.offsets.length - 1;
  const dist = new Float64Array(nodeCount);
  dist.fill(Number.POSITIVE_INFINITY);
  const previousNode = new Int32Array(nodeCount); previousNode.fill(-1);
  const previousEdge = new Int32Array(nodeCount); previousEdge.fill(-1);
  const settled = new Uint8Array(nodeCount);
  const heap = new QuadHeap();
  heap.clear();
  dist[message.start] = 0;
  heap.push(message.start, 0);

  while (true) {
    const item = heap.pop();
    if (!item) break;
    const [node, current] = item;
    if (settled[node] || current !== dist[node]) continue;
    settled[node] = 1;

    for (let edge = message.offsets[node]; edge < message.offsets[node + 1]; edge += 1) {
      const target = message.targets[edge];
      const candidate = current + message.costs[edge];
      if (candidate >= dist[target]) continue;
      dist[target] = candidate;
      previousNode[target] = node;
      previousEdge[target] = edge;
      heap.push(target, candidate);
    }
  }

  const routes = message.edgeIds.map((_, index) => {
    const edgePath: number[] = [];
    let current = index;
    if (!Number.isFinite(dist[current])) return { target: index, cost: Number.POSITIVE_INFINITY, edgePath };
    while (current !== message.start) {
      const edge = previousEdge[current];
      if (edge < 0) break;
      edgePath.push(edge);
      current = previousNode[current];
    }
    edgePath.reverse();
    return {
      target: index,
      cost: dist[index],
      edgePath,
      edgeIds: edgePath.map((edge) => message.edgeIds[edge]),
    };
  });

  self.postMessage({ type: "routed", job: message.job, routes });
};
