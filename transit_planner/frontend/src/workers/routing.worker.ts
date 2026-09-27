type RouteRequest = {
  type: "route";
  job: number;
  start: number;
  targetNodes: Int32Array;
  offsets: Int32Array;
  targets: Int32Array;
  costs: Float64Array;
};

class QuadHeap {
  private nodes = new Int32Array(32);
  private values = new Float64Array(32);
  private size = 0;

  push(node: number, value: number): void {
    if (this.size >= this.nodes.length) {
      const nextNodes = new Int32Array(this.nodes.length * 2);
      nextNodes.set(this.nodes);
      this.nodes = nextNodes;
      const nextValues = new Float64Array(this.values.length * 2);
      nextValues.set(this.values);
      this.values = nextValues;
    }
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
    if (last > 0) {
      const lastNode = this.nodes[last];
      const lastValue = this.values[last];
      let index = 0;
      while (true) {
        const firstChild = index * 4 + 1;
        if (firstChild >= last) break;
        let best = firstChild;
        const end = Math.min(last, firstChild + 4);
        for (let child = firstChild + 1; child < end; child += 1) {
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
}

self.onmessage = (event: MessageEvent<RouteRequest>) => {
  const message = event.data;
  if (message.type !== "route") return;

  const nodeCount = message.offsets.length - 1;
  const distances = new Float64Array(message.targetNodes.length);
  const previousNode = new Int32Array(nodeCount);
  const previousEdge = new Int32Array(nodeCount);
  const targetIndex = new Int32Array(message.targetNodes.length);
  previousNode.fill(-1);
  previousEdge.fill(-1);

  const dist = new Float64Array(nodeCount);
  dist.fill(Number.POSITIVE_INFINITY);
  const settled = new Uint8Array(nodeCount);
  const heap = new QuadHeap();

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

  for (let index = 0; index < message.targetNodes.length; index += 1) {
    const target = message.targetNodes[index];
    targetIndex[index] = target;
    distances[index] = dist[target];
  }

  self.postMessage(
    {
      type: "routed",
      job: message.job,
      targetIndex,
      distances,
      previousNode,
      previousEdge,
    },
    { transfer: [targetIndex.buffer, distances.buffer, previousNode.buffer, previousEdge.buffer] },
  );
};
