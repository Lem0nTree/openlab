import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InboxStatus(StrEnum):
    CAPTURED = "captured"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONFIRMED = "confirmed"
    PARTIALLY_RECEIVED = "partially_received"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Lab(Base, Timestamped):
    __tablename__ = "labs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    units: Mapped[str] = mapped_column(String(20), default="metric", nullable=False)
    kicad_cli: Mapped[str | None] = mapped_column(String(500))


class User(Base, Timestamped):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Membership(Base, Timestamped):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("lab_id", "user_id", name="uq_membership_lab_user"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(30), default="owner", nullable=False)


class SessionToken(Base):
    __tablename__ = "session_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Thing(Base, Timestamped):
    __tablename__ = "things"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(120), default="uncategorized", nullable=False, index=True
    )
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    mpn: Mapped[str | None] = mapped_column(String(200), index=True)
    tracking_mode: Mapped[str] = mapped_column(String(30), default="quantity", nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ThingAlias(Base):
    __tablename__ = "thing_aliases"
    __table_args__ = (UniqueConstraint("thing_id", "value", name="uq_thing_alias"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[str] = mapped_column(String(300), nullable=False, index=True)


class Location(Base, Timestamped):
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    public_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=lambda: token_code()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def token_code() -> str:
    return uuid.uuid4().hex


class StockBalance(Base, Timestamped):
    __tablename__ = "stock_balances"
    __table_args__ = (
        UniqueConstraint("thing_id", "location_id", name="uq_balance_thing_location"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(ForeignKey("things.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0, nullable=False)


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        UniqueConstraint("lab_id", "idempotency_key", name="uq_movement_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    thing_id: Mapped[str] = mapped_column(ForeignKey("things.id"), nullable=False, index=True)
    from_location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), index=True)
    to_location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxItem(Base, Timestamped):
    __tablename__ = "inbox_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    input_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default=InboxStatus.CAPTURED, nullable=False, index=True
    )
    text: Mapped[str | None] = mapped_column(Text)
    provider_name: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    processing_evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )


class InboxCandidate(Base, Timestamped):
    __tablename__ = "inbox_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inbox_item_id: Mapped[str] = mapped_column(
        ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1, nullable=False)
    category: Mapped[str] = mapped_column(String(120), default="uncategorized", nullable=False)
    identity_confidence: Mapped[str] = mapped_column(
        "confidence", String(20), default="unresolved", nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="proposed", nullable=False, index=True)
    thing_id: Mapped[str | None] = mapped_column(ForeignKey("things.id"), index=True)
    product_url: Mapped[str | None] = mapped_column(String(2000))
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class Attachment(Base, Timestamped):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    inbox_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("inbox_items.id", ondelete="CASCADE")
    )
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(500))
    storage_key: Mapped[str | None] = mapped_column(String(600))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_error: Mapped[str | None] = mapped_column(Text)


class Job(Base, Timestamped):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Project(Base, Timestamped):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    design_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class Requirement(Base, Timestamped):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "role_key", name="uq_requirement_source_role"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="required", nullable=False)
    constraints: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="user", nullable=False)
    role_key: Mapped[str | None] = mapped_column(String(120))
    selected_thing_id: Mapped[str | None] = mapped_column(ForeignKey("things.id"), index=True)
    match_status: Mapped[str | None] = mapped_column(String(30))


class Allocation(Base, Timestamped):
    __tablename__ = "allocations"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_allocation_project_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    thing_id: Mapped[str] = mapped_column(ForeignKey("things.id"), nullable=False)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    state: Mapped[str] = mapped_column(String(30), default="reserved", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class TechnicalFact(Base, Timestamped):
    __tablename__ = "technical_facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    source_type: Mapped[str] = mapped_column(String(40), default="user", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(600))
    verification_state: Mapped[str] = mapped_column(
        String(30), default="unverified", nullable=False
    )


class Capability(Base):
    __tablename__ = "capabilities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[str] = mapped_column(String(150), nullable=False)


class ThingInterface(Base):
    __tablename__ = "thing_interfaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class Pin(Base):
    __tablename__ = "pins"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    number: Mapped[str | None] = mapped_column(String(40))
    electrical_type: Mapped[str] = mapped_column(String(40), default="passive", nullable=False)
    alternate_functions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    restrictions: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(600))
    verification_state: Mapped[str] = mapped_column(
        String(30), default="unverified", nullable=False
    )


class Relationship(Base):
    __tablename__ = "thing_relationships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    from_thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False
    )
    to_thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)


class Embedding(Base, Timestamped):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint(
            "thing_id", "purpose", "provider", "model", name="uq_embedding_space_thing"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    thing_id: Mapped[str] = mapped_column(
        ForeignKey("things.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_vector: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), default="profile", nullable=False)


class ProviderConfig(Base, Timestamped):
    __tablename__ = "provider_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(600), nullable=False)
    model: Mapped[str] = mapped_column(String(300), nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(300))
    embeddings_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
