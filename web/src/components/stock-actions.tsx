"use client";

import { FormEvent, useMemo, useState } from "react";
import { api, idempotencyHeaders, type Balance, type Location, type Thing } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { stockMovementPayload } from "@/lib/inventory-utils";

type StockAction = "receive" | "move" | "consume" | "count";

export function StockActions({
  thing,
  locations,
  balances,
  defaultLocationId,
  onDone,
}: {
  thing: Thing;
  locations: Location[];
  balances: Balance[];
  defaultLocationId?: string;
  onDone: () => Promise<void> | void;
}) {
  const [action, setAction] = useState<StockAction>("receive");
  const [sourceId, setSourceId] = useState(defaultLocationId ?? "");
  const [destinationId, setDestinationId] = useState(defaultLocationId ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const thingBalances = useMemo(
    () => balances.filter((balance) => balance.thing_id === thing.id),
    [balances, thing.id],
  );
  const stockedLocationIds = new Set(thingBalances.map((balance) => balance.location_id));
  const sourceLocations = action === "count"
    ? locations
    : locations.filter((location) => stockedLocationIds.has(location.id));
  const activeLocationId = action === "receive" ? destinationId : sourceId;
  const activeBalance = thingBalances.find((balance) => balance.location_id === activeLocationId);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const quantity = Number(data.get("quantity"));
    const note = String(data.get("note") ?? "");
    try {
      if (action === "count") {
        if (!sourceId) throw new Error("Choose a drawer to count");
        await api("/inventory/adjust", {
          method: "POST",
          headers: idempotencyHeaders(),
          body: JSON.stringify({
            thing_id: thing.id,
            location_id: sourceId,
            counted_quantity: quantity,
            revision: activeBalance?.revision ?? 0,
            note,
          }),
        });
      } else {
        if (action !== "receive" && !sourceId) throw new Error("Choose a source drawer");
        if (action !== "consume" && !destinationId) throw new Error("Choose a destination drawer");
        await api(`/inventory/${action}`, {
          method: "POST",
          headers: idempotencyHeaders(),
          body: JSON.stringify(stockMovementPayload(action, thing.id, quantity, sourceId, destinationId, note)),
        });
      }
      form.reset();
      await onDone();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stock-action-form" onSubmit={(event) => void submit(event)}>
      <div className="stock-action-tabs" role="group" aria-label="Stock action">
        {(["receive", "move", "consume", "count"] as StockAction[]).map((value) => (
          <button type="button" key={value} className={action === value ? "active" : ""} onClick={() => setAction(value)}>
            {value === "consume" ? "Use" : value === "count" ? "Set count" : value[0].toUpperCase() + value.slice(1)}
          </button>
        ))}
      </div>
      <p className="stock-action-context"><strong>{thing.name}</strong>{activeBalance ? ` · ${formatQuantity(activeBalance.quantity)} currently in ${activeBalance.location_name}` : " · no recorded stock in this drawer"}</p>
      <div className="stock-action-fields">
        {action !== "receive" && (
          <label><span>{action === "count" ? "Drawer" : "From"}</span><select value={sourceId} onChange={(event) => setSourceId(event.target.value)} required><option value="">Choose drawer</option>{sourceLocations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label>
        )}
        {(action === "receive" || action === "move") && (
          <label><span>{action === "move" ? "To" : "Drawer"}</span><select value={destinationId} onChange={(event) => setDestinationId(event.target.value)} required><option value="">Choose drawer</option>{locations.filter((location) => action !== "move" || location.id !== sourceId).map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label>
        )}
        <label><span>{action === "count" ? "Counted quantity" : "Quantity"}</span><input key={`${action}:${activeLocationId}:${activeBalance?.revision ?? 0}`} name="quantity" type="number" min={action === "count" ? "0" : "0.000001"} step="any" defaultValue={action === "count" ? activeBalance?.quantity ?? "0" : "1"} required /></label>
        <label className="stock-note"><span>{action === "count" ? "Reason" : "Note (optional)"}</span><input name="note" placeholder={action === "count" ? "e.g. Physical drawer count" : "Optional context"} required={action === "count"} /></label>
        <button disabled={busy}>{busy ? "Saving…" : action === "count" ? "Save count" : `${action === "consume" ? "Use" : action[0].toUpperCase() + action.slice(1)} stock`}</button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
