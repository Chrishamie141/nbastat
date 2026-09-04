export const ownerTerms = {
  scheduleLoaded: "Schedule",
  gameIdentitiesValid: "Games",
  databaseHealthy: "Database",
  week1SlateAvailable: "Current Slate",
  currentSlateAvailable: "Current Slate",
  predictionsComplete: "Predictions",
  artifactsReadable: "Predictions",
  gameDetailOperational: "Game Pages",
  statusRefreshOperational: "Live Updates",
  actualIngestionOperational: "Final Stats",
  staleReconciliationOperational: "Game Status Sync",
  searchOperational: "Search",
  criticalOwnerErrors: "Critical Issues",
};

export const ownerStatus = (value) => {
  const status = String(value || "UNKNOWN").toUpperCase();
  if (["PASS", "HEALTHY", "READY", "CURRENT", "SUCCEEDED"].includes(status)) return "READY";
  if (["WARN", "WARNING", "STALE", "PENDING", "DEGRADED"].includes(status)) return "WARNING";
  return "UNAVAILABLE";
};

