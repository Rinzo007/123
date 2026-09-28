/**
 * Thin worker wrapper around the assignment iteration loop.
 *
 * The loop lives in `assignment-runtime.ts` so it can be imported directly:
 * the assignment needs to re-route every OD pair on every iteration, and a
 * nested worker per pair would be untenable. Keeping the loop in a plain
 * module also makes it runnable in Node under test.
 */
import { assignDemand, type AssignmentWorkerInput } from "./assignment-runtime";

self.onmessage = (event: MessageEvent<AssignmentWorkerInput>) => {
  const input = event.data;
  try {
    self.postMessage(assignDemand(input));
  } catch (error) {
    self.postMessage({
      type: "error",
      job: input?.job ?? -1,
      message: error instanceof Error ? error.message : String(error),
    });
  }
};
