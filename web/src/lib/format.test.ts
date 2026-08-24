import { describe, expect, it } from "vitest";
import { formatQuantity } from "./format";

describe("formatQuantity", () => {
  it("removes database padding without rounding fractional quantities", () => {
    expect(formatQuantity("1.000000")).toBe("1");
    expect(formatQuantity("1.600000")).toBe("1.6");
  });
});
