"""The product MCP: typed, lab-scoped tools over the existing OpenLab domain model.

This module deliberately does not proxy arbitrary REST paths.  Tool contracts are
small, bounded task intents and every request is resolved to the OAuth grant that
made it, which keeps the installer MCP and product data MCP separate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import AnyHttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .mcp_auth import actor_for_access_token, canonical_mcp_url
from .models import (
    AuditEvent,
    InboxCandidate,
    InboxItem,
    Job,
    Location,
    McpActionReceipt,
    McpGrant,
    McpIdempotencyResult,
    Project,
    Requirement,
    StockBalance,
    StockMovement,
    Thing,
    User,
)
from .services import (
    adjust_inventory,
    apply_movement,
    audit,
    compatible_things,
    create_thing,
    get_lab_thing,
    lab_for_user,
)

MAX_PAGE = 100
DEFAULT_PAGE = 25


class OpenLabTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        db = SessionLocal()
        try:
            actor = actor_for_access_token(db, token)
            if not actor:
                return None
            user, grant = actor
            return AccessToken(
                token=token,
                client_id=grant.client_id,
                scopes=list(grant.scopes),
                expires_at=int(grant.access_expires_at.timestamp()) if grant.access_expires_at else None,
                subject=user.id,
                claims={"grant_id": grant.id, "lab_id": grant.lab_id},
            )
        finally:
            db.close()


def _annotations(*, read_only: bool, destructive: bool = False, idempotent: bool = True, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


def _page(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE))


def _actor(db: Session, scope: str = "openlab:read") -> tuple[User, McpGrant]:
    access = get_access_token()
    if not access or not access.subject or not access.claims:
        raise ToolError("Authentication required")
    grant_id = str(access.claims.get("grant_id", ""))
    grant = db.get(McpGrant, grant_id)
    user = db.get(User, access.subject)
    if not grant or not user or grant.revoked_at or scope not in grant.scopes:
        raise ToolError(f"MCP scope required: {scope}")
    return user, grant


def _result(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, default=str, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ToolError("OUTPUT_LIMIT: result exceeded 64 KiB")
    return value


def _idempotent(db: Session, grant: McpGrant, action: str, request_id: str) -> dict[str, Any] | None:
    if not request_id or len(request_id) > 128:
        raise ToolError("request_id is required and must be at most 128 characters")
    existing = db.scalar(select(McpIdempotencyResult).where(McpIdempotencyResult.grant_id == grant.id, McpIdempotencyResult.request_id == request_id))
    if existing:
        if existing.action != action:
            raise ToolError("request_id was already used for another action")
        return dict(existing.result)
    return None


def _save_idempotent(db: Session, grant: McpGrant, action: str, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    db.add(McpIdempotencyResult(grant_id=grant.id, request_id=request_id, action=action, result=result))
    return result


def _receipt(db: Session, grant: McpGrant, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    digest = sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    item = McpActionReceipt(
        grant_id=grant.id, action=action, payload=payload, payload_hash=digest,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(item)
    db.commit()
    return {"receipt": item.id, "expires_at": item.expires_at.isoformat(), "action": action, "payload_hash": digest}


def _consume_receipt(db: Session, grant: McpGrant, receipt: str, action: str) -> McpActionReceipt:
    item = db.scalar(select(McpActionReceipt).where(McpActionReceipt.id == receipt, McpActionReceipt.grant_id == grant.id).with_for_update())
    if not item or item.action != action or item.expires_at <= datetime.now(UTC):
        raise ToolError("CONFIRMATION_REQUIRED: receipt is invalid or expired")
    if item.consumed_at:
        if item.result is not None:
            return item
        raise ToolError("receipt has already been consumed")
    return item


def create_product_mcp() -> MCPServer[object]:
    settings = get_settings()
    public_base = (settings.public_url or "http://localhost:3000").rstrip("/")
    server = MCPServer(
        "openlab",
        title="OpenLab",
        description="Query and operate the authenticated OpenLab lab workspace.",
        instructions="Use evidence returned by the tools. Do not treat queued work or generated proposals as accepted physical truth.",
        version=settings.version,
        token_verifier=OpenLabTokenVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(public_base),
            resource_server_url=AnyHttpUrl(f"{public_base}/mcp"),
            required_scopes=["openlab:read"],
        ),
    )

    @server.tool(description="Retrieve a redacted overview of the current lab and MCP readiness.", annotations=_annotations(read_only=True))
    def get_lab_overview() -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            lab_id = lab_for_user(db, user)
            counts = {
                "things": db.scalar(select(func.count(Thing.id)).where(Thing.lab_id == lab_id, Thing.archived_at.is_(None))) or 0,
                "locations": db.scalar(select(func.count(Location.id)).where(Location.lab_id == lab_id, Location.archived_at.is_(None))) or 0,
                "projects": db.scalar(select(func.count(Project.id)).where(Project.lab_id == lab_id)) or 0,
                "inbox": db.scalar(select(func.count(InboxItem.id)).where(InboxItem.lab_id == lab_id)) or 0,
            }
            return _result({"lab_id": lab_id, "counts": counts, "mcp": {"enabled": True, "direct_http_url": canonical_mcp_url(settings.public_url)}})
        finally:
            db.close()

    @server.tool(description="Search local OpenLab inventory and knowledge without calling an AI provider.", annotations=_annotations(read_only=True))
    def search_openlab(query: str, limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        from .intelligence import search_inventory
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            values = search_inventory(db, lab_for_user(db, user), query, _page(limit), allow_semantic=False)
            return _result({"query": query, "results": values, "retrieval": "local"})
        finally:
            db.close()

    @server.tool(description="List inventory items in the current lab with bounded filtering.", annotations=_annotations(read_only=True))
    def list_inventory(query: str | None = None, limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            statement = select(Thing).where(Thing.lab_id == lab_for_user(db, user), Thing.archived_at.is_(None)).order_by(Thing.name).limit(_page(limit))
            if query:
                term = f"%{query}%"
                statement = statement.where(Thing.name.ilike(term) | Thing.mpn.ilike(term))
            values = [{"id": item.id, "name": item.name, "category": item.category, "manufacturer": item.manufacturer, "mpn": item.mpn, "revision": item.revision, "resource_uri": f"openlab://things/{item.id}"} for item in db.scalars(statement).all()]
            return _result({"items": values, "limit": _page(limit)})
        finally:
            db.close()

    @server.tool(description="Retrieve an inventory item with its current stock by location.", annotations=_annotations(read_only=True))
    def get_inventory_item(thing_id: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            lab_id = lab_for_user(db, user)
            item = db.scalar(select(Thing).where(Thing.id == thing_id, Thing.lab_id == lab_id, Thing.archived_at.is_(None)))
            if not item:
                raise ToolError("Thing not found")
            stock = db.execute(select(StockBalance, Location).join(Location).where(StockBalance.thing_id == item.id, StockBalance.quantity > 0)).all()
            return _result({"id": item.id, "name": item.name, "category": item.category, "manufacturer": item.manufacturer, "mpn": item.mpn, "metadata": item.metadata_json, "revision": item.revision, "stock": [{"location_id": loc.id, "location_name": loc.name, "quantity": balance.quantity} for balance, loc in stock], "resource_uri": f"openlab://things/{item.id}"})
        finally:
            db.close()

    @server.tool(description="List physical locations in the current lab.", annotations=_annotations(read_only=True))
    def list_locations(limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            rows = db.scalars(select(Location).where(Location.lab_id == lab_for_user(db, user), Location.archived_at.is_(None)).order_by(Location.name).limit(_page(limit))).all()
            return _result({"locations": [{"id": x.id, "name": x.name, "parent_id": x.parent_id, "revision": x.revision, "resource_uri": f"openlab://locations/{x.id}"} for x in rows]})
        finally:
            db.close()

    @server.tool(description="List recent stock movements with optional item or location filters.", annotations=_annotations(read_only=True))
    def list_stock_movements(thing_id: str | None = None, location_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            statement = select(StockMovement).where(StockMovement.lab_id == lab_for_user(db, user)).order_by(StockMovement.created_at.desc()).limit(_page(limit))
            if thing_id:
                statement = statement.where(StockMovement.thing_id == thing_id)
            if location_id:
                statement = statement.where((StockMovement.from_location_id == location_id) | (StockMovement.to_location_id == location_id))
            rows = db.scalars(statement).all()
            return _result({"movements": [{"id": x.id, "thing_id": x.thing_id, "from_location_id": x.from_location_id, "to_location_id": x.to_location_id, "quantity": x.quantity, "type": x.movement_type, "created_at": x.created_at} for x in rows]})
        finally:
            db.close()

    @server.tool(description="List inbox review items without returning attachment bytes.", annotations=_annotations(read_only=True))
    def list_inbox(status: str | None = None, limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            statement = select(InboxItem).where(InboxItem.lab_id == lab_for_user(db, user)).order_by(InboxItem.created_at.desc()).limit(_page(limit))
            if status:
                statement = statement.where(InboxItem.status == status)
            rows = db.scalars(statement).all()
            return _result({"items": [{"id": x.id, "input_type": x.input_type, "status": x.status, "text": x.text, "error": x.error, "created_at": x.created_at, "resource_uri": f"openlab://inbox/{x.id}"} for x in rows]})
        finally:
            db.close()

    @server.tool(description="Retrieve a review item and its candidates without attachment bytes.", annotations=_annotations(read_only=True))
    def get_inbox_item(inbox_id: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            item = db.scalar(select(InboxItem).where(InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)))
            if not item:
                raise ToolError("Inbox item not found")
            candidates = db.scalars(select(InboxCandidate).where(InboxCandidate.inbox_item_id == item.id)).all()
            return _result({"id": item.id, "status": item.status, "text": item.text, "evidence": item.processing_evidence, "candidates": [{"id": x.id, "name": x.name, "quantity": x.quantity, "category": x.category, "confidence": x.identity_confidence, "status": x.status, "thing_id": x.thing_id} for x in candidates]})
        finally:
            db.close()

    @server.tool(description="List projects in the current lab.", annotations=_annotations(read_only=True))
    def list_projects(status: str | None = None, limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            statement = select(Project).where(Project.lab_id == lab_for_user(db, user)).order_by(Project.name).limit(_page(limit))
            if status:
                statement = statement.where(Project.status == status)
            rows = db.scalars(statement).all()
            return _result({"projects": [{"id": x.id, "name": x.name, "description": x.description, "status": x.status, "revision": x.revision, "resource_uri": f"openlab://projects/{x.id}"} for x in rows]})
        finally:
            db.close()

    @server.tool(description="Retrieve one project, requirements, and allocations.", annotations=_annotations(read_only=True))
    def get_project(project_id: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            project = db.scalar(select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user)))
            if not project:
                raise ToolError("Project not found")
            requirements = db.scalars(select(Requirement).where(Requirement.project_id == project.id)).all()
            return _result({"id": project.id, "name": project.name, "description": project.description, "status": project.status, "revision": project.revision, "requirements": [{"id": x.id, "name": x.name, "quantity": x.quantity, "priority": x.priority, "selected_thing_id": x.selected_thing_id, "match_status": x.match_status} for x in requirements], "design": project.design_json, "resource_uri": f"openlab://projects/{project.id}"})
        finally:
            db.close()

    @server.tool(description="List asynchronous OpenLab jobs in the current lab.", annotations=_annotations(read_only=True))
    def list_jobs(kind: str | None = None, limit: int = DEFAULT_PAGE) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            statement = select(Job).where(Job.lab_id == lab_for_user(db, user)).order_by(Job.created_at.desc()).limit(_page(limit))
            if kind:
                statement = statement.where(Job.kind == kind)
            rows = db.scalars(statement).all()
            return _result({"jobs": [{"id": x.id, "kind": x.kind, "status": x.status, "result": x.result, "last_error": x.last_error, "resource_uri": f"openlab://jobs/{x.id}"} for x in rows]})
        finally:
            db.close()

    @server.tool(description="Check candidate inventory compatibility against required capabilities and interfaces.", annotations=_annotations(read_only=True))
    def check_compatibility(required_capabilities: list[str] | None = None, required_interfaces: list[str] | None = None, minimum_facts: dict[str, float] | None = None) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            values = compatible_things(db, user, required_capabilities or [], required_interfaces or [], {key: Decimal(str(value)) for key, value in (minimum_facts or {}).items()})
            return _result({"results": values})
        finally:
            db.close()

    @server.tool(description="List recent OpenLab audit activity available to this lab.", annotations=_annotations(read_only=True))
    def get_mcp_activity(limit: int = 20) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, _ = _actor(db)
            rows = db.scalars(select(AuditEvent).where(AuditEvent.lab_id == lab_for_user(db, user)).order_by(AuditEvent.created_at.desc()).limit(_page(limit))).all()
            return _result({"events": [{"id": x.id, "action": x.action, "entity_type": x.entity_type, "entity_id": x.entity_id, "details": x.details, "created_at": x.created_at} for x in rows]})
        finally:
            db.close()

    @server.tool(description="Create a zero-stock inventory item as an idempotent draft.", annotations=_annotations(read_only=False, idempotent=True))
    def create_inventory_item(name: str, category: str = "uncategorized", manufacturer: str | None = None, mpn: str | None = None, request_id: str = "") -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:write")
            previous = _idempotent(db, grant, "create_inventory_item", request_id)
            if previous:
                return previous
            item = create_thing(db, user, name=name, category=category, manufacturer=manufacturer, mpn=mpn, metadata={}, aliases=[])
            audit(db, user, "mcp.thing_created", "thing", item.id, client_id=grant.client_id)
            result = _save_idempotent(db, grant, "create_inventory_item", request_id, {"id": item.id, "name": item.name, "resource_uri": f"openlab://things/{item.id}"})
            db.commit()
            return result
        finally:
            db.close()

    @server.tool(description="Create a physical location as an idempotent draft.", annotations=_annotations(read_only=False, idempotent=True))
    def create_location(name: str, request_id: str = "") -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:write")
            previous = _idempotent(db, grant, "create_location", request_id)
            if previous:
                return previous
            item = Location(lab_id=lab_for_user(db, user), name=name)
            db.add(item)
            db.flush()
            audit(db, user, "mcp.location_created", "location", item.id, client_id=grant.client_id)
            result = _save_idempotent(db, grant, "create_location", request_id, {"id": item.id, "name": item.name, "resource_uri": f"openlab://locations/{item.id}"})
            db.commit()
            return result
        finally:
            db.close()

    @server.tool(description="Capture a text inbox draft without processing or receiving stock.", annotations=_annotations(read_only=False, idempotent=True))
    def capture_inbox(text: str, input_type: str = "text", request_id: str = "") -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:write")
            previous = _idempotent(db, grant, "capture_inbox", request_id)
            if previous:
                return previous
            item = InboxItem(lab_id=lab_for_user(db, user), created_by=user.id, input_type=input_type, text=text, status="captured")
            db.add(item)
            db.flush()
            audit(db, user, "mcp.inbox_captured", "inbox_item", item.id, client_id=grant.client_id)
            result = _save_idempotent(db, grant, "capture_inbox", request_id, {"id": item.id, "status": item.status, "resource_uri": f"openlab://inbox/{item.id}"})
            db.commit()
            return result
        finally:
            db.close()

    @server.tool(description="Create a project draft as an idempotent write.", annotations=_annotations(read_only=False, idempotent=True))
    def create_project(name: str, description: str | None = None, request_id: str = "") -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:write")
            previous = _idempotent(db, grant, "create_project", request_id)
            if previous:
                return previous
            item = Project(lab_id=lab_for_user(db, user), name=name, description=description, status="pending")
            db.add(item)
            db.flush()
            audit(db, user, "mcp.project_created", "project", item.id, client_id=grant.client_id)
            result = _save_idempotent(db, grant, "create_project", request_id, {"id": item.id, "name": item.name, "resource_uri": f"openlab://projects/{item.id}"})
            db.commit()
            return result
        finally:
            db.close()

    @server.tool(description="Add a project requirement as an idempotent draft.", annotations=_annotations(read_only=False, idempotent=True))
    def add_project_requirement(project_id: str, name: str, quantity: float, priority: str = "required", request_id: str = "") -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:write")
            previous = _idempotent(db, grant, "add_project_requirement", request_id)
            if previous:
                return previous
            project = db.scalar(select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user)).with_for_update())
            if not project:
                raise ToolError("Project not found")
            requirement = Requirement(project_id=project.id, name=name, quantity=Decimal(str(quantity)), priority=priority)
            db.add(requirement)
            project.revision += 1
            db.flush()
            audit(db, user, "mcp.requirement_added", "requirement", requirement.id, client_id=grant.client_id)
            result = _save_idempotent(db, grant, "add_project_requirement", request_id, {"id": requirement.id, "project_id": project.id})
            db.commit()
            return result
        finally:
            db.close()

    @server.tool(description="Preview a consequential inventory action and return a short-lived confirmation receipt.", annotations=_annotations(read_only=False, destructive=False, idempotent=True))
    def preview_inventory_change(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        db = SessionLocal()
        try:
            _, grant = _actor(db, "openlab:commit")
            if action not in {"receive", "move", "consume", "adjust", "archive_item"}:
                raise ToolError("Unsupported inventory action")
            return _result({"status": "confirmation_required", **_receipt(db, grant, f"inventory:{action}", payload)})
        finally:
            db.close()

    @server.tool(description="Apply a previously previewed inventory action using its one-use confirmation receipt.", annotations=_annotations(read_only=False, destructive=True, idempotent=True))
    def apply_inventory_change(receipt: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            user, grant = _actor(db, "openlab:commit")
            item = db.scalar(select(McpActionReceipt).where(McpActionReceipt.id == receipt, McpActionReceipt.grant_id == grant.id).with_for_update())
            if not item:
                raise ToolError("CONFIRMATION_REQUIRED: receipt not found")
            if item.consumed_at and item.result is not None:
                return dict(item.result)
            if not item.action.startswith("inventory:") or item.expires_at <= datetime.now(UTC):
                raise ToolError("CONFIRMATION_REQUIRED: receipt is invalid or expired")
            payload = dict(item.payload)
            action = item.action.removeprefix("inventory:")
            request_id = f"mcp:{item.id}"
            if action == "archive_item":
                thing = get_lab_thing(db, user, str(payload["thing_id"]))
                thing.archived_at = datetime.now(UTC)
                result: dict[str, object] = {"status": "applied", "action": action, "thing_id": thing.id}
            elif action == "adjust":
                movement = adjust_inventory(
                    db, user, thing_id=str(payload["thing_id"]), location_id=str(payload["location_id"]),
                    counted_quantity=Decimal(str(payload["counted_quantity"])), revision=int(str(payload["revision"])),
                    note=str(payload["note"]), idempotency_key=request_id,
                )
                result = {"status": "applied", "action": action, "movement_id": movement.id}
            else:
                required = {"receive": (None, "to_location_id"), "move": ("from_location_id", "to_location_id"), "consume": ("from_location_id", None)}
                source_key, destination_key = required[action]
                if source_key and not payload.get(source_key):
                    raise ToolError(f"VALIDATION_ERROR: {action} needs {source_key}")
                if destination_key and not payload.get(destination_key):
                    raise ToolError(f"VALIDATION_ERROR: {action} needs {destination_key}")
                movement = apply_movement(
                    db, user, thing_id=str(payload["thing_id"]), quantity=Decimal(str(payload["quantity"])),
                    movement_type=action, idempotency_key=request_id,
                    from_location_id=str(payload[source_key]) if source_key else None,
                    to_location_id=str(payload[destination_key]) if destination_key else None,
                    note=str(payload.get("note") or "") or None,
                )
                result = {"status": "applied", "action": action, "movement_id": movement.id}
            item.consumed_at = datetime.now(UTC)
            item.result = result
            audit(db, user, "mcp.inventory_confirmation_consumed", "mcp_receipt", item.id, client_id=grant.client_id)
            db.commit()
            return result
        finally:
            db.close()

    return server


product_mcp = create_product_mcp()
