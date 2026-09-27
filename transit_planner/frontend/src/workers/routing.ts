export interface DirectedEdgeInput {
  from: number;
  to: number;
  cost: number;
}

export interface PackedGraph {
  nodeCount: number;
  offsets: Int32Array;
  targets: Int32Array;
  costs: Float64Array;
}

export function packGraph(nodeCount: number, edges: readonly DirectedEdgeInput[]): PackedGraph {
  if (nodeCount < 0) throw new Error("nodeCount must be non-negative");
  const counts = new Int32Array(nodeCount);
  for (const edge of edges) {
    if (edge.from < 0 || edge.from >= nodeCount || edge.to < 0 || edge.to >= nodeCount) {
      throw new Error("Graph edge endpoint is outside node range");
    }
    if (!Number.isFinite(edge.cost) || edge.cost < 0) throw new Error("Graph edge cost must be finite");
    counts[edge.from] += 1;
  }

  const offsets = new Int32Array(nodeCount + 1);
  for (let node = 0; node < nodeCount; node += 1) offsets[node + 1] = offsets[node] + counts[node];
  const cursor = offsets.slice(0, nodeCount);
  const targets = new Int32Array(edges.length);
  const costs = new Float64Array(edges.length);
  for (const edge of edges) {
    const position = cursor[edge.from]++;
    targets[position] = edge.to;
    costs[position] = edge.cost;
  }
  return { nodeCount, offsets, targets, costs };
}

export interface RouteSolution {
  target: number;
  cost: number;
  edgeIndices: Int32Array;
}

let requestId = 0;

export function routeGraph(
  graph: PackedGraph,
  start: number,
  targetNodes: Int32Array,
): Promise<RouteSolution[]> {
  if (start < 0 || start >= graph.nodeCount) {
    return Promise.reject(new Error("Route start is outside graph"));
  }
  const worker = new Worker(new URL("./routing.worker.ts", import.meta.url), { type: "module" });
  const job = ++requestId;

  return new Promise((resolve, reject) => {
    worker.onmessage = (event: MessageEvent) => {
      const data = event.data as {
        type?: string;
        job?: number;
        targetIndex?: Int32Array;
        distances?: Float64Array;
        previousNode?: Int32Array;
        previousEdge?: Int32Array;
      };
      if (data.type !== "routed" || data.job !== job) return;

      const targetIndex = data.targetIndex!;
      const distances = data.distances!;
      const previousNode = data.previousNode!;
      const previousEdge = data.previousEdge!;
      const solutions: RouteSolution[] = new Array(targetIndex.length);

      for (let i = 0; i < targetIndex.length; i += 1) {
        const target = targetIndex[i];
        if (!Number.isFinite(distances[i])) {
          solutions[i] = { target, cost: Number.POSITIVE_INFINITY, edgeIndices: new Int32Array(0) };
          continue;
        }
        const path: number[] = [];
        let node = target;
        while (node !== start) {
          const edge = previousEdge[node];
          if (edge < 0) {
            path.length = 0;
            break;
          }
          path.push(edge);
          node = previousNode[node];
        }
        path.reverse();
        solutions[i] = { target, cost: distances[i], edgeIndices: Int32Array.from(path) };
      }

      worker.terminate();
      resolve(solutions);
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "routing worker failed"));
    };
    worker.postMessage(
      {
        type: "route",
        job,
        start,
        targetNodes,
        offsets: graph.offsets,
        targets: graph.targets,
        costs: graph.costs,
      },
      { transfer: [targetNodes.buffer] },
    );
  });
}
