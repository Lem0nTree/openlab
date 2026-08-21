from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SetupRequest(BaseModel):
    token: str
    lab_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(APIModel):
    id: str
    email: str
    display_name: str
    is_owner: bool


class LabOut(APIModel):
    id: str
    name: str
    units: str


class ThingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    category: str = Field(default="uncategorized", max_length=120)
    manufacturer: str | None = None
    mpn: str | None = None
    tracking_mode: Literal["quantity"] = "quantity"
    metadata: dict[str, object] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)


class ThingPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    metadata: dict[str, object] | None = None
    revision: int


class ThingOut(APIModel):
    id: str
    name: str
    category: str
    manufacturer: str | None
    mpn: str | None
    tracking_mode: str
    metadata_json: dict[str, object]
    revision: int
    created_at: datetime
    updated_at: datetime


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class LocationOut(APIModel):
    id: str
    name: str
    parent_id: str | None
    public_code: str
    revision: int


class StockMutation(BaseModel):
    thing_id: str
    quantity: Decimal = Field(gt=0)
    to_location_id: str | None = None
    from_location_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class StockMovementOut(APIModel):
    id: str
    thing_id: str
    from_location_id: str | None
    to_location_id: str | None
    quantity: Decimal
    movement_type: str
    created_at: datetime


class BalanceOut(APIModel):
    thing_id: str
    location_id: str
    quantity: Decimal


class InboxCapture(BaseModel):
    input_type: Literal["text", "photo", "screenshot", "voice", "pdf"]
    text: str | None = Field(default=None, max_length=20_000)


class InboxCandidateInput(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(gt=0)
    category: str = "uncategorized"
    confidence: Literal["confirmed", "likely", "generic", "unresolved"] = "unresolved"


class InboxConfirm(BaseModel):
    location_id: str
    candidate: InboxCandidateInput
    existing_thing_id: str | None = None


class InboxOut(APIModel):
    id: str
    input_type: str
    status: str
    text: str | None
    error: str | None
    processing_evidence: dict[str, object]
    created_at: datetime


class InboxCandidateOut(APIModel):
    id: str
    name: str
    quantity: Decimal
    category: str
    confidence: str
    provenance: dict[str, object]


class ProviderConfigInput(BaseModel):
    """OpenAI-compatible endpoint settings; an absent key preserves the stored key."""

    base_url: str = Field(min_length=8, max_length=600)
    model: str = Field(min_length=1, max_length=300)
    api_key: str | None = Field(default=None, max_length=2000)
    enabled: bool = False


class ProviderConfigOut(APIModel):
    id: str
    provider: str
    base_url: str
    model: str
    enabled: bool
    has_api_key: bool
    egress: Literal["local", "external"]


class ProviderModelsOut(BaseModel):
    models: list[str]
    egress: Literal["local", "external"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None


class RequirementCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(gt=0)
    priority: Literal["required", "recommended", "optional"] = "required"
    constraints: dict[str, object] = Field(default_factory=dict)


class ProjectOut(APIModel):
    id: str
    name: str
    description: str | None
    status: str


class RequirementOut(APIModel):
    id: str
    name: str
    quantity: Decimal
    priority: str
    constraints: dict[str, object]


class AllocationOut(APIModel):
    id: str
    thing_id: str
    location_id: str | None
    quantity: Decimal
    state: str


class ProjectDetailOut(ProjectOut):
    requirements: list[RequirementOut]
    allocations: list[AllocationOut]


class AllocationCreate(BaseModel):
    thing_id: str
    location_id: str
    quantity: Decimal = Field(gt=0)
    state: Literal["reserved", "in_use", "recoverable"] = "reserved"


class AllocationRecover(BaseModel):
    location_id: str
    quantity: Decimal = Field(gt=0)


class FactCreate(BaseModel):
    key: str
    value_numeric: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    source_type: str = "user"
    source_ref: str | None = None
    verification_state: Literal["unverified", "accepted", "verified"] = "unverified"


class CompatibilityRequest(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list)
    required_interfaces: list[str] = Field(default_factory=list)
    minimum_facts: dict[str, Decimal] = Field(default_factory=dict)


class CompatibilityResult(BaseModel):
    thing_id: str
    status: Literal["pass", "fail", "unknown"]
    evidence: list[str]


class AIQuery(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


class AIAnswer(BaseModel):
    status: Literal["disabled", "ready"]
    answer: str
    evidence: list[dict[str, object]] = Field(default_factory=list)
