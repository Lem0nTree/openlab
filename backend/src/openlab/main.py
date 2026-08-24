import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from http import HTTPStatus
from io import BytesIO
from secrets import token_urlsafe

import qrcode  # type: ignore[import-untyped]
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import Response as FastAPIResponse
from qrcode.image.svg import SvgPathImage  # type: ignore[import-untyped]
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .enrichment import enrich_thing, queue_thing_enrichment
from .intelligence import accept_build_plan, queue_thing_embedding, search_inventory
from .models import (
    Allocation,
    Capability,
    InboxCandidate,
    InboxItem,
    Job,
    Lab,
    Location,
    Membership,
    Pin,
    Project,
    ProviderConfig,
    Requirement,
    SessionToken,
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
    encrypt_secret,
    is_local_endpoint,
)
from .schemas import (
    AIAnswer,
    AIQuery,
    AllocationCreate,
    AllocationRecover,
    BalanceOut,
    BuildPlanAccept,
    BuildPlanRequest,
    CompatibilityRequest,
    CompatibilityResult,
    FactCreate,
    InboxCandidateBatchConfirm,
    InboxCandidateConfirm,
    InboxCandidateInput,
    InboxCandidateOut,
    InboxCandidatePatch,
    InboxCandidateReceive,
    InboxCapture,
    InboxEnrichURL,
    InboxOut,
    JobOut,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    LabOut,
    LocationCreate,
    LocationOut,
    LoginRequest,
    PinOut,
    PinoutReplace,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
    ProviderConfigInput,
    ProviderConfigOut,
    ProviderModelsOut,
    RequirementCreate,
    SchematicAccept,
    SchematicRequest,
    SetupRequest,
    StockMovementOut,
    StockMutation,
    ThingCreate,
    ThingKnowledgeReplace,
    ThingOut,
    ThingPatch,
    UserOut,
)
from .schematics import accept_schematic, export_kicad_schematic
from .security import create_session, current_user, hasher, require_csrf
from .services import (
    active_provider,
    apply_movement,
    audit,
    available_quantity,
    compatible_things,
    create_thing,
    enrich_product_url,
    get_lab_location,
    get_lab_thing,
    lab_for_user,
    refresh_inbox_status,
    save_upload,
)

settings = get_settings()
bootstrap_token = settings.setup_token or token_urlsafe(24)
logger = logging.getLogger("openlab")

app = FastAPI(
    title="OpenLab API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
    docs_url=None,
    redoc_url=None,
)


def problem(code: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"type": "about:blank", "title": title, "status": code, "detail": detail},
        media_type="application/problem+json",
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return problem(exc.status_code, HTTPStatus(exc.status_code).phrase, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return problem(422, "Validation failed", str(exc.errors()))


def require_idempotency(value: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    if not value or len(value) > 128:
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key header is required")
    return value


def require_owner(user: User = Depends(current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="Only the lab owner may configure AI")
    return user


def provider_out(config: ProviderConfig) -> dict[str, object]:
    return {
        "id": config.id,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "embedding_model": config.embedding_model,
        "enabled": config.enabled,
        "embeddings_enabled": config.embeddings_enabled,
        "has_api_key": config.secret_ciphertext is not None,
        "egress": "local" if is_local_endpoint(config.base_url) else "external",
    }


@app.on_event("startup")
def startup() -> None:
    if not settings.setup_token:
        logger.warning("OpenLab one-time owner setup token: %s", bootstrap_token)


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    db.execute(select(1))
    return {"status": "healthy", "time": datetime.now(UTC).isoformat()}


@app.get("/api/v1/setup")
def setup_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"setup_required": db.scalar(select(func.count(User.id))) == 0}


@app.post("/api/v1/setup", response_model=LabOut, status_code=201)
def setup(payload: SetupRequest, db: Session = Depends(get_db)) -> Lab:
    if db.scalar(select(func.count(User.id))) != 0:
        raise HTTPException(status_code=409, detail="OpenLab is already configured")
    if payload.token != bootstrap_token:
        raise HTTPException(status_code=403, detail="Invalid setup token")
    lab = Lab(name=payload.lab_name)
    owner = User(
        email=payload.email.lower(),
        display_name=payload.display_name,
        password_hash=hasher.hash(payload.password),
        is_owner=True,
    )
    db.add_all([lab, owner])
    db.flush()
    db.add(Membership(lab_id=lab.id, user_id=owner.id, role="owner"))
    db.commit()
    return lab


@app.post("/api/v1/session", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    try:
        valid = bool(user and hasher.verify(user.password_hash, payload.password))
    except VerifyMismatchError:
        valid = False
    if not valid or not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    raw, csrf = create_session(db, user)
    db.commit()
    response.set_cookie(
        "openlab_session", raw, httponly=True, samesite="lax", max_age=settings.session_hours * 3600
    )
    response.set_cookie(
        "openlab_csrf", csrf, httponly=False, samesite="lax", max_age=settings.session_hours * 3600
    )
    return user


@app.delete("/api/v1/session", status_code=204, dependencies=[Depends(require_csrf)])
def logout(
    response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    for item in db.scalars(select(SessionToken).where(SessionToken.user_id == user.id)).all():
        db.delete(item)
    db.commit()
    response.delete_cookie("openlab_session")
    response.delete_cookie("openlab_csrf")
    return response


@app.get("/api/v1/session", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@app.get("/api/v1/lab", response_model=LabOut)
def lab(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Lab:
    value = db.get(Lab, lab_for_user(db, user))
    if value is None:
        raise HTTPException(status_code=404, detail="Lab not found")
    return value


@app.get("/api/v1/ai/provider", response_model=ProviderConfigOut | None)
def get_provider_config(
    user: User = Depends(require_owner), db: Session = Depends(get_db)
) -> dict[str, object] | None:
    config = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.lab_id == lab_for_user(db, user))
        .order_by(ProviderConfig.updated_at.desc())
    )
    return provider_out(config) if config else None


@app.put(
    "/api/v1/ai/provider", response_model=ProviderConfigOut, dependencies=[Depends(require_csrf)]
)
def save_provider_config(
    payload: ProviderConfigInput,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not payload.base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Provider endpoint must use http or https")
    if payload.embeddings_enabled and not payload.embedding_model:
        raise HTTPException(
            status_code=422, detail="Choose an embedding model before enabling semantic retrieval"
        )
    lab_id = lab_for_user(db, user)
    config = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.lab_id == lab_id)
        .order_by(ProviderConfig.updated_at.desc())
    )
    if not config:
        config = ProviderConfig(
            lab_id=lab_id,
            provider="openai-compatible",
            base_url=payload.base_url.rstrip("/"),
            model=payload.model,
            embedding_model=payload.embedding_model,
            capabilities={"chat": True, "vision": "model-dependent", "audio": "endpoint-dependent"},
            enabled=payload.enabled,
            embeddings_enabled=payload.embeddings_enabled,
        )
        db.add(config)
    else:
        config.base_url = payload.base_url.rstrip("/")
        config.model = payload.model
        config.embedding_model = payload.embedding_model
        config.enabled = payload.enabled
        config.embeddings_enabled = payload.embeddings_enabled
    if payload.api_key is not None:
        try:
            config.secret_ciphertext = (
                encrypt_secret(payload.api_key, settings.encryption_key)
                if payload.api_key
                else None
            )
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    if config.embeddings_enabled and config.embedding_model:
        for thing_id in db.scalars(
            select(Thing.id).where(Thing.lab_id == lab_id, Thing.archived_at.is_(None))
        ).all():
            queue_thing_embedding(db, lab_id, thing_id)
    audit(db, user, "ai.provider_configured", "provider_config", config.id, enabled=config.enabled)
    db.commit()
    return provider_out(config)


@app.get("/api/v1/ai/provider/models", response_model=ProviderModelsOut)
def provider_models(
    user: User = Depends(require_owner), db: Session = Depends(get_db)
) -> dict[str, object]:
    try:
        config = db.scalar(
            select(ProviderConfig)
            .where(ProviderConfig.lab_id == lab_for_user(db, user))
            .order_by(ProviderConfig.updated_at.desc())
        )
        if not config:
            raise HTTPException(status_code=409, detail="Save an AI provider first")
        provider = OpenAICompatibleProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=decrypt_secret(config.secret_ciphertext, settings.encryption_key),
        )
        return {
            "models": provider.list_models(),
            "egress": "local" if is_local_endpoint(config.base_url) else "external",
        }
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/things", response_model=list[ThingOut])
def list_things(
    q: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Thing]:
    query = (
        select(Thing)
        .where(Thing.lab_id == lab_for_user(db, user), Thing.archived_at.is_(None))
        .order_by(Thing.name)
    )
    if q:
        term = f"%{q}%"
        query = (
            query.outerjoin(ThingAlias)
            .where(Thing.name.ilike(term) | Thing.mpn.ilike(term) | ThingAlias.value.ilike(term))
            .distinct()
        )
    return list(db.scalars(query).all())


@app.post(
    "/api/v1/things", response_model=ThingOut, status_code=201, dependencies=[Depends(require_csrf)]
)
def add_thing(
    payload: ThingCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Thing:
    item = create_thing(
        db,
        user,
        name=payload.name,
        category=payload.category,
        manufacturer=payload.manufacturer,
        mpn=payload.mpn,
        metadata=payload.metadata,
        aliases=payload.aliases,
    )
    queue_thing_embedding(db, item.lab_id, item.id)
    db.commit()
    return item


@app.get("/api/v1/things/{thing_id}", response_model=ThingOut)
def get_thing(
    thing_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Thing:
    return get_lab_thing(db, user, thing_id)


@app.patch(
    "/api/v1/things/{thing_id}", response_model=ThingOut, dependencies=[Depends(require_csrf)]
)
def patch_thing(
    thing_id: str,
    payload: ThingPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Thing:
    item = get_lab_thing(db, user, thing_id)
    if item.revision != payload.revision:
        raise HTTPException(status_code=409, detail="Thing was updated elsewhere; reload and retry")
    for field, value in payload.model_dump(exclude={"revision"}, exclude_unset=True).items():
        setattr(item, "metadata_json" if field == "metadata" else field, value)
    item.revision += 1
    queue_thing_embedding(db, item.lab_id, item.id)
    audit(db, user, "thing.updated", "thing", item.id)
    db.commit()
    return item


@app.delete("/api/v1/things/{thing_id}", status_code=204, dependencies=[Depends(require_csrf)])
def archive_thing(
    thing_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    item = get_lab_thing(db, user, thing_id)
    item.archived_at = datetime.now(UTC)
    item.revision += 1
    audit(db, user, "thing.archived", "thing", item.id)
    db.commit()


@app.get("/api/v1/locations", response_model=list[LocationOut])
def list_locations(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Location]:
    return list(
        db.scalars(
            select(Location)
            .where(Location.lab_id == lab_for_user(db, user), Location.archived_at.is_(None))
            .order_by(Location.name)
        ).all()
    )


@app.post(
    "/api/v1/locations",
    response_model=LocationOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def add_location(
    payload: LocationCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Location:
    if payload.parent_id:
        get_lab_location(db, user, payload.parent_id)
    item = Location(lab_id=lab_for_user(db, user), name=payload.name, parent_id=payload.parent_id)
    db.add(item)
    db.flush()
    audit(db, user, "location.created", "location", item.id)
    db.commit()
    return item


@app.get("/api/v1/locations/code/{public_code}", response_model=LocationOut)
def lookup_location(
    public_code: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Location:
    item = db.scalar(
        select(Location).where(
            Location.public_code == public_code, Location.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Location not found")
    return item


@app.get("/api/v1/locations/{location_id}/qr.svg", response_class=FastAPIResponse)
def location_qr(
    location_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> FastAPIResponse:
    location = get_lab_location(db, user, location_id)
    output = BytesIO()
    qrcode.make(
        f"openlab://location/{location.public_code}", image_factory=SvgPathImage, border=2
    ).save(output)
    return FastAPIResponse(content=output.getvalue(), media_type="image/svg+xml")


@app.get("/api/v1/inventory/balances", response_model=list[BalanceOut])
def balances(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[StockBalance]:
    return list(
        db.scalars(
            select(StockBalance)
            .join(Thing)
            .where(Thing.lab_id == lab_for_user(db, user), StockBalance.quantity > 0)
        ).all()
    )


def stock_endpoint(kind: str) -> Callable[..., StockMovement]:
    def endpoint(
        payload: StockMutation,
        idempotency_key: str = Depends(require_idempotency),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> StockMovement:
        if kind == "receive" and not payload.to_location_id:
            raise HTTPException(status_code=422, detail="Receiving stock needs a destination")
        if kind == "move" and (not payload.from_location_id or not payload.to_location_id):
            raise HTTPException(
                status_code=422, detail="Moving stock needs a source and destination"
            )
        if kind == "consume" and not payload.from_location_id:
            raise HTTPException(status_code=422, detail="Consuming stock needs a source")
        item = apply_movement(
            db,
            user,
            thing_id=payload.thing_id,
            quantity=payload.quantity,
            movement_type=kind,
            idempotency_key=idempotency_key,
            from_location_id=payload.from_location_id,
            to_location_id=payload.to_location_id,
            note=payload.note,
        )
        db.commit()
        return item

    return endpoint


app.post(
    "/api/v1/inventory/receive",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)(stock_endpoint("receive"))
app.post(
    "/api/v1/inventory/move",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)(stock_endpoint("move"))
app.post(
    "/api/v1/inventory/consume",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)(stock_endpoint("consume"))


@app.get("/api/v1/inbox", response_model=list[InboxOut])
def list_inbox(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[InboxItem]:
    return list(
        db.scalars(
            select(InboxItem)
            .where(InboxItem.lab_id == lab_for_user(db, user))
            .order_by(InboxItem.created_at.desc())
        ).all()
    )


@app.get("/api/v1/inbox/{inbox_id}/candidates", response_model=list[InboxCandidateOut])
def list_inbox_candidates(
    inbox_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[InboxCandidate]:
    inbox = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    return list(
        db.scalars(select(InboxCandidate).where(InboxCandidate.inbox_item_id == inbox.id)).all()
    )


@app.post(
    "/api/v1/inbox", response_model=InboxOut, status_code=201, dependencies=[Depends(require_csrf)]
)
def capture_inbox(
    payload: InboxCapture, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> InboxItem:
    item = InboxItem(
        lab_id=lab_for_user(db, user),
        created_by=user.id,
        input_type=payload.input_type,
        text=payload.text,
        status="captured",
    )
    db.add(item)
    db.flush()
    audit(db, user, "inbox.captured", "inbox_item", item.id, input_type=payload.input_type)
    db.commit()
    return item


@app.get("/api/v1/projects/{project_id}", response_model=ProjectDetailOut)
def get_project(
    project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "revision": project.revision,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "design_json": project.design_json,
        "requirements": list(
            db.scalars(select(Requirement).where(Requirement.project_id == project.id)).all()
        ),
        "allocations": list(
            db.scalars(select(Allocation).where(Allocation.project_id == project.id)).all()
        ),
    }


@app.post(
    "/api/v1/inbox/{inbox_id}/attachments", status_code=201, dependencies=[Depends(require_csrf)]
)
async def upload_inbox_attachment(
    inbox_id: str,
    upload: UploadFile,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    attachment = await save_upload(db, user, inbox_id, upload)
    db.commit()
    return {"id": attachment.id, "sha256": attachment.sha256, "size_bytes": attachment.size_bytes}


@app.post(
    "/api/v1/inbox/{inbox_id}/process",
    response_model=InboxOut,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def process_inbox(
    inbox_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> InboxItem:
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    if item.status in {"committed", "cancelled"}:
        raise HTTPException(status_code=409, detail="This Inbox item cannot be processed again")
    item.status = "queued"
    item.error = None
    job = Job(lab_id=item.lab_id, kind="inbox.process", payload={"inbox_id": item.id})
    db.add(job)
    audit(db, user, "inbox.queued", "inbox_item", item.id, job_id=job.id)
    db.commit()
    return item


def inbox_candidate_for_user(
    db: Session, user: User, inbox_id: str, candidate_id: str
) -> tuple[InboxItem, InboxCandidate]:
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    candidate = db.scalar(
        select(InboxCandidate).where(
            InboxCandidate.id == candidate_id, InboxCandidate.inbox_item_id == item.id
        )
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Inbox candidate not found")
    return item, candidate


def update_candidate_proposal(
    candidate: InboxCandidate, payload: InboxCandidatePatch
) -> InboxCandidate:
    if candidate.status != "proposed":
        raise HTTPException(
            status_code=409,
            detail="Only proposed candidates can be edited; edit the inventory item instead",
        )
    if payload.name is not None:
        candidate.name = payload.name
    if payload.quantity is not None:
        candidate.quantity = payload.quantity
    if payload.category is not None:
        candidate.category = payload.category
    if "description" in payload.model_fields_set:
        provenance = dict(candidate.provenance)
        if payload.description is None:
            provenance.pop("description", None)
        else:
            provenance["description"] = payload.description
        candidate.provenance = provenance
    candidate.revision = (candidate.revision or 0) + 1
    return candidate


@app.patch(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}",
    response_model=InboxCandidateOut,
    dependencies=[Depends(require_csrf)],
)
def patch_inbox_candidate(
    inbox_id: str,
    candidate_id: str,
    payload: InboxCandidatePatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InboxCandidate:
    _item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    update_candidate_proposal(candidate, payload)
    audit(db, user, "inbox.candidate_updated", "inbox_candidate", candidate.id)
    db.commit()
    return candidate


def confirm_candidate_identity(
    db: Session,
    user: User,
    item: InboxItem,
    candidate: InboxCandidate,
    payload: InboxCandidateConfirm,
) -> InboxCandidate:
    if candidate.status == "ignored":
        raise HTTPException(status_code=409, detail="Ignored candidates cannot be confirmed")
    supplied_values = any(
        value is not None
        for value in (
            payload.name,
            payload.description,
            payload.quantity,
            payload.category,
            payload.existing_thing_id,
        )
    )
    if candidate.status in {"confirmed", "received"}:
        if not supplied_values:
            return candidate
        raise HTTPException(
            status_code=409,
            detail="This candidate is already confirmed; edit the inventory item instead",
        )
    if (
        candidate.status == "proposed"
        and candidate.identity_confidence == "unresolved"
        and not candidate.product_url
    ):
        raise HTTPException(
            status_code=409,
            detail="Unable to parse this item; provide a product link before confirmation",
        )
    if payload.name is not None:
        candidate.name = payload.name
    if payload.quantity is not None:
        candidate.quantity = payload.quantity
    if payload.category is not None:
        candidate.category = payload.category
    provenance = dict(candidate.provenance)
    if "description" in payload.model_fields_set:
        if payload.description is None:
            provenance.pop("description", None)
        else:
            provenance["description"] = payload.description
        candidate.provenance = provenance
    description = provenance.get("description")
    thing = (
        get_lab_thing(db, user, payload.existing_thing_id)
        if payload.existing_thing_id
        else create_thing(
            db,
            user,
            name=candidate.name,
            category=candidate.category,
            manufacturer=None,
            mpn=None,
            metadata={"description": description} if isinstance(description, str) else {},
            aliases=[],
        )
    )
    candidate.thing_id = thing.id
    candidate.status = "confirmed"
    queue_thing_enrichment(db, thing.lab_id, thing.id)
    queue_thing_embedding(db, thing.lab_id, thing.id)
    refresh_inbox_status(db, item)
    audit(db, user, "inbox.candidate_confirmed", "inbox_candidate", candidate.id, thing_id=thing.id)
    return candidate


@app.post(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}/confirm",
    response_model=InboxCandidateOut,
    dependencies=[Depends(require_csrf)],
)
def confirm_inbox_candidate(
    inbox_id: str,
    candidate_id: str,
    payload: InboxCandidateConfirm,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InboxCandidate:
    item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    result = confirm_candidate_identity(db, user, item, candidate, payload)
    db.commit()
    return result


@app.post(
    "/api/v1/inbox/{inbox_id}/confirm-batch",
    response_model=list[InboxCandidateOut],
    dependencies=[Depends(require_csrf)],
)
def confirm_inbox_batch(
    inbox_id: str,
    payload: InboxCandidateBatchConfirm,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[InboxCandidate]:
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    candidates = list(
        db.scalars(
            select(InboxCandidate).where(
                InboxCandidate.inbox_item_id == item.id,
                InboxCandidate.id.in_(payload.candidate_ids),
            )
        ).all()
    )
    if len(candidates) != len(set(payload.candidate_ids)):
        raise HTTPException(status_code=404, detail="One or more Inbox candidates were not found")
    for candidate in candidates:
        confirm_candidate_identity(db, user, item, candidate, InboxCandidateConfirm())
    db.commit()
    return candidates


@app.post(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}/ignore",
    response_model=InboxCandidateOut,
    dependencies=[Depends(require_csrf)],
)
def ignore_inbox_candidate(
    inbox_id: str,
    candidate_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InboxCandidate:
    item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    if candidate.status == "received":
        raise HTTPException(status_code=409, detail="Received candidates cannot be ignored")
    candidate.status = "ignored"
    refresh_inbox_status(db, item)
    audit(db, user, "inbox.candidate_ignored", "inbox_candidate", candidate.id)
    db.commit()
    return candidate


@app.delete(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
def delete_inbox_candidate(
    inbox_id: str,
    candidate_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    if candidate.status != "proposed":
        raise HTTPException(
            status_code=409,
            detail="Only proposed candidates can be removed; edit the inventory item instead",
        )
    candidate.status = "ignored"
    refresh_inbox_status(db, item)
    audit(db, user, "inbox.candidate_removed", "inbox_candidate", candidate.id)
    db.commit()
    return Response(status_code=204)


@app.post(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}/receive",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def receive_inbox_candidate(
    inbox_id: str,
    candidate_id: str,
    payload: InboxCandidateReceive,
    idempotency_key: str = Depends(require_idempotency),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StockMovement:
    item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    if candidate.status not in {"confirmed", "received"} or not candidate.thing_id:
        raise HTTPException(
            status_code=409, detail="Confirm this candidate's identity before receiving it"
        )
    quantity = payload.quantity or candidate.quantity
    movement = apply_movement(
        db,
        user,
        thing_id=candidate.thing_id,
        quantity=quantity,
        movement_type="receive",
        idempotency_key=idempotency_key,
        to_location_id=payload.location_id,
        note=f"Inbox {item.id} candidate {candidate.id}",
    )
    candidate.status = "received"
    refresh_inbox_status(db, item)
    audit(
        db,
        user,
        "inbox.candidate_received",
        "inbox_candidate",
        candidate.id,
        thing_id=candidate.thing_id,
    )
    db.commit()
    return movement


@app.post(
    "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}/enrich-url",
    response_model=InboxCandidateOut,
    dependencies=[Depends(require_csrf)],
)
def enrich_inbox_candidate_url(
    inbox_id: str,
    candidate_id: str,
    payload: InboxEnrichURL,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> InboxCandidate:
    item, candidate = inbox_candidate_for_user(db, user, inbox_id, candidate_id)
    if candidate.status != "proposed":
        raise HTTPException(status_code=409, detail="This candidate cannot be enriched")
    result = enrich_product_url(payload.url)
    candidate.product_url = str(result["normalized_url"])
    provenance = dict(candidate.provenance)
    provenance["product_link"] = result
    candidate.provenance = provenance
    proposal = result.get("proposal", {})
    if isinstance(proposal, dict):
        page_text = "\n".join(
            str(value) for value in (proposal.get("name"), proposal.get("description")) if value
        )
        provider, config = active_provider(db, item.lab_id)
        if provider and page_text:
            try:
                raw = provider.extract_inbox(page_text)
                values = raw.get("candidates", [])
                if not isinstance(values, list) or not values:
                    raise ValueError("candidates is empty")
                normalized = InboxCandidateInput.model_validate(values[0])
            except (ProviderError, ValueError) as first_error:
                try:
                    repaired = provider.extract_inbox(
                        page_text, repair_error=str(first_error)[:1200]
                    )
                    values = repaired.get("candidates", [])
                    if not isinstance(values, list) or not values:
                        raise ValueError("candidates is empty")
                    normalized = InboxCandidateInput.model_validate(values[0])
                except (ProviderError, ValueError):
                    normalized = None
            if normalized:
                candidate.name = normalized.name
                candidate.category = normalized.category
                candidate.identity_confidence = normalized.identity_confidence
                provenance["description"] = normalized.description
                provenance["observations"] = normalized.observations
                provenance["product_link_model"] = config.model if config else None
                candidate.provenance = provenance
    audit(db, user, "inbox.candidate_enriched", "inbox_candidate", candidate.id)
    db.commit()
    return candidate


@app.get("/api/v1/projects", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Project]:
    return list(
        db.scalars(
            select(Project).where(Project.lab_id == lab_for_user(db, user)).order_by(Project.name)
        ).all()
    )


@app.post(
    "/api/v1/projects",
    response_model=ProjectOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def create_project(
    payload: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Project:
    item = Project(
        lab_id=lab_for_user(db, user),
        name=payload.name,
        description=payload.description,
        status="pending",
    )
    db.add(item)
    db.flush()
    audit(db, user, "project.created", "project", item.id)
    db.commit()
    return item


@app.patch(
    "/api/v1/projects/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(require_csrf)],
)
def update_project_status(
    project_id: str,
    payload: ProjectUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Project:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
        .with_for_update(of=Project)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    changed_fields: list[str] = []
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Project name cannot be blank")
        project.name = name
        changed_fields.append("name")
    if "description" in payload.model_fields_set:
        project.description = (
            payload.description.strip() if payload.description and payload.description.strip() else None
        )
        changed_fields.append("description")
    if payload.status is not None:
        project.status = payload.status
        changed_fields.append("status")
    if not changed_fields:
        raise HTTPException(status_code=400, detail="At least one project field is required")
    project.revision += 1
    if changed_fields == ["status"]:
        audit(db, user, "project.status_updated", "project", project.id, status=payload.status)
    else:
        audit(db, user, "project.updated", "project", project.id, fields=changed_fields)
    db.commit()
    return project


@app.post(
    "/api/v1/projects/{project_id}/requirements",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def add_requirement(
    project_id: str,
    payload: RequirementCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
        .with_for_update(of=Project)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    item = Requirement(project_id=project.id, **payload.model_dump())
    db.add(item)
    project.revision += 1
    db.commit()
    return {"id": item.id}


@app.post(
    "/api/v1/projects/{project_id}/allocations",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def allocate_project_stock(
    project_id: str,
    payload: AllocationCreate,
    idempotency_key: str = Depends(require_idempotency),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
        .with_for_update(of=Project)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    existing = db.scalar(
        select(Allocation).where(
            Allocation.project_id == project.id, Allocation.idempotency_key == idempotency_key
        )
    )
    if existing:
        return {"id": existing.id, "state": existing.state}
    get_lab_thing(db, user, payload.thing_id)
    get_lab_location(db, user, payload.location_id)
    if available_quantity(db, payload.thing_id, lock=True) < payload.quantity:
        raise HTTPException(status_code=409, detail="Insufficient available stock")
    allocation = Allocation(
        project_id=project.id, idempotency_key=idempotency_key, **payload.model_dump()
    )
    db.add(allocation)
    db.flush()
    if payload.state in {"in_use", "recoverable"}:
        apply_movement(
            db,
            user,
            thing_id=payload.thing_id,
            quantity=payload.quantity,
            movement_type="allocate",
            idempotency_key=sha256(
                f"allocation:{project.id}:{idempotency_key}".encode()
            ).hexdigest(),
            from_location_id=payload.location_id,
            note=f"Project {project.id}",
        )
    audit(db, user, "project.allocated", "allocation", allocation.id, project_id=project.id)
    db.commit()
    return {"id": allocation.id, "state": allocation.state}


@app.post(
    "/api/v1/projects/{project_id}/allocations/{allocation_id}/recover",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def recover_project_stock(
    project_id: str,
    allocation_id: str,
    payload: AllocationRecover,
    idempotency_key: str = Depends(require_idempotency),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StockMovement:
    allocation = db.scalar(
        select(Allocation)
        .join(Project)
        .where(
            Allocation.id == allocation_id,
            Allocation.project_id == project_id,
            Project.lab_id == lab_for_user(db, user),
            Allocation.state.in_(["in_use", "recoverable"]),
        )
    )
    if not allocation:
        raise HTTPException(status_code=404, detail="Recoverable allocation not found")
    if payload.quantity > allocation.quantity:
        raise HTTPException(status_code=422, detail="Recovery quantity exceeds allocation")
    movement = apply_movement(
        db,
        user,
        thing_id=allocation.thing_id,
        quantity=payload.quantity,
        movement_type="recover",
        idempotency_key=idempotency_key,
        to_location_id=payload.location_id,
        note=f"Recovered from project {project_id}",
    )
    allocation.quantity -= payload.quantity
    if allocation.quantity == 0:
        allocation.state = "recovered"
    audit(db, user, "project.recovered", "allocation", allocation.id, project_id=project_id)
    db.commit()
    return movement


@app.post("/api/v1/things/{thing_id}/facts", status_code=201, dependencies=[Depends(require_csrf)])
def add_fact(
    thing_id: str,
    payload: FactCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    get_lab_thing(db, user, thing_id)
    item = TechnicalFact(thing_id=thing_id, **payload.model_dump())
    db.add(item)
    thing = get_lab_thing(db, user, thing_id)
    thing.revision += 1
    queue_thing_embedding(db, thing.lab_id, thing.id)
    db.commit()
    return {"id": item.id}


@app.post("/api/v1/knowledge/compatible", response_model=list[CompatibilityResult])
def compatible(
    payload: CompatibilityRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return compatible_things(
        db, user, payload.required_capabilities, payload.required_interfaces, payload.minimum_facts
    )


@app.post("/api/v1/knowledge/search", response_model=list[KnowledgeSearchResult])
def knowledge_search(
    payload: KnowledgeSearchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return search_inventory(db, lab_for_user(db, user), payload.query, payload.limit)


@app.put(
    "/api/v1/things/{thing_id}/knowledge",
    dependencies=[Depends(require_csrf)],
)
def replace_thing_knowledge(
    thing_id: str,
    payload: ThingKnowledgeReplace,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    thing = get_lab_thing(db, user, thing_id)
    for capability in db.scalars(select(Capability).where(Capability.thing_id == thing.id)).all():
        db.delete(capability)
    for interface in db.scalars(
        select(ThingInterface).where(ThingInterface.thing_id == thing.id)
    ).all():
        db.delete(interface)
    db.flush()
    capabilities = sorted({value.strip() for value in payload.capabilities if value.strip()})
    db.add_all([Capability(thing_id=thing.id, value=value) for value in capabilities])
    db.add_all(
        [ThingInterface(thing_id=thing.id, **value.model_dump()) for value in payload.interfaces]
    )
    thing.revision += 1
    queue_thing_embedding(db, thing.lab_id, thing.id)
    audit(
        db,
        user,
        "thing.knowledge_replaced",
        "thing",
        thing.id,
        capability_count=len(capabilities),
        interface_count=len(payload.interfaces),
    )
    db.commit()
    return {
        "capabilities": capabilities,
        "interfaces": [value.model_dump() for value in payload.interfaces],
    }


@app.get("/api/v1/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.lab_id == lab_for_user(db, user)))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/v1/projects/{project_id}/jobs", response_model=list[JobOut])
def list_project_jobs(
    project_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Job]:
    lab_id = lab_for_user(db, user)
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_id)
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return list(
        db.scalars(
            select(Job)
            .where(
                Job.lab_id == lab_id,
                Job.kind.in_(["project.plan", "project.schematic", "thing.enrich"]),
                Job.payload["project_id"].astext == project.id,
            )
            .order_by(Job.created_at.desc())
            .limit(20)
        ).all()
    )


@app.post(
    "/api/v1/projects/{project_id}/plan",
    response_model=JobOut,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def queue_build_plan(
    project_id: str,
    payload: BuildPlanRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Job:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    pending = db.scalars(
        select(Job).where(
            Job.lab_id == project.lab_id,
            Job.kind == "project.plan",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for job in pending:
        if str(job.payload.get("project_id", "")) == project.id:
            return job
    job = Job(
        lab_id=project.lab_id,
        kind="project.plan",
        payload={"project_id": project.id, "goal": payload.goal},
    )
    db.add(job)
    db.flush()
    audit(db, user, "project.plan_queued", "project", project.id, job_id=job.id)
    db.commit()
    return job


@app.post(
    "/api/v1/projects/{project_id}/plan/accept",
    response_model=ProjectDetailOut,
    dependencies=[Depends(require_csrf)],
)
def accept_project_plan(
    project_id: str,
    payload: BuildPlanAccept,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    lab_id = lab_for_user(db, user)
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.lab_id == lab_id)
        .with_for_update(of=Project)
    )
    job = db.scalar(select(Job).where(Job.id == payload.job_id, Job.lab_id == lab_id))
    if not project or not job:
        raise HTTPException(status_code=404, detail="Project or BUILD proposal not found")
    try:
        accept_build_plan(db, project, job, payload.solution_id, payload.revision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    selected_thing_ids = set(
        db.scalars(
            select(Requirement.selected_thing_id).where(
                Requirement.project_id == project.id,
                Requirement.selected_thing_id.is_not(None),
            )
        ).all()
    )
    for thing_id in selected_thing_ids:
        if thing_id:
            queue_thing_enrichment(db, project.lab_id, thing_id, project.id)
    project.status = "active"
    audit(
        db,
        user,
        "project.plan_accepted",
        "project",
        project.id,
        job_id=job.id,
        solution_id=payload.solution_id,
    )
    db.commit()
    return get_project(project.id, user, db)


@app.post(
    "/api/v1/projects/{project_id}/enrich",
    response_model=list[JobOut],
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def queue_project_enrichment(
    project_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Job]:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    thing_ids = set(
        db.scalars(
            select(Requirement.selected_thing_id).where(
                Requirement.project_id == project.id,
                Requirement.selected_thing_id.is_not(None),
            )
        ).all()
    )
    jobs = [
        queue_thing_enrichment(db, project.lab_id, thing_id, project.id)
        for thing_id in thing_ids
        if thing_id
        and not db.scalar(select(func.count(Pin.id)).where(Pin.thing_id == thing_id))
    ]
    audit(db, user, "project.enrichment_queued", "project", project.id, job_count=len(jobs))
    db.commit()
    return jobs


@app.get("/api/v1/things/{thing_id}/pins", response_model=list[PinOut])
def get_pinout(
    thing_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[Pin]:
    get_lab_thing(db, user, thing_id)
    return list(db.scalars(select(Pin).where(Pin.thing_id == thing_id).order_by(Pin.number)).all())


@app.put(
    "/api/v1/things/{thing_id}/pins",
    response_model=list[PinOut],
    dependencies=[Depends(require_csrf)],
)
def replace_pinout(
    thing_id: str,
    payload: PinoutReplace,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Pin]:
    thing = get_lab_thing(db, user, thing_id)
    for pin in db.scalars(select(Pin).where(Pin.thing_id == thing.id)).all():
        db.delete(pin)
    db.flush()
    pins = [Pin(thing_id=thing.id, **value.model_dump()) for value in payload.pins]
    db.add_all(pins)
    thing.revision += 1
    audit(db, user, "thing.pinout_replaced", "thing", thing.id, pin_count=len(pins))
    db.commit()
    return pins


@app.post(
    "/api/v1/projects/{project_id}/schematic",
    response_model=JobOut,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
def queue_schematic(
    project_id: str,
    payload: SchematicRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Job:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.design_json.get("solution"):
        raise HTTPException(status_code=409, detail="Accept a BUILD solution first")
    selected_count = db.scalar(
        select(func.count(Requirement.id)).where(
            Requirement.project_id == project.id,
            Requirement.selected_thing_id.is_not(None),
        )
    )
    if not selected_count:
        raise HTTPException(
            status_code=409,
            detail="This BUILD solution has no owned components to wire",
        )
    selected_thing_ids = set(
        db.scalars(
            select(Requirement.selected_thing_id).where(
                Requirement.project_id == project.id,
                Requirement.selected_thing_id.is_not(None),
            )
        ).all()
    )
    for thing_id in selected_thing_ids:
        if thing_id and not db.scalar(select(func.count(Pin.id)).where(Pin.thing_id == thing_id)):
            enrich_thing(db, project.lab_id, thing_id)
    job = Job(
        lab_id=project.lab_id,
        kind="project.schematic",
        payload={"project_id": project.id, "notes": payload.notes},
    )
    db.add(job)
    db.flush()
    audit(db, user, "project.schematic_queued", "project", project.id, job_id=job.id)
    db.commit()
    return job


@app.post(
    "/api/v1/projects/{project_id}/schematic/accept",
    response_model=ProjectDetailOut,
    dependencies=[Depends(require_csrf)],
)
def accept_project_schematic(
    project_id: str,
    payload: SchematicAccept,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    lab_id = lab_for_user(db, user)
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.lab_id == lab_id)
        .with_for_update(of=Project)
    )
    job = db.scalar(select(Job).where(Job.id == payload.job_id, Job.lab_id == lab_id))
    if not project or not job:
        raise HTTPException(status_code=404, detail="Project or schematic proposal not found")
    if (
        job.kind != "project.schematic"
        or job.status != "completed"
        or not job.result
        or str(job.payload.get("project_id", "")) != project.id
    ):
        raise HTTPException(status_code=409, detail="Schematic proposal is not ready")
    if job.expires_at and job.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Schematic proposal has expired")
    if int(str(job.result.get("project_revision", -1))) != project.revision:
        raise HTTPException(
            status_code=409, detail="Schematic proposal is stale; generate a new one"
        )
    try:
        accept_schematic(project, job.result, job.id, payload.revision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = dict(job.result)
    result["accepted_at"] = datetime.now(UTC).isoformat()
    job.result = result
    audit(db, user, "project.schematic_accepted", "project", project.id, job_id=job.id)
    db.commit()
    return get_project(project.id, user, db)


@app.get("/api/v1/projects/{project_id}/schematic.kicad_sch")
def download_project_schematic(
    project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> FastAPIResponse:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.lab_id == lab_for_user(db, user))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    schematic = project.design_json.get("schematic")
    if not isinstance(schematic, dict):
        raise HTTPException(status_code=409, detail="No accepted schematic is available")
    content = export_kicad_schematic(schematic)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-") or "openlab"
    return FastAPIResponse(
        content=content,
        media_type="application/x-kicad-schematic",
        headers={"Content-Disposition": f'attachment; filename="{filename}.kicad_sch"'},
    )


@app.post("/api/v1/ai/query", response_model=AIAnswer)
def ai_query(query: AIQuery, user: User = Depends(current_user)) -> AIAnswer:
    _ = (query, user)
    return AIAnswer(
        status="disabled",
        answer="AI is disabled. Inventory and compatibility features remain available locally.",
    )
