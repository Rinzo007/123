import type { StreetWorkerMessage } from "../street-graph";

type RouteMessage = Extract<StreetWorkerMessage, { type: "route" }>;
type CancelMessage = Extract<StreetWorkerMessage, { type: "cancel" }>;

class BinaryHeap {
  private nodes: number[] = [];
  private values: number[] = [];

  clear(): void {
    this.nodes.length = 0;
    this.values.length = 0;
  }

  get size(): number {
    return this.nodes.length;
  }

  push(node: number, value: number): void {
    this.nodes.push(node);
    this.values.push(value);
    let index = this.nodes.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (this.values[parent] <= this.values[index]) break;
      [this.values[parent], this.values[index]] = [this.values[index], this.values[parent]];
      [this.nodes[parent], this.nodes[index]] = [this.nodes[index], this.nodes[parent]];
      index = parent;
    }
  }

  pop(): [number, number] {
    const node = this.nodes[0];
    const value = this.values[0];
    const last = this.nodes.length - 1;
    this.nodes[0] = this.nodes[last];
    this.values[0] = this.values[last];
    this.nodes.pop();
    this.values.pop();
    let index = 0;
    while (true) {
      const left = index * 2 + 1;
      if (left >= this.nodes.length) break;
      const right = left + 1;
      let best = left;
      if (right < this.nodes.length && this.values[right] < this.values[left]) best = right;
      if (this.values[index] <= this.values[best]) break;
      [this.values[index], this.values[best]] = [this.values[best], this.values[index]];
      [this.nodes[index], this.nodes[best]] = [this.nodes[best], this.nodes[index]];
      index = best;
    }
    return [node, value];
  }
}

const heap = new BinaryHeap();
let cancelledBefore = -1;

function route(
  message: RouteMessage,
  start: number,
  end: number,
): { edges: Int32Array; nodes: Int32Array; costMin: number } {
  const { offsets, targets, edges, costMin } = message;
  const vertexCount = offsets.length - 1;
  const distance = new Float64Array(vertexCount).fill(Number.POSITIVE_INFINITY);
  const previous = new Int32Array(vertexCount).fill(-1);
  const settled = new Uint8Array(vertexCount);

  distance[start] = 0;
  heap.clear();
  heap.push(start, 0);

  while (heap.size > 0) {
    if (message.job <= cancelledBefore) break;
    const [node, current] = heap.pop();
    if (settled[node]) continue;
    if (current !== distance[node]) continue;
    settled[node] = 1;
    if (node === end) break;

    for (let arc = offsets[node]; arc < offsets[node + 1]; arc += 1) {
      const target = targets[arc];
      if (settled[target]) continue;
      const candidate = current + costMin[arc];
      if (candidate >= distance[target]) continue;
      distance[target] = candidate;
      previous[target] = node;
      heap.push(target, candidate);
    }
  }

  if (!Number.isFinite(distance[end])) {
    return { edges: new Int32Array(0), nodes: new Int32Array(0), costMin: Number.POSITIVE_INFINITY };
  }

  const nodeChain: number[] = [];
  let cursor = end;
  while (cursor !== -1) {
    nodeChain.push(cursor);
    cursor = previous[cursor];
  }
  nodeChain.reverse();

  const chain: number[] = [];
  for (let index = 0; index + 1 < nodeChain.length; index += 1) {
    const edge = findArcEdge(offsets, edges, targets, nodeChain[index], nodeChain[index + 1]);
    if (edge < 0) {
      return { edges: new Int32Array(0), nodes: new Int32Array(0), costMin: Number.POSITIVE_INFINITY };
    }
    chain.push(edge);
  }
  return {
    edges: Int32Array.from(chain),
    nodes: Int32Array.from(nodeChain),
    costMin: distance[end],
  };
}

function findArcEdge(
  offsets: Int32Array,
  edges: Int32Array,
  targets: Int32Array,
  from: number,
  to: number,
): number {
  for (let arc = offsets[from]; arc < offsets[from + 1]; arc += 1) {
    if (targets[arc] === to) return edges[arc];
  }
  return -1;
}

self.onmessage = (event: MessageEvent<RouteMessage | CancelMessage>) => {
  const message = event.data;
  if (message.type === "cancel") {
    cancelledBefore = message.before;
    return;
  }
  if (message.type !== "route") return;

  const results: Array<{ edges: Int32Array; nodes: Int32Array; costMin: number }> = [];
  for (let index = 0; index < message.requests.length; index += 2) {
    const start = message.requests[index];
    const end = message.requests[index + 1];
    if (start === end) {
      results.push({ edges: new Int32Array(0), nodes: Int32Array.of(start), costMin: 0 });
      continue;
    }
    const result = route(message, start, end);
    if (!Number.isFinite(result.costMin)) {
      self.postMessage(
        { type: "failed", job: message.job, detail: `No street path between nodes ${start} and ${end}` },
      );
      return;
    }
    results.push(result);
  }

  self.postMessage({ type: "routed", job: message.job, results });
};
