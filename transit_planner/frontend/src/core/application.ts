import type { NetworkPayload } from "../types";
import { ComputationCache } from "./cache";
import { JobManager } from "./jobs";
import { NetworkEditor } from "./network-editor";

export class TransitPlannerApplication {
  readonly jobs = new JobManager();
  readonly cache = new ComputationCache();
  readonly networkEditor: NetworkEditor;

  constructor(network: NetworkPayload) {
    this.networkEditor = new NetworkEditor(network);
  }

  get network(): NetworkPayload { return this.networkEditor.network; }

  invalidateNetworkComputations(): void { this.cache.clear(); }

  editNetwork<T>(operation: () => T): T {
    const result = operation();
    this.invalidateNetworkComputations();
    return result;
  }
}
