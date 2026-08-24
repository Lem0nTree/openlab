export function formatQuantity(quantity: number | string): string {
  const numericQuantity = Number(quantity);
  return Number.isFinite(numericQuantity) ? String(numericQuantity) : String(quantity);
}
