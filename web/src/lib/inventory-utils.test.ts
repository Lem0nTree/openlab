import { describe, expect, it } from "vitest";
import { captureMode, existingThingConfirmation, locationFromCode, movementSummary, stockMovementPayload } from "./inventory-utils";

const drawer = { id: "drawer-1", name: "Drawer A", parent_id: null, public_code: "code-a", revision: 1, thing_count: 2, total_quantity: "5" };

describe("inventory helpers", () => {
  it("accepts supported capture modes and rejects arbitrary query values", () => {
    expect(captureMode("photo")).toBe("photo");
    expect(captureMode("unknown")).toBe("text");
  });

  it("resolves drawer labels by public code", () => {
    expect(locationFromCode([drawer], "code-a")?.id).toBe("drawer-1");
    expect(locationFromCode([drawer], "missing")).toBeNull();
  });

  it("describes movement direction", () => {
    expect(movementSummary("move", "Drawer A", "Drawer B")).toBe("Drawer A → Drawer B");
  });

  it("only attaches an existing Thing to confirmation when selected", () => {
    expect(existingThingConfirmation("thing-1")).toEqual({ existing_thing_id: "thing-1" });
    expect(existingThingConfirmation("")).toEqual({});
  });

  it("builds shared receive, move, and use payloads with exact endpoints", () => {
    expect(stockMovementPayload("receive", "thing-1", 3, "", "drawer-b", "arrival")).toMatchObject({
      from_location_id: null, to_location_id: "drawer-b", note: "arrival",
    });
    expect(stockMovementPayload("move", "thing-1", 2, "drawer-a", "drawer-b", "")).toMatchObject({
      from_location_id: "drawer-a", to_location_id: "drawer-b", note: null,
    });
    expect(stockMovementPayload("consume", "thing-1", 1, "drawer-a", "", "used")).toMatchObject({
      from_location_id: "drawer-a", to_location_id: null, note: "used",
    });
  });
});
