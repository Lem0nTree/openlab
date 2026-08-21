import logging
from datetime import UTC, datetime
from hashlib import sha256
from http import HTTPStatus
from io import BytesIO
from secrets import token_urlsafe

import qrcode
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import Response as FastAPIResponse
from qrcode.image.svg import SvgPathImage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import (
    Allocation,
    InboxCandidate,
    InboxItem,
    Job,
    Lab,
    Location,
    Membership,
    Project,
    ProviderConfig,
    Requirement,
    SessionToken,
    StockBalance,
    TechnicalFact,
    Thing,
    ThingAlias,
    User,
)
from .providers import ProviderError, encrypt_secret, is_local_endpoint
from .schemas import (
    AIAnswer,
    AIQuery,
    AllocationCreate,
    AllocationRecover,
    BalanceOut,
    CompatibilityRequest,
    CompatibilityResult,
    FactCreate,
    InboxCandidateOut,
    InboxCapture,
    InboxConfirm,
    InboxOut,
    LabOut,
    LocationCreate,
    LocationOut,
    LoginRequest,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProviderConfigInput,
    ProviderConfigOut,
    ProviderModelsOut,
    RequirementCreate,
    SetupRequest,
    StockMovementOut,
    StockMutation,
    ThingCreate,
    ThingOut,
    ThingPatch,
    UserOut,
)
from .security import create_session, current_user, hasher, require_csrf
from .services import (
    active_provider,
    apply_movement,
    audit,
    available_quantity,
    compatible_things,
    create_thing,
    get_lab_location,
    get_lab_thing,
    lab_for_user,
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
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return problem(exc.status_code, HTTPStatus(exc.status_code).phrase, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
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
        "enabled": config.enabled,
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
    return db.get(Lab, lab_for_user(db, user))


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
            capabilities={"chat": True, "vision": "model-dependent", "audio": "endpoint-dependent"},
            enabled=payload.enabled,
        )
        db.add(config)
    else:
        config.base_url = payload.base_url.rstrip("/")
        config.model = payload.model
        config.enabled = payload.enabled
    if payload.api_key is not None:
        try:
            config.secret_ciphertext = (
                encrypt_secret(payload.api_key, settings.encryption_key) if payload.api_key else None
            )
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.flush()
    audit(db, user, "ai.provider_configured", "provider_config", config.id, enabled=config.enabled)
    db.commit()
    return provider_out(config)


@app.get("/api/v1/ai/provider/models", response_model=ProviderModelsOut)
def provider_models(
    user: User = Depends(require_owner), db: Session = Depends(get_db)
) -> dict[str, object]:
    try:
        provider, config = active_provider(db, lab_for_user(db, user))
        if not provider or not config:
            raise HTTPException(status_code=409, detail="Enable and save an AI provider first")
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
def location_qr(location_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> FastAPIResponse:
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


def stock_endpoint(kind: str):
    def endpoint(
        payload: StockMutation,
        idempotency_key: str = Depends(require_idempotency),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> StockMovementOut:
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
    "/api/v1/inbox/{inbox_id}/process", response_model=InboxOut, status_code=202,
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
    if item.status in {"committed", "confirmed", "cancelled"}:
        raise HTTPException(status_code=409, detail="This Inbox item cannot be processed again")
    item.status = "queued"
    item.error = None
    job = Job(lab_id=item.lab_id, kind="inbox.process", payload={"inbox_id": item.id})
    db.add(job)
    audit(db, user, "inbox.queued", "inbox_item", item.id, job_id=job.id)
    db.commit()
    return item


@app.post(
    "/api/v1/inbox/{inbox_id}/confirm",
    response_model=StockMovementOut,
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
def confirm_inbox(
    inbox_id: str,
    payload: InboxConfirm,
    idempotency_key: str = Depends(require_idempotency),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StockMovementOut:
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.id == inbox_id, InboxItem.lab_id == lab_for_user(db, user)
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    if item.status == "committed":
        raise HTTPException(status_code=409, detail="Inbox item was already committed")
    thing = (
        get_lab_thing(db, user, payload.existing_thing_id)
        if payload.existing_thing_id
        else create_thing(
            db,
            user,
            name=payload.candidate.name,
            category=payload.candidate.category,
            manufacturer=None,
            mpn=None,
            metadata={},
            aliases=[],
        )
    )
    item.status = "confirmed"
    movement = apply_movement(
        db,
        user,
        thing_id=thing.id,
        quantity=payload.candidate.quantity,
        movement_type="receive",
        idempotency_key=idempotency_key,
        to_location_id=payload.location_id,
        note=f"Inbox {item.id}",
    )
    item.status = "committed"
    audit(db, user, "inbox.committed", "inbox_item", item.id, thing_id=thing.id)
    db.commit()
    return movement


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
        lab_id=lab_for_user(db, user), name=payload.name, description=payload.description
    )
    db.add(item)
    db.flush()
    audit(db, user, "project.created", "project", item.id)
    db.commit()
    return item


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
) -> StockMovementOut:
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
    db.commit()
    return {"id": item.id}


@app.post("/api/v1/knowledge/compatible", response_model=list[CompatibilityResult])
def compatible(
    payload: CompatibilityRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return compatible_things(
        db, user, payload.required_capabilities, payload.required_interfaces, payload.minimum_facts
    )


@app.post("/api/v1/ai/query", response_model=AIAnswer)
def ai_query(_: AIQuery, user: User = Depends(current_user)) -> AIAnswer:
    _ = user
    return AIAnswer(
        status="disabled",
        answer="AI is disabled. Inventory and compatibility features remain available locally.",
    )
