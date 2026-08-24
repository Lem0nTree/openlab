import type { Location } from "./api";

const captureModes = new Set(["text", "photo", "screenshot", "voice", "email", "pdf"]);

export function captureMode(value?: string): string {
  return value && captureModes.has(value) ? value : "text";
}

export function locationFromCode(locations: Location[], code?: string): Location | null {
  if (!code) return null;
  return locations.find((location) => location.public_code === code) ?? null;
}

export function existingThingConfirmation(existingThingId?: string): { existing_thing_id?: string } {
  return existingThingId ? { existing_thing_id: existingThingId } : {};
}

export function stockMovementPayload(
  action: "receive" | "move" | "consume",
  thingId: string,
  quantity: number,
  sourceId: string,
  destinationId: string,
  note: string,
) {
  return {
    thing_id: thingId,
    quantity,
    from_location_id: action === "move" || action === "consume" ? sourceId : null,
    to_location_id: action === "move" || action === "receive" ? destinationId : null,
    note: note || null,
  };
}

export function movementSummary(
  type: string,
  source: string | null,
  destination: string | null,
): string {
  if (type === "move") return `${source ?? "Unknown"} → ${destination ?? "Unknown"}`;
  if (type === "consume") return `Used from ${source ?? "Unknown"}`;
  if (type === "receive") return `Received into ${destination ?? "Unknown"}`;
  if (type === "adjust") return destination ? `Count increased in ${destination}` : `Count reduced in ${source ?? "Unknown"}`;
  return destination ?? source ?? type;
}
