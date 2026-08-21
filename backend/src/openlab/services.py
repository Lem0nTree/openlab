import hashlib
import mimetypes
import re
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    Allocation,
    Attachment,
    AuditEvent,
    Capability,
    InboxCandidate,
    InboxItem,
    Location,
    ProviderConfig,
    StockBalance,
    StockMovement,
    TechnicalFact,
    Thing,
    ThingAlias,
    ThingInterface,
    User,
)
from .providers import (
    OpenAICompatibleProvider,
    ProviderError,
    decrypt_secret,
    is_local_endpoint,
)
from .schemas import InboxCandidateInput


def audit(
    db: Session, user: User, action: str, entity_type: str, entity_id: str, **details: object
) -> None:
    db.add(
        AuditEvent(
            lab_id=lab_for_user(db, user),
            actor_id=user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )


def lab_for_user(db: Session, user: User) -> str:
    from .models import Membership

    lab_id = db.scalar(select(Membership.lab_id).where(Membership.user_id == user.id))
    if not lab_id:
        raise HTTPException(status_code=403, detail="User is not a lab member")
    return lab_id


def get_lab_thing(db: Session, user: User, thing_id: str) -> Thing:
    item = db.scalar(
        select(Thing).where(Thing.id == thing_id, Thing.lab_id == lab_for_user(db, user))
    )
    if not item:
        raise HTTPException(status_code=404, detail="Thing not found")
    return item


def get_lab_location(db: Session, user: User, location_id: str) -> Location:
    item = db.scalar(
        select(Location).where(
            Location.id == location_id, Location.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Location not found")
    return item


def create_thing(
    db: Session,
    user: User,
    *,
    name: str,
    category: str,
    manufacturer: str | None,
    mpn: str | None,
    metadata: dict[str, object],
    aliases: Iterable[str] = (),
) -> Thing:
    thing = Thing(
        lab_id=lab_for_user(db, user),
        name=name,
        category=category,
        manufacturer=manufacturer,
        mpn=mpn,
        metadata_json=metadata,
    )
    db.add(thing)
    db.flush()
    for value in {a.strip() for a in aliases if a.strip()}:
        db.add(ThingAlias(thing_id=thing.id, value=value))
    audit(db, user, "thing.created", "thing", thing.id)
    return thing


def _balance(
    db: Session, thing_id: str, location_id: str, lock: bool = False
) -> StockBalance | None:
    query = select(StockBalance).where(
        StockBalance.thing_id == thing_id, StockBalance.location_id == location_id
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _change_balance(db: Session, thing_id: str, location_id: str, delta: Decimal) -> None:
    balance = _balance(db, thing_id, location_id, lock=True)
    if not balance:
        if delta < 0:
            raise HTTPException(status_code=409, detail="Insufficient stock at source location")
        db.add(StockBalance(thing_id=thing_id, location_id=location_id, quantity=delta))
        return
    updated = Decimal(balance.quantity) + delta
    if updated < 0:
        raise HTTPException(status_code=409, detail="Insufficient stock at source location")
    balance.quantity = updated
    balance.revision += 1


def apply_movement(
    db: Session,
    user: User,
    *,
    thing_id: str,
    quantity: Decimal,
    movement_type: str,
    idempotency_key: str,
    from_location_id: str | None = None,
    to_location_id: str | None = None,
    note: str | None = None,
) -> StockMovement:
    lab_id = lab_for_user(db, user)
    existing = db.scalar(
        select(StockMovement).where(
            StockMovement.lab_id == lab_id, StockMovement.idempotency_key == idempotency_key
        )
    )
    if existing:
        return existing
    get_lab_thing(db, user, thing_id)
    if not from_location_id and not to_location_id:
        raise HTTPException(
            status_code=422, detail="A stock movement needs a source or destination"
        )
    if from_location_id:
        get_lab_location(db, user, from_location_id)
        _change_balance(db, thing_id, from_location_id, -quantity)
    if to_location_id:
        get_lab_location(db, user, to_location_id)
        _change_balance(db, thing_id, to_location_id, quantity)
    movement = StockMovement(
        lab_id=lab_id,
        thing_id=thing_id,
        quantity=quantity,
        movement_type=movement_type,
        idempotency_key=idempotency_key,
        from_location_id=from_location_id,
        to_location_id=to_location_id,
        actor_id=user.id,
        note=note,
    )
    db.add(movement)
    db.flush()
    audit(db, user, f"stock.{movement_type}", "stock_movement", movement.id, thing_id=thing_id)
    return movement


def available_quantity(db: Session, thing_id: str, *, lock: bool = False) -> Decimal:
    """Return stock not already reserved, optionally holding row locks until commit.

    Allocation creation uses the locking variant so that competing requests cannot both
    observe the same available quantity.  We sum locked rows in Python because PostgreSQL
    does not permit ``FOR UPDATE`` on aggregate queries.
    """
    balance_query = select(StockBalance).where(StockBalance.thing_id == thing_id)
    reservation_query = select(Allocation).where(
        Allocation.thing_id == thing_id, Allocation.state == "reserved"
    )
    if lock:
        balance_query = balance_query.with_for_update(of=StockBalance)
        reservation_query = reservation_query.with_for_update(of=Allocation)
    balances = db.scalars(balance_query).all()
    reservations = db.scalars(reservation_query).all()
    return sum((Decimal(row.quantity) for row in balances), Decimal()) - sum(
        (Decimal(row.quantity) for row in reservations), Decimal()
    )


async def save_upload(
    db: Session, user: User, inbox_item_id: str, upload: UploadFile
) -> Attachment:
    settings = get_settings()
    payload = await upload.read(settings.upload_max_bytes + 1)
    if len(payload) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large"
        )
    digest = hashlib.sha256(payload).hexdigest()
    suffix = Path(upload.filename or "file").suffix.lower()
    storage_key = f"objects/sha256/{digest[:2]}/{digest}{suffix}"
    target = settings.data_dir / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)
    attachment = Attachment(
        lab_id=lab_for_user(db, user),
        inbox_item_id=inbox_item_id,
        sha256=digest,
        content_type=upload.content_type
        or mimetypes.guess_type(upload.filename or "")[0]
        or "application/octet-stream",
        size_bytes=len(payload),
        original_name=upload.filename,
        storage_key=storage_key,
    )
    db.add(attachment)
    return attachment


def attachment_bytes(attachment: Attachment) -> bytes:
    target = get_settings().data_dir / attachment.storage_key
    if not target.is_file():
        raise ProviderError("The stored source artifact is unavailable")
    return target.read_bytes()


def active_provider(db: Session, lab_id: str) -> tuple[OpenAICompatibleProvider | None, ProviderConfig | None]:
    config = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.lab_id == lab_id, ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.updated_at.desc())
    )
    if not config:
        return None, None
    return (
        OpenAICompatibleProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=decrypt_secret(config.secret_ciphertext, get_settings().encryption_key),
        ),
        config,
    )


def fallback_candidates(text: str | None) -> list[InboxCandidateInput]:
    """Useful offline behavior; it intentionally makes no identity claims."""
    value = (text or "").strip()
    if not value:
        return []
    match = re.match(
        r"^(?P<quantity>\d+(?:\.\d+)?)\s*[x×]\s*(?P<name>.+)$", value, re.IGNORECASE
    )
    if match:
        return [
            InboxCandidateInput(
                name=match.group("name").strip(),
                quantity=Decimal(match.group("quantity")),
                confidence="generic",
            )
        ]
    return [InboxCandidateInput(name=value[:300], quantity=Decimal(1), confidence="unresolved")]


def process_inbox_item(db: Session, inbox: InboxItem) -> None:
    """Normalize source modalities into validated review candidates, never stock writes."""
    lab_id = inbox.lab_id
    attachments = list(
        db.scalars(select(Attachment).where(Attachment.inbox_item_id == inbox.id)).all()
    )
    provider, config = active_provider(db, lab_id)
    source_text = inbox.text or ""
    images: list[tuple[bytes, str]] = []
    evidence: dict[str, object] = {
        "input_type": inbox.input_type,
        "attachment_count": len(attachments),
        "egress": "local" if config and is_local_endpoint(config.base_url) else "external",
    }
    try:
        if provider:
            for attachment in attachments:
                raw = attachment_bytes(attachment)
                if attachment.content_type.startswith("image/"):
                    images.append((raw, attachment.content_type))
                elif attachment.content_type.startswith("audio/"):
                    source_text = f"{source_text}\n{provider.transcribe(raw, attachment.content_type)}".strip()
            result = provider.extract_inbox(source_text, images)
            raw_candidates = result.get("candidates", [])
            if not isinstance(raw_candidates, list):
                raise ProviderError("Provider candidates must be an array")
            candidates = [InboxCandidateInput.model_validate(value) for value in raw_candidates]
            evidence.update(
                {
                    "provider": config.provider,
                    "model": config.model,
                    "base_url": config.base_url,
                    "source_leaves_server": not is_local_endpoint(config.base_url),
                }
            )
            inbox.provider_name = config.provider
        else:
            candidates = fallback_candidates(source_text)
            evidence.update({"provider": "disabled", "source_leaves_server": False})
        for current in db.scalars(
            select(InboxCandidate).where(InboxCandidate.inbox_item_id == inbox.id)
        ).all():
            db.delete(current)
        db.flush()
        for candidate in candidates:
            db.add(
                InboxCandidate(
                    inbox_item_id=inbox.id,
                    name=candidate.name,
                    quantity=candidate.quantity,
                    category=candidate.category,
                    confidence=candidate.confidence,
                    provenance={
                        "source": "provider" if provider else "offline_parser",
                        "input_type": inbox.input_type,
                        "provider": config.provider if config else "disabled",
                        "model": config.model if config else None,
                    },
                )
            )
        inbox.text = source_text or inbox.text
        inbox.status = "needs_review"
        inbox.error = None
        inbox.processing_evidence = evidence
    except ProviderError as exc:
        inbox.status = "failed"
        inbox.error = str(exc)
        evidence["source_leaves_server"] = bool(config and not is_local_endpoint(config.base_url))
        inbox.processing_evidence = evidence
        raise


def compatible_things(
    db: Session,
    user: User,
    required_capabilities: list[str],
    required_interfaces: list[str],
    minimum_facts: dict[str, Decimal],
) -> list[dict[str, object]]:
    lab_id = lab_for_user(db, user)
    things = db.scalars(
        select(Thing).where(Thing.lab_id == lab_id, Thing.archived_at.is_(None))
    ).all()
    outcomes: list[dict[str, object]] = []
    for thing in things:
        evidence: list[str] = []
        result = "pass"
        caps = set(
            db.scalars(select(Capability.value).where(Capability.thing_id == thing.id)).all()
        )
        interfaces = set(
            db.scalars(select(ThingInterface.kind).where(ThingInterface.thing_id == thing.id)).all()
        )
        for cap in required_capabilities:
            if cap not in caps:
                result = "fail"
                evidence.append(f"missing capability: {cap}")
        for interface in required_interfaces:
            if interface not in interfaces:
                result = "fail"
                evidence.append(f"missing interface: {interface}")
        for key, minimum in minimum_facts.items():
            fact = db.scalar(
                select(TechnicalFact).where(
                    TechnicalFact.thing_id == thing.id, TechnicalFact.key == key
                )
            )
            if not fact or fact.value_numeric is None:
                if result != "fail":
                    result = "unknown"
                evidence.append(f"unknown fact: {key}")
            elif Decimal(fact.value_numeric) < minimum:
                result = "fail"
                evidence.append(f"{key} below required minimum")
            else:
                evidence.append(f"{key} satisfies minimum")
        if not evidence:
            evidence.append("No constraints supplied")
        outcomes.append({"thing_id": thing.id, "status": result, "evidence": evidence})
    return outcomes
