import hashlib
import html
import ipaddress
import json
import math
import mimetypes
import re
import socket
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, UploadFile, status
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
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
    if not attachment.storage_key:
        raise ProviderError("The temporary source artifact has already been purged")
    target = get_settings().data_dir / attachment.storage_key
    if not target.is_file():
        raise ProviderError("The stored source artifact is unavailable")
    return target.read_bytes()


def purge_attachment(attachment: Attachment) -> None:
    """Remove a raw capture while retaining its small audit record and extracted evidence."""
    if attachment.purged_at:
        return
    try:
        if attachment.storage_key:
            target = get_settings().data_dir / attachment.storage_key
            if target.exists():
                target.unlink()
        attachment.storage_key = None
        attachment.purged_at = datetime.now(UTC)
        attachment.cleanup_error = None
    except OSError as exc:
        attachment.cleanup_error = str(exc)[:2000]


def cleanup_expired_attachments(db: Session, expiry_hours: int = 24) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=expiry_hours)
    attachments = db.scalars(
        select(Attachment).where(Attachment.purged_at.is_(None), Attachment.created_at < cutoff)
    ).all()
    for attachment in attachments:
        purge_attachment(attachment)
    return len(attachments)


def _candidate_parent_status(db: Session, inbox: InboxItem) -> str:
    states = list(
        db.scalars(
            select(InboxCandidate.status).where(InboxCandidate.inbox_item_id == inbox.id)
        ).all()
    )
    if not states or all(state == "proposed" for state in states):
        return "needs_review"
    actionable = [state for state in states if state != "ignored"]
    if actionable and all(state == "received" for state in actionable):
        return "committed"
    if any(state == "received" for state in actionable):
        return "partially_received"
    if all(state in {"confirmed", "ignored"} for state in states):
        return "confirmed"
    if any(state == "confirmed" for state in states):
        return "partially_confirmed"
    return "needs_review"


def refresh_inbox_status(db: Session, inbox: InboxItem) -> None:
    inbox.status = _candidate_parent_status(db, inbox)


def _html_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def email_candidates(raw: bytes) -> tuple[list[InboxCandidateInput], dict[str, object], str]:
    """Extract conservative order lines without treating tracking URLs as products."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_parts: list[str] = []
    for part in message.walk():
        if (
            part.get_content_maintype() == "multipart"
            or part.get_content_disposition() == "attachment"
        ):
            continue
        content = part.get_content()
        body_parts.append(
            _html_text(content) if part.get_content_type() == "text/html" else str(content)
        )
    body = "\n".join(body_parts)
    classification_text = f"{message.get('subject', '')}\n{body}"
    links = re.findall(r"https?://[^\s<>\"']+", body)
    classified = []
    for link in links[:50]:
        lowered = link.lower()
        role = "product_page"
        if any(token in lowered for token in ("track", "tracking", "shipment")):
            role = "tracking"
        elif any(token in lowered for token in ("order", "receipt", "purchase")):
            role = "order_details"
        elif any(token in lowered for token in ("promo", "campaign", "unsubscribe")):
            role = "promotional"
        classified.append({"url": link, "role": role})
    candidates: list[InboxCandidateInput] = []
    for line in body.splitlines():
        clean = re.sub(r"\s+", " ", line).strip(" -•\t")
        match = re.match(
            r"(?:qty\s*[:x]?\s*)?(\d+(?:\.\d+)?)\s*[x×]\s*(.{2,300})$", clean, re.IGNORECASE
        )
        if match:
            candidates.append(
                InboxCandidateInput(
                    name=match.group(2).strip(),
                    quantity=Decimal(match.group(1)),
                    identity_confidence="unresolved",
                )
            )
    evidence = {
        "message_type": "shipment"
        if re.search(r"shipp|deliver", classification_text, re.IGNORECASE)
        else "order"
        if re.search(r"order|purchas", classification_text, re.IGNORECASE)
        else "unknown",
        "sender": str(message.get("from", ""))[:500],
        "order_reference": (
            re.search(
                r"(?:order|reference)\s*(?:#|no\.?|number)?\s*([A-Z0-9-]{4,})", body, re.IGNORECASE
            )
            or [None, None]
        )[1],
        "links": classified,
    }
    return candidates, evidence, body


def _safe_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=422, detail="Only public HTTP(S) product URLs are allowed")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=422, detail="Product URL host could not be resolved"
        ) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise HTTPException(
                status_code=422, detail="Product URL must not resolve to an internal address"
            )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def enrich_product_url(url: str) -> dict[str, object]:
    """Fetch small public HTML safely; this never writes canonical Thing data."""
    current = _safe_http_url(url)
    for _ in range(4):
        try:
            response = httpx.get(
                current,
                follow_redirects=False,
                timeout=8.0,
                headers={"User-Agent": "OpenLab/0.1 product review"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=422, detail="Product page could not be retrieved"
            ) from exc
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise HTTPException(
                    status_code=422, detail="Product page returned an invalid redirect"
                )
            current = _safe_http_url(urljoin(current, location))
            continue
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code >= 400:
            raise HTTPException(status_code=422, detail="Product page is inaccessible")
        if not content_type.startswith(("text/html", "text/plain")):
            raise HTTPException(status_code=422, detail="Product page must be HTML or plain text")
        if len(response.content) > 1_000_000:
            raise HTTPException(status_code=422, detail="Product page is too large")
        source = response.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", source, re.IGNORECASE | re.DOTALL)
        title = _html_text(title_match.group(1)) if title_match else ""
        description_match = re.search(
            r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']",
            source,
            re.IGNORECASE | re.DOTALL,
        )
        description = _html_text(description_match.group(1)) if description_match else ""
        return {
            "normalized_url": current,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "content_fingerprint": hashlib.sha256(response.content).hexdigest(),
            "proposal": {"name": title[:300] or None, "description": description[:2000] or None},
            "link_classification": "product_page",
        }
    raise HTTPException(status_code=422, detail="Product page redirected too many times")


def active_provider(
    db: Session, lab_id: str
) -> tuple[OpenAICompatibleProvider | None, ProviderConfig | None]:
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
    match = re.match(r"^(?P<quantity>\d+(?:\.\d+)?)\s*[x×]\s*(?P<name>.+)$", value, re.IGNORECASE)
    if match:
        return [
            InboxCandidateInput(
                name=match.group("name").strip(),
                quantity=Decimal(match.group("quantity")),
                identity_confidence="low",
            )
        ]
    return [
        InboxCandidateInput(name=value[:300], quantity=Decimal(1), identity_confidence="unresolved")
    ]


def canonical_profile(
    *,
    name: str,
    category: str,
    description: str | None = None,
    manufacturer: str | None = None,
    mpn: str | None = None,
    aliases: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    interfaces: Iterable[str] = (),
    facts: Iterable[str] = (),
) -> tuple[str, str]:
    """Produce a short, stable, privacy-safe sentence for Thing retrieval."""
    fields = {
        "name": name.strip(),
        "category": category.strip(),
        "description": description or "",
        "manufacturer": manufacturer or "",
        "mpn": mpn or "",
        "aliases": sorted({item.strip() for item in aliases if item.strip()}),
        "capabilities": sorted({item.strip() for item in capabilities if item.strip()}),
        "interfaces": sorted({item.strip() for item in interfaces if item.strip()}),
        "facts": sorted({item.strip() for item in facts if item.strip()}),
    }
    profile = f"{fields['name']} is an electronics {fields['category']}"
    if fields["description"]:
        profile = f"{profile}: {str(fields['description']).strip().rstrip('.')}"
    profile = f"{profile}."
    if fields["aliases"]:
        profile = f"{profile} Also known as: {', '.join(fields['aliases'])}."
    functions = [*fields["capabilities"], *fields["interfaces"]]
    if functions:
        profile = f"{profile} Functions and interfaces: {', '.join(functions)}."
    if fields["facts"]:
        profile = f"{profile} Recorded facts: {', '.join(fields['facts'])}."
    profile = profile[:1000]
    return profile, hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = list(left), list(right)
    if len(a) != len(b) or not a:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in a)) * math.sqrt(
        sum(value * value for value in b)
    )
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0.0


def extract_pdf_text(raw: bytes) -> str:
    """Extract bounded local PDF text; the raw file remains a temporary artifact."""
    reader = PdfReader(BytesIO(raw))
    parts: list[str] = []
    total = 0
    for page in reader.pages[:100]:
        value = page.extract_text() or ""
        if not value:
            continue
        remaining = 20_000 - total
        if remaining <= 0:
            break
        parts.append(value[:remaining])
        total += len(parts[-1])
    return "\n".join(parts).strip()


def _validated_provider_candidates(
    provider: OpenAICompatibleProvider,
    source_text: str,
    images: list[tuple[bytes, str]],
    evidence: dict[str, object],
    fallback: list[InboxCandidateInput] | None = None,
) -> list[InboxCandidateInput]:
    error: str | None = None
    for attempt in range(2):
        try:
            result = provider.extract_inbox(
                source_text, images, repair_error=error if attempt else None
            )
            raw_candidates = result.get("candidates", [])
            if not isinstance(raw_candidates, list):
                raise TypeError("candidates is not an array")
            candidates = [
                InboxCandidateInput.model_validate(value) for value in raw_candidates[:25]
            ]
            if not candidates:
                raise ValueError("candidates is empty")
            if attempt:
                evidence["schema_repair"] = True
            return candidates
        except (ProviderError, ValidationError, ValueError, TypeError) as exc:
            error = str(exc)[:1200]
    evidence["schema_fallback"] = True
    evidence["provider_validation_error"] = error
    candidates = fallback if fallback is not None else fallback_candidates(source_text)
    return candidates or [
        InboxCandidateInput(
            name="Unknown electronics item",
            quantity=Decimal(1),
            category="other",
            identity_confidence="unresolved",
        )
    ]


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
        email_evidence: dict[str, object] = {}
        if inbox.input_type == "email":
            for attachment in attachments:
                if attachment.content_type in {"message/rfc822", "text/plain", "text/html"}:
                    parsed, email_evidence, parsed_text = email_candidates(
                        attachment_bytes(attachment)
                    )
                    source_text = f"{source_text}\n{parsed_text}".strip()
                    if parsed:
                        candidates = parsed
                        break
            else:
                candidates = fallback_candidates(source_text)
        else:
            candidates = []
        for attachment in attachments:
            if attachment.content_type == "application/pdf":
                try:
                    pdf_text = extract_pdf_text(attachment_bytes(attachment))
                    source_text = f"{source_text}\n{pdf_text}".strip()
                    evidence["pdf_text_extracted"] = bool(pdf_text)
                except (OSError, PdfReadError, ValueError) as exc:
                    evidence["pdf_extraction_error"] = str(exc)[:500]
            elif provider and attachment.content_type.startswith("image/"):
                images.append((attachment_bytes(attachment), attachment.content_type))
            elif provider and attachment.content_type.startswith("audio/"):
                transcript = provider.transcribe(
                    attachment_bytes(attachment), attachment.content_type
                )
                source_text = f"{source_text}\n{transcript}".strip()
        if provider:
            assert config is not None
            if candidates:
                normalized_email: list[InboxCandidateInput] = []
                for original in candidates[:25]:
                    normalized = _validated_provider_candidates(
                        provider, original.name, [], evidence, fallback=[original]
                    )[0]
                    normalized_email.append(
                        normalized.model_copy(update={"quantity": original.quantity})
                    )
                candidates = normalized_email
                evidence["email_lines_classified"] = len(candidates)
            else:
                candidates = _validated_provider_candidates(provider, source_text, images, evidence)
            evidence.update(
                {
                    "provider": config.provider,
                    "model": config.model,
                    "base_url": config.base_url,
                    "source_leaves_server": not is_local_endpoint(config.base_url),
                }
            )
            inbox.provider_name = config.provider
        elif not candidates:
            candidates = fallback_candidates(source_text)
            if not candidates and attachments:
                candidates = [
                    InboxCandidateInput(
                        name="Unknown electronics item",
                        quantity=Decimal(1),
                        category="other",
                        identity_confidence="unresolved",
                    )
                ]
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
                    identity_confidence=candidate.identity_confidence,
                    provenance={
                        "source": "provider" if provider else "offline_parser",
                        "input_type": inbox.input_type,
                        "provider": config.provider if config else "disabled",
                        "model": config.model if config else None,
                        "description": candidate.description,
                        "observations": candidate.observations,
                        "raw_title": source_text[:20_000] or None,
                        **email_evidence,
                    },
                )
            )
        inbox.text = source_text or inbox.text
        inbox.status = "needs_review"
        inbox.error = None
        inbox.processing_evidence = evidence
        db.flush()
        # Captures are transient by default. Candidate evidence and normalized text survive.
        for attachment in attachments:
            purge_attachment(attachment)
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
