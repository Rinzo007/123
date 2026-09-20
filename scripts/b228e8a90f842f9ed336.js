() => {
  try {
    return performance.getEntriesByType("resource").filter((r) => r.initiatorType === "script").map((r) => r.name);
  } catch (e) { return []; }
}