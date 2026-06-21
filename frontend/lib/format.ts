// Indian-style number / currency formatting (lakh-crore grouping).

const inrFmt = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const numFmt = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function inr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return inrFmt.format(value);
}

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return numFmt.format(value);
}

// Compact ₹ for big headline figures: ₹1.23L / ₹4.56Cr
export function inrCompact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e7) return `₹${(value / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `₹${(value / 1e5).toFixed(2)}L`;
  return inr(value);
}
