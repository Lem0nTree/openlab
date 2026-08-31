"""Minimal, durable client for OpenLab's pseudonymous product telemetry."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import (
    AuditEvent,
    CommunicationConsent,
    Job,
    Lab,
    Project,
    TelemetryOutbox,
    TelemetryState,
    Thing,
    User,
)
from .providers import ProviderError, decrypt_secret, encrypt_secret

DISCLOSURE_VERSION = "2026-08-31"
_RELEASE_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def production_reporting(settings: Settings | None = None) -> bool:
    value = settings or get_settings()
    return bool(value.telemetry_endpoint.strip()) and bool(_RELEASE_VERSION.fullmatch(value.version))


def ensure_state(db: Session, settings: Settings | None = None) -> TelemetryState:
    state = db.get(TelemetryState, "installation")
    if state is not None:
        return state
    value = settings or get_settings()
    state = TelemetryState(
        id="installation",
        installation_id=token_urlsafe(24),
        credential_ciphertext=encrypt_secret(token_urlsafe(32), value.encryption_key),
        disclosure_version=DISCLOSURE_VERSION,
    )
    db.add(state)
    db.flush()
    return state


def queue_outbox(
    db: Session, kind: str, idempotency_key: str, *, activity_day: datetime | None = None,
    consent_id: str | None = None,
) -> None:
    if db.scalar(select(TelemetryOutbox.id).where(TelemetryOutbox.idempotency_key == idempotency_key)):
        return
    db.add(TelemetryOutbox(
        kind=kind, idempotency_key=idempotency_key, activity_day=activity_day,
        consent_id=consent_id, next_attempt_at=datetime.now(UTC),
    ))


def queue_registration(db: Session, state: TelemetryState) -> None:
    if state.registered_at is None:
        queue_outbox(db, "register", f"register:{state.installation_id}")


def queue_preference(db: Session, state: TelemetryState) -> None:
    queue_outbox(db, "preference", f"preference:{state.installation_id}:{state.usage_enabled}:{int(datetime.now(UTC).timestamp())}")


def midnight(day: datetime) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def activity_payload(
    db: Session, state: TelemetryState, day: datetime, settings: Settings | None = None
) -> dict[str, object]:
    value = settings or get_settings()
    start = midnight(day)
    end = start + timedelta(days=1)
    inbox_processed = db.scalar(select(func.count(Job.id)).where(
        Job.kind == "inbox.process", Job.status == "completed",
        Job.completed_at >= start, Job.completed_at < end,
    )) or 0
    components_confirmed = db.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.action == "inbox.candidate_confirmed",
        AuditEvent.created_at >= start, AuditEvent.created_at < end,
    )) or 0
    things_created = db.scalar(select(func.count(Thing.id)).where(
        Thing.created_at >= start, Thing.created_at < end,
    )) or 0
    projects_created = db.scalar(select(func.count(Project.id)).where(
        Project.created_at >= start, Project.created_at < end,
    )) or 0
    return {
        "schema_version": 1,
        "installation_id": state.installation_id,
        "app_version": value.version,
        "platform": platform_name(),
        "activity_day": start.date().isoformat(),
        "inbox_processed": int(inbox_processed),
        "components_confirmed": int(components_confirmed),
        "things_created": int(things_created),
        "projects_created": int(projects_created),
        # Email intake has no released configuration yet; never infer it from Inbox content.
        "email_intake_enabled": False,
    }


def platform_name() -> str:
    import platform

    raw = platform.machine().lower()
    if raw in {"aarch64", "arm64"}:
        return "arm64"
    if raw in {"x86_64", "amd64"}:
        return "amd64"
    return "other"


def schedule(db: Session, settings: Settings | None = None) -> None:
    value = settings or get_settings()
    if not production_reporting(value):
        return
    state = ensure_state(db, value)
    queue_registration(db, state)
    completed = db.scalar(select(func.count(Lab.id)).where(Lab.onboarding_completed_at.is_not(None))) or 0
    if not completed:
        return
    if state.onboarding_seen_at is None:
        state.onboarding_seen_at = datetime.now(UTC)
    if not state.usage_enabled:
        return
    now = datetime.now(UTC)
    # Spread daily submissions over the hour deterministically without recording a schedule.
    jitter_minutes = hashlib.sha256(state.installation_id.encode()).digest()[0] % 60
    if now < midnight(now) + timedelta(minutes=jitter_minutes):
        return
    day = midnight(now - timedelta(days=1))
    first_day = midnight(state.last_queued_day + timedelta(days=1)) if state.last_queued_day else day
    while first_day <= day:
        queue_outbox(
            db,
            "activity",
            f"activity:{state.installation_id}:{first_day.date().isoformat()}",
            activity_day=first_day,
        )
        state.last_queued_day = first_day
        first_day += timedelta(days=1)


def due_outbox(db: Session) -> TelemetryOutbox | None:
    return db.scalar(select(TelemetryOutbox).where(
        TelemetryOutbox.status == "queued", TelemetryOutbox.next_attempt_at <= datetime.now(UTC)
    ).order_by(TelemetryOutbox.created_at).with_for_update(skip_locked=True).limit(1))


def _credential(state: TelemetryState, settings: Settings) -> str:
    token = decrypt_secret(state.credential_ciphertext, settings.encryption_key)
    if not token:
        raise ProviderError("Telemetry credential is unavailable")
    return token


def _endpoint(settings: Settings, suffix: str) -> str:
    return settings.telemetry_endpoint.rstrip("/") + suffix


def deliver_one(db: Session, settings: Settings | None = None) -> bool:
    value = settings or get_settings()
    if not production_reporting(value):
        return False
    event = due_outbox(db)
    if event is None:
        return False
    state = ensure_state(db, value)
    try:
        credential = _credential(state, value)
        headers = {"Authorization": f"Bearer {credential}", "Idempotency-Key": event.idempotency_key}
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            if event.kind == "register":
                response = client.post(_endpoint(value, "/installations/register"), json={
                    "schema_version": 1, "installation_id": state.installation_id,
                    "installation_token": credential, "app_version": value.version,
                    "platform": platform_name(),
                }, headers={"Idempotency-Key": event.idempotency_key})
            elif event.kind == "activity" and event.activity_day is not None:
                if state.registered_at is None:
                    raise ProviderError("Telemetry registration has not been acknowledged")
                payload = activity_payload(db, state, event.activity_day, value)
                payload["event_id"] = event.idempotency_key
                response = client.put(_endpoint(value, "/activity"), json=payload, headers=headers)
            elif event.kind == "preference":
                response = client.put(_endpoint(value, "/preferences"), json={
                    "installation_id": state.installation_id, "usage_enabled": state.usage_enabled,
                    "disclosure_version": state.disclosure_version,
                }, headers=headers)
            elif event.kind == "history_delete":
                response = client.delete(_endpoint(value, "/history"), headers=headers)
            elif event.kind in {"subscribe", "unsubscribe"} and event.consent_id:
                consent = db.get(CommunicationConsent, event.consent_id)
                user = db.get(User, consent.user_id) if consent else None
                if not consent or not user:
                    raise ProviderError("Newsletter consent is unavailable")
                if event.kind == "subscribe":
                    response = client.post(_endpoint(value, "/subscriptions"), json={
                        "email": user.email, "consented_at": consent.consented_at.isoformat() if consent.consented_at else None,
                        "consent_version": consent.notice_version, "source": "owner_setup",
                    }, headers=headers)
                else:
                    token = decrypt_secret(consent.subscription_token_ciphertext, value.encryption_key)
                    if not token:
                        raise ProviderError("Newsletter subscription token is unavailable")
                    response = client.delete(_endpoint(value, f"/subscriptions/{token}"), headers=headers)
            else:
                raise ProviderError("Unsupported telemetry delivery")
        if response.status_code >= 300:
            raise ProviderError(f"Telemetry endpoint returned {response.status_code}")
        if event.kind == "register":
            state.registered_at = datetime.now(UTC)
        elif event.kind == "activity" and event.activity_day:
            state.last_reported_day = event.activity_day
        elif event.kind == "subscribe" and event.consent_id:
            consent = db.get(CommunicationConsent, event.consent_id)
            body = response.json() if response.content else {}
            if consent and isinstance(body, dict) and isinstance(body.get("subscription_token"), str):
                consent.subscription_token_ciphertext = encrypt_secret(body["subscription_token"], value.encryption_key)
                consent.status = "subscribed"
        elif event.kind == "unsubscribe" and event.consent_id:
            consent = db.get(CommunicationConsent, event.consent_id)
            if consent:
                consent.status = "unsubscribed"
        event.status, event.completed_at, event.last_error = "completed", datetime.now(UTC), None
    except (httpx.HTTPError, ProviderError, ValueError) as exc:
        event.attempts += 1
        seconds = min(24 * 60 * 60, 2 ** min(event.attempts, 16))
        event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=seconds)
        event.last_error = str(exc)[:500]
    return True
