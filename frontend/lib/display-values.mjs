// Never infer units from magnitude: 1 percent and a probability of 1 differ.
export function formatPercent(value, unit = "percent") {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "—";
  const percent = Number(value) * (unit === "fraction" ? 100 : 1);
  if (percent < 0 || percent > 100) return "—";
  return `${percent.toFixed(1)}%`;
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
