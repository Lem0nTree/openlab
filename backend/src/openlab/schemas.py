from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class LabSettingsInput(StrictInput):
    name: str = Field(min_length=1, max_length=200)
    units: Literal["metric", "imperial"]


class KicadSettingsInput(StrictInput):
    cli_path: str | None = Field(default=None, max_length=500)


class KicadSettingsOut(BaseModel):
    cli_path: str | None
    effective_cli: str | None
    source: Literal["settings", "environment", "unset"]
    check_status: Literal["unknown", "queued", "running", "available", "unavailable"]
    version: str | None
    error: str | None


class EnvironmentVariableOut(BaseModel):
    name: str
    category: Literal["application", "security", "infrastructure"]
    status: Literal["configured", "not_configured", "deployment_managed"]
    value: str | None = None
    secret: bool = False
    editable: bool = False
    restart_required: bool = True
    description: str


class SettingsOverviewOut(BaseModel):
    lab: LabOut
    kicad: KicadSettingsOut
    environment: list[EnvironmentVariableOut]


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


class LocationCreate(StrictInput):
    name: str = Field(min_length=1, max_length=200)


class LocationOut(APIModel):
    id: str
    name: str
    parent_id: str | None
    public_code: str
    revision: int
    thing_count: int = 0
    total_quantity: Decimal = Decimal()


class LocationQRInfo(BaseModel):
    target_url: str
    svg_url: str


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
    note: str | None
    created_at: datetime


class StockMovementDetailOut(StockMovementOut):
    thing_name: str
    from_location_name: str | None
    to_location_name: str | None


class BalanceOut(APIModel):
    thing_id: str
    location_id: str
    quantity: Decimal
    revision: int
    thing_name: str
    thing_category: str
    thing_manufacturer: str | None
    thing_mpn: str | None
    location_name: str


class StockAdjustment(StrictInput):
    thing_id: str
    location_id: str
    counted_quantity: Decimal = Field(ge=0)
    revision: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2000)


class InboxCapture(BaseModel):
    input_type: Literal["text", "photo", "screenshot", "voice", "pdf", "email"]
    text: str | None = Field(default=None, max_length=20_000)


ThingCategory = Literal[
    "module",
    "ic",
    "board",
    "sensor",
    "passive",
    "connector",
    "power",
    "tool",
    "other",
    "uncategorized",
]


class InboxCandidateInput(StrictInput):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    quantity: Decimal = Field(gt=0)
    category: ThingCategory = "uncategorized"
    identity_confidence: Literal["high", "medium", "low", "unresolved"] = "unresolved"
    observations: list[str] = Field(default_factory=list, max_length=8)


class InboxConfirm(BaseModel):
    location_id: str
    candidate: InboxCandidateInput
    existing_thing_id: str | None = None


class InboxCandidateConfirm(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    quantity: Decimal | None = Field(default=None, gt=0)
    category: ThingCategory | None = None
    existing_thing_id: str | None = None


class InboxCandidatePatch(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=600)
    quantity: Decimal | None = Field(default=None, gt=0)
    category: ThingCategory | None = None


class InboxCandidateReceive(BaseModel):
    location_id: str
    quantity: Decimal | None = Field(default=None, gt=0)


class InboxCandidateBatchConfirm(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=100)


class InboxEnrichURL(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


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
    identity_confidence: str
    status: str
    thing_id: str | None
    product_url: str | None
    provenance: dict[str, object]


class ProviderConfigInput(BaseModel):
    """OpenAI-compatible endpoint settings; an absent key preserves the stored key."""

    base_url: str = Field(min_length=8, max_length=600)
    model: str = Field(min_length=1, max_length=300)
    embedding_model: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=2000)
    enabled: bool = False
    embeddings_enabled: bool = False


class ProviderConfigOut(APIModel):
    id: str
    provider: str
    base_url: str
    model: str
    embedding_model: str | None
    enabled: bool
    embeddings_enabled: bool
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
    revision: int
    created_at: datetime
    updated_at: datetime


class ProjectUpdate(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["pending", "active", "completed", "archived", "cancelled"] | None = None


class RequirementOut(APIModel):
    id: str
    name: str
    quantity: Decimal
    priority: str
    constraints: dict[str, object]
    source: str
    role_key: str | None
    selected_thing_id: str | None
    match_status: str | None


class AllocationOut(APIModel):
    id: str
    thing_id: str
    location_id: str | None
    quantity: Decimal
    state: str


class ProjectDetailOut(ProjectOut):
    requirements: list[RequirementOut]
    allocations: list[AllocationOut]
    design_json: dict[str, object]


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


class KnowledgeSearchRequest(StrictInput):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=30)


class KnowledgeSearchResult(BaseModel):
    thing_id: str
    name: str
    category: str
    score: float
    match_type: Literal["exact", "text", "semantic"]
    available_quantity: Decimal
    locations: list[str] = Field(default_factory=list)


class AlternativeSearchRequest(StrictInput):
    target_name: str = Field(min_length=1, max_length=300)
    intended_use: str | None = Field(default=None, max_length=2000)


class InterfaceInput(StrictInput):
    kind: str = Field(min_length=1, max_length=100)
    details: dict[str, object] = Field(default_factory=dict)


class ThingKnowledgeReplace(StrictInput):
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    interfaces: list[InterfaceInput] = Field(default_factory=list, max_length=50)


class JobOut(APIModel):
    id: str
    kind: str
    status: str
    payload: dict[str, object]
    result: dict[str, object] | None
    attempts: int
    last_error: str | None
    expires_at: datetime | None


class BuildPlanRequest(StrictInput):
    goal: str | None = Field(default=None, max_length=4000)


class BuildPlanAccept(StrictInput):
    job_id: str
    solution_id: str
    revision: int


class PinInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    number: str | None = Field(default=None, max_length=40)
    electrical_type: Literal[
        "power_in",
        "power_out",
        "ground",
        "input",
        "output",
        "bidirectional",
        "open_drain",
        "passive",
        "no_connect",
    ] = "passive"
    alternate_functions: list[str] = Field(default_factory=list, max_length=30)
    restrictions: str | None = Field(default=None, max_length=2000)
    details: dict[str, object] = Field(default_factory=dict)
    source_ref: str | None = Field(default=None, max_length=600)
    verification_state: Literal["unverified", "accepted", "verified"] = "unverified"


class PinOut(APIModel):
    id: str
    name: str
    role: str
    number: str | None
    electrical_type: str
    alternate_functions: list[str]
    restrictions: str | None
    details: dict[str, object]
    source_ref: str | None
    verification_state: str


class PinoutReplace(StrictInput):
    pins: list[PinInput] = Field(min_length=1, max_length=300)


class SchematicRequest(StrictInput):
    notes: str | None = Field(default=None, max_length=2000)


class SchematicAccept(StrictInput):
    job_id: str
    revision: int


class AIQuery(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


class AIAnswer(BaseModel):
    status: Literal["disabled", "ready"]
    answer: str
    evidence: list[dict[str, object]] = Field(default_factory=list)
