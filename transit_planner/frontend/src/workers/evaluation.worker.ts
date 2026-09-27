const runtimeUrl = new URL("/assets/evaluation.worker-jRuvxHc_.js", self.location.href).href;
// Keep the exact reference evaluator as the numeric authority while exposing
// it through the local worker boundary used by the application.
await import(/* @vite-ignore */ runtimeUrl);
