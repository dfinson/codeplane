/** Null is missing currency evidence, never measured zero. */
export function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "Unknown USD";
  if (value === 0) return "$0.00";
  return `$${value.toFixed(value < 0.001 ? 6 : value < 0.01 ? 4 : 2)}`;
}

export function sumKnown(values: (number | null | undefined)[]): number | null {
  const known = values.filter((value): value is number => value != null && Number.isFinite(value));
  return known.length ? known.reduce((sum, value) => sum + value, 0) : null;
}
