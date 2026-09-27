export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface JobSnapshot<T = unknown> {
  id: string;
  type: string;
  status: JobStatus;
  progress: number;
  result?: T;
  error?: string;
}

type JobListener = (job: JobSnapshot) => void;

export class JobManager {
  private jobs = new Map<string, JobSnapshot>();
  private listeners = new Set<JobListener>();

  subscribe(listener: JobListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private publish(job: JobSnapshot): void {
    this.jobs.set(job.id, job);
    for (const listener of this.listeners) listener(job);
  }

  async run<T>(type: string, task: (report: (progress: number) => void) => Promise<T>): Promise<T> {
    const id = crypto.randomUUID();
    this.publish({ id, type, status: "queued", progress: 0 });
    this.publish({ id, type, status: "running", progress: 0 });
    try {
      const result = await task(progress => {
        this.publish({ id, type, status: "running", progress: Math.max(0, Math.min(1, progress)) });
      });
      this.publish({ id, type, status: "completed", progress: 1, result });
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.publish({ id, type, status: "failed", progress: 1, error: message });
      throw error;
    }
  }

  get(id: string): JobSnapshot | undefined { return this.jobs.get(id); }
}
