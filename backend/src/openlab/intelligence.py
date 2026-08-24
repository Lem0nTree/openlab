"""Canonical inventory profiles, hybrid retrieval, and bounded BUILD planning."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    Capability,
    Embedding,
    Job,
    Location,
    Project,
    ProviderConfig,
    Requirement,
    StockBalance,
    TechnicalFact,
    Thing,
    ThingAlias,
    ThingInterface,
)
from .providers import OpenAICompatibleProvider, ProviderError, decrypt_secret
from .services import available_quantity, canonical_profile


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildRole(StrictModel):
    role_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(default=Decimal(1), gt=0)
    priority: Literal["required", "recommended", "optional"] = "required"
    category: str | None = Field(default=None, max_length=120)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    required_interfaces: list[str] = Field(default_factory=list, max_length=20)
    minimum_facts: dict[str, Decimal] = Field(default_factory=dict)


class BuildBreakdown(StrictModel):
    summary: str = Field(min_length=1, max_length=600)
    roles: list[BuildRole] = Field(min_length=1, max_length=20)


def _provider_config(db: Session, lab_id: str) -> ProviderConfig | None:
    return db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.lab_id == lab_id)
        .order_by(ProviderConfig.updated_at.desc())
    )


def active_embedding_provider(
    db: Session, lab_id: str
) -> tuple[OpenAICompatibleProvider | None, ProviderConfig | None]:
    config = _provider_config(db, lab_id)
    if not config or not config.embeddings_enabled or not config.embedding_model:
        return None, config
    return (
        OpenAICompatibleProvider(
            base_url=config.base_url,
            model=config.embedding_model,
            api_key=decrypt_secret(config.secret_ciphertext, get_settings().encryption_key),
        ),
        config,
    )


def _valid_vector(values: object) -> list[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ProviderError("Embedding provider returned an empty vector")
    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProviderError("Embedding provider returned a non-numeric vector")
        number = float(value)
        if not math.isfinite(number):
            raise ProviderError("Embedding provider returned a non-finite vector")
        vector.append(number)
    return vector


def thing_profile(db: Session, thing: Thing) -> tuple[str, str]:
    aliases = db.scalars(select(ThingAlias.value).where(ThingAlias.thing_id == thing.id)).all()
    capabilities = db.scalars(select(Capability.value).where(Capability.thing_id == thing.id)).all()
    interfaces = db.scalars(
        select(ThingInterface.kind).where(ThingInterface.thing_id == thing.id)
    ).all()
    verified_facts = db.scalars(
        select(TechnicalFact).where(
            TechnicalFact.thing_id == thing.id,
            TechnicalFact.verification_state.in_(["accepted", "verified"]),
        )
    ).all()
    facts = []
    for fact in verified_facts:
        value = fact.value_text if fact.value_text is not None else fact.value_numeric
        if value is not None:
            facts.append(f"{fact.key} {value}{' ' + fact.unit if fact.unit else ''}")
    description = thing.metadata_json.get("description")
    return canonical_profile(
        name=thing.name,
        category=thing.category,
        description=str(description) if isinstance(description, str) else None,
        manufacturer=thing.manufacturer,
        mpn=thing.mpn,
        aliases=aliases,
        capabilities=capabilities,
        interfaces=interfaces,
        facts=facts,
    )


def embed_thing(db: Session, lab_id: str, thing_id: str) -> dict[str, object]:
    thing = db.scalar(
        select(Thing).where(
            Thing.id == thing_id, Thing.lab_id == lab_id, Thing.archived_at.is_(None)
        )
    )
    if not thing:
        raise ProviderError("Thing for embedding is unavailable")
    provider, config = active_embedding_provider(db, lab_id)
    if not provider or not config or not config.embedding_model:
        return {"status": "skipped", "reason": "embeddings_disabled", "thing_id": thing_id}
    profile, fingerprint = thing_profile(db, thing)
    existing = db.scalar(
        select(Embedding).where(
            Embedding.thing_id == thing.id,
            Embedding.purpose == "profile",
            Embedding.provider == config.provider,
            Embedding.model == config.embedding_model,
        )
    )
    if existing and existing.fingerprint == fingerprint:
        return {"status": "current", "thing_id": thing.id, "dimensions": existing.dimensions}
    rows = provider.embed([profile])
    if len(rows) != 1:
        raise ProviderError("Embedding provider returned an unexpected number of vectors")
    vector = _valid_vector(rows[0])
    if existing is None:
        existing = Embedding(
            lab_id=lab_id,
            thing_id=thing.id,
            purpose="profile",
            provider=config.provider,
            model=config.embedding_model,
            dimensions=len(vector),
            fingerprint=fingerprint,
            profile_text=profile,
            embedding_vector=vector,
        )
        db.add(existing)
    else:
        existing.dimensions = len(vector)
        existing.fingerprint = fingerprint
        existing.profile_text = profile
        existing.embedding_vector = vector
    db.flush()
    return {
        "status": "embedded",
        "thing_id": thing.id,
        "dimensions": len(vector),
        "profile": profile,
    }


def queue_thing_embedding(db: Session, lab_id: str, thing_id: str) -> Job | None:
    config = _provider_config(db, lab_id)
    if not config or not config.embeddings_enabled or not config.embedding_model:
        return None
    pending = db.scalars(
        select(Job).where(
            Job.lab_id == lab_id,
            Job.kind == "thing.embed",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    if any(str(job.payload.get("thing_id", "")) == thing_id for job in pending):
        return None
    job = Job(lab_id=lab_id, kind="thing.embed", payload={"thing_id": thing_id})
    db.add(job)
    return job


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _token_score(query: str, values: list[str]) -> float:
    query_tokens = set(_normalize(query).split())
    if not query_tokens:
        return 0.0
    best = 0.0
    for value in values:
        value_tokens = set(_normalize(value).split())
        if value_tokens:
            best = max(best, len(query_tokens & value_tokens) / len(query_tokens | value_tokens))
    return best


def search_inventory(
    db: Session, lab_id: str, query: str, limit: int = 10
) -> list[dict[str, object]]:
    things = list(
        db.scalars(select(Thing).where(Thing.lab_id == lab_id, Thing.archived_at.is_(None))).all()
    )
    if not things:
        return []
    thing_ids = [thing.id for thing in things]
    aliases_by_thing: dict[str, list[str]] = {thing_id: [] for thing_id in thing_ids}
    for thing_id, value in db.execute(
        select(ThingAlias.thing_id, ThingAlias.value).where(ThingAlias.thing_id.in_(thing_ids))
    ).all():
        aliases_by_thing[thing_id].append(value)

    semantic: dict[str, float] = {}
    try:
        provider, config = active_embedding_provider(db, lab_id)
    except ProviderError:
        provider, config = None, None
    if provider and config and config.embedding_model:
        try:
            rows = provider.embed([query])
            if len(rows) == 1:
                vector = _valid_vector(rows[0])
                distances = db.execute(
                    select(
                        Embedding.thing_id,
                        Embedding.embedding_vector.cosine_distance(vector).label("distance"),
                    ).where(
                        Embedding.lab_id == lab_id,
                        Embedding.purpose == "profile",
                        Embedding.provider == config.provider,
                        Embedding.model == config.embedding_model,
                        Embedding.dimensions == len(vector),
                    )
                ).all()
                semantic = {
                    thing_id: max(0.0, min(1.0, 1.0 - float(distance)))
                    for thing_id, distance in distances
                    if distance is not None
                }
        except ProviderError:
            semantic = {}

    balances = db.execute(
        select(StockBalance.thing_id, Location.name, StockBalance.quantity)
        .join(Location, Location.id == StockBalance.location_id)
        .where(StockBalance.thing_id.in_(thing_ids), StockBalance.quantity > 0)
    ).all()
    stock: dict[str, tuple[Decimal, list[str]]] = {
        thing_id: (Decimal(0), []) for thing_id in thing_ids
    }
    for thing_id, location_name, quantity in balances:
        current, locations = stock[thing_id]
        stock[thing_id] = (current + Decimal(quantity), [*locations, location_name])
    for thing_id, (_, locations) in stock.items():
        stock[thing_id] = (max(Decimal(0), available_quantity(db, thing_id)), locations)

    normalized_query = _normalize(query)
    results: list[dict[str, object]] = []
    for thing in things:
        aliases = aliases_by_thing[thing.id]
        description = thing.metadata_json.get("description")
        values = [
            thing.name,
            thing.mpn or "",
            thing.category,
            str(description) if isinstance(description, str) else "",
            *aliases,
        ]
        exact = any(_normalize(value) == normalized_query for value in values if value)
        text_score = _token_score(query, values)
        semantic_score = semantic.get(thing.id, 0.0)
        if exact:
            score, match_type = 1.0, "exact"
        elif text_score > 0:
            score, match_type = 0.55 + 0.3 * text_score, "text"
            if semantic_score * 0.8 > score:
                score, match_type = semantic_score * 0.8, "semantic"
        elif semantic_score > 0:
            score, match_type = semantic_score * 0.8, "semantic"
        else:
            continue
        available, locations = stock[thing.id]
        results.append(
            {
                "thing_id": thing.id,
                "name": thing.name,
                "category": thing.category,
                "score": round(score, 6),
                "match_type": match_type,
                "available_quantity": available,
                "locations": sorted(set(locations)),
            }
        )
    results.sort(
        key=lambda row: (float(str(row["score"])), Decimal(str(row["available_quantity"]))),
        reverse=True,
    )
    return results[:limit]


def _manual_roles(db: Session, project: Project) -> list[BuildRole]:
    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project.id, Requirement.source == "user")
            .order_by(Requirement.created_at)
        ).all()
    )
    roles = []
    for requirement in requirements:
        constraints = requirement.constraints or {}
        capabilities_value = constraints.get("required_capabilities", [])
        interfaces_value = constraints.get("required_interfaces", [])
        facts_value = constraints.get("minimum_facts", {})
        priority = (
            requirement.priority
            if requirement.priority in {"required", "recommended", "optional"}
            else "required"
        )
        roles.append(
            BuildRole(
                role_key=requirement.role_key
                or f"manual_{re.sub(r'[^a-z0-9]', '', requirement.id.lower())[:12]}",
                name=requirement.name,
                quantity=requirement.quantity,
                priority=cast(Literal["required", "recommended", "optional"], priority),
                category=str(constraints["category"]) if constraints.get("category") else None,
                required_capabilities=[str(value) for value in capabilities_value]
                if isinstance(capabilities_value, list)
                else [],
                required_interfaces=[str(value) for value in interfaces_value]
                if isinstance(interfaces_value, list)
                else [],
                minimum_facts={str(key): Decimal(str(value)) for key, value in facts_value.items()}
                if isinstance(facts_value, dict)
                else {},
            )
        )
    return roles


def _manual_breakdown(db: Session, project: Project, goal: str) -> BuildBreakdown:
    roles = _manual_roles(db, project)
    if roles:
        return BuildBreakdown(summary=goal[:600], roles=roles)
    normalized = _normalize(goal)
    tokens = set(normalized.split())
    if tokens & {"plant", "soil"} and tokens & {"water", "watering", "moisture"}:
        return BuildBreakdown(
            summary="Monitor soil moisture and indicate when a plant needs water.",
            roles=[
                BuildRole(
                    role_key="moisture_sensor",
                    name="soil moisture sensor",
                    category="sensor",
                    required_capabilities=["soil moisture sensing"],
                ),
                BuildRole(
                    role_key="controller",
                    name="microcontroller development board",
                    category="board",
                    required_capabilities=["microcontroller"],
                ),
                BuildRole(
                    role_key="indicator",
                    name="visual indicator",
                    priority="recommended",
                    category="module",
                    required_capabilities=["visual indicator"],
                ),
            ],
        )
    return BuildBreakdown(
        summary=goal[:600],
        roles=[BuildRole(role_key="primary", name=goal[:300])],
    )


def _decompose_build(db: Session, project: Project, goal: str) -> BuildBreakdown:
    config = _provider_config(db, project.lab_id)
    if not config or not config.enabled:
        return _manual_breakdown(db, project, goal)
    provider = OpenAICompatibleProvider(
        base_url=config.base_url,
        model=config.model,
        api_key=decrypt_secret(config.secret_ciphertext, get_settings().encryption_key),
    )
    prompt = (
        "Decompose this electronics build into at most 20 inventory roles. Return exactly an object "
        "with summary and roles. Each role has role_key (lowercase snake_case), name, quantity, "
        "priority (required|recommended|optional), category (string|null), required_capabilities "
        "(array), required_interfaces (array), and minimum_facts (numeric object). Use generic "
        "functional requirements, do not invent a particular part, technical rating, or interface "
        "that the request does not require. Do not include tools or consumables unless needed. "
        f"Project: {project.name}. Goal: {goal}"
    )
    try:
        raw = provider.generate_structured(
            prompt, "BuildBreakdown", BuildBreakdown.model_json_schema()
        )
        breakdown = BuildBreakdown.model_validate(raw)
    except (ProviderError, ValidationError, ValueError) as first_error:
        try:
            repaired = provider.generate_structured(
                f"{prompt}\nPrevious output was invalid: {str(first_error)[:1200]}. Correct it with no extra fields.",
                "BuildBreakdown",
                BuildBreakdown.model_json_schema(),
            )
            breakdown = BuildBreakdown.model_validate(repaired)
        except (ProviderError, ValidationError, ValueError):
            return _manual_breakdown(db, project, goal)
    manual_roles = _manual_roles(db, project)
    existing = {(_normalize(role.role_key), _normalize(role.name)) for role in manual_roles}
    generated = [
        role
        for role in breakdown.roles
        if (_normalize(role.role_key), _normalize(role.name)) not in existing
        and not any(_normalize(role.name) == _normalize(manual.name) for manual in manual_roles)
    ]
    return BuildBreakdown(summary=breakdown.summary, roles=[*manual_roles, *generated][:20])


def _compatibility(
    db: Session, thing_id: str, role: BuildRole, available: Decimal
) -> tuple[Literal["pass", "fail", "unknown"], list[str]]:
    evidence: list[str] = []
    status: Literal["pass", "fail", "unknown"] = "pass"
    thing = db.get(Thing, thing_id)
    if not thing:
        return "fail", ["inventory item no longer exists"]
    if available < role.quantity:
        status = "fail"
        evidence.append(f"needs {role.quantity}; only {available} available")
    if role.category and _normalize(thing.category) != _normalize(role.category):
        status = "fail"
        evidence.append(f"category is {thing.category}, not {role.category}")
    capabilities = {
        _normalize(value)
        for value in db.scalars(
            select(Capability.value).where(Capability.thing_id == thing_id)
        ).all()
    }
    interfaces = {
        _normalize(value)
        for value in db.scalars(
            select(ThingInterface.kind).where(ThingInterface.thing_id == thing_id)
        ).all()
    }
    for capability in role.required_capabilities:
        if _normalize(capability) in capabilities:
            evidence.append(f"capability confirmed: {capability}")
        elif status != "fail":
            status = "unknown"
            evidence.append(f"capability not recorded: {capability}")
    for interface in role.required_interfaces:
        if _normalize(interface) in interfaces:
            evidence.append(f"interface confirmed: {interface}")
        elif status != "fail":
            status = "unknown"
            evidence.append(f"interface not recorded: {interface}")
    for key, minimum in role.minimum_facts.items():
        fact = db.scalar(
            select(TechnicalFact).where(
                TechnicalFact.thing_id == thing_id,
                TechnicalFact.key == key,
                TechnicalFact.verification_state.in_(["accepted", "verified"]),
            )
        )
        if not fact or fact.value_numeric is None:
            if status != "fail":
                status = "unknown"
            evidence.append(f"verified fact not recorded: {key}")
        elif Decimal(fact.value_numeric) < minimum:
            status = "fail"
            evidence.append(f"{key} is below {minimum}")
        else:
            evidence.append(f"{key} meets {minimum}")
    if not evidence:
        evidence.append("identity and available quantity match")
    return status, evidence


def plan_build(
    db: Session, project_id: str, lab_id: str, goal_override: str | None
) -> dict[str, object]:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.lab_id == lab_id))
    if not project:
        raise ProviderError("Project for BUILD planning is unavailable")
    goal = (goal_override or project.description or project.name).strip()
    breakdown = _decompose_build(db, project, goal)
    options_by_role: list[tuple[BuildRole, list[dict[str, object]]]] = []
    missing: list[dict[str, object]] = []
    for role in breakdown.roles:
        query = " ".join(
            [role.name, role.category or "", *role.required_capabilities, *role.required_interfaces]
        ).strip()
        matches = search_inventory(db, lab_id, query, limit=5)
        options: list[dict[str, object]] = []
        for match in matches:
            available = Decimal(str(match["available_quantity"]))
            status, evidence = _compatibility(db, str(match["thing_id"]), role, available)
            if status == "fail":
                continue
            options.append({**match, "match_status": status, "evidence": evidence})
        if options:
            options_by_role.append((role, options[:5]))
        else:
            missing.append(
                {
                    "role_key": role.role_key,
                    "name": role.name,
                    "quantity": str(role.quantity),
                    "priority": role.priority,
                    "constraints": {
                        "category": role.category,
                        "required_capabilities": role.required_capabilities,
                        "required_interfaces": role.required_interfaces,
                        "minimum_facts": {
                            key: str(value) for key, value in role.minimum_facts.items()
                        },
                    },
                    "label": "Component required",
                }
            )

    combinations = (
        product(*(options for _, options in options_by_role)) if options_by_role else [()]
    )
    solutions: list[dict[str, object]] = []
    for checked, combination in enumerate(combinations):
        if checked >= 500:
            break
        used: dict[str, Decimal] = {}
        valid = True
        components: list[dict[str, object]] = []
        score = 0.0
        for (role, _), match in zip(options_by_role, combination, strict=True):
            thing_id = str(match["thing_id"])
            used[thing_id] = used.get(thing_id, Decimal(0)) + role.quantity
            if used[thing_id] > Decimal(str(match["available_quantity"])):
                valid = False
                break
            match_score = float(str(match["score"]))
            if match["match_status"] == "unknown":
                match_score -= 0.12
            score += match_score
            components.append(
                {
                    "role_key": role.role_key,
                    "requirement_name": role.name,
                    "quantity": str(role.quantity),
                    "thing_id": thing_id,
                    "thing_name": match["name"],
                    "category": match["category"],
                    "available_quantity": str(match["available_quantity"]),
                    "match_status": match["match_status"],
                    "evidence": match["evidence"],
                    "constraints": {
                        "category": role.category,
                        "required_capabilities": role.required_capabilities,
                        "required_interfaces": role.required_interfaces,
                        "minimum_facts": {
                            key: str(value) for key, value in role.minimum_facts.items()
                        },
                    },
                }
            )
        if not valid:
            continue
        identity = "|".join(
            f"{item['role_key']}:{item['thing_id']}:{item['quantity']}" for item in components
        )
        solutions.append(
            {
                "id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                "score": round(score, 6),
                "components": components,
                "missing_components": missing,
            }
        )
    solutions.sort(key=lambda solution: float(str(solution["score"])), reverse=True)
    return {
        "project_id": project.id,
        "project_revision": project.revision,
        "goal": goal,
        "summary": breakdown.summary,
        "solutions": solutions[:3],
        "component_required": missing,
        "limits": {"top_per_role": 5, "combinations_checked": 500, "solutions_returned": 3},
    }


def accept_build_plan(
    db: Session, project: Project, job: Job, solution_id: str, expected_revision: int
) -> Project:
    now = datetime.now(UTC)
    if project.revision != expected_revision:
        raise ValueError("Project was updated elsewhere; reload and retry")
    if job.kind != "project.plan" or job.status != "completed" or not job.result:
        raise ValueError("BUILD proposal is not ready")
    if int(str(job.result.get("project_revision", -1))) != project.revision:
        raise ValueError("BUILD proposal is stale; generate a new one")
    if job.expires_at and job.expires_at <= now:
        raise ValueError("BUILD proposal has expired; generate a new one")
    if str(job.payload.get("project_id", "")) != project.id or job.lab_id != project.lab_id:
        raise ValueError("BUILD proposal does not belong to this project")
    solutions = job.result.get("solutions", [])
    if not isinstance(solutions, list):
        raise ValueError("BUILD proposal has no valid solutions")  # noqa: TRY004
    solution = next(
        (
            value
            for value in solutions
            if isinstance(value, dict) and value.get("id") == solution_id
        ),
        None,
    )
    if not solution:
        raise ValueError("Selected BUILD solution was not found")
    components = solution.get("components", [])
    missing = solution.get("missing_components", [])
    if not isinstance(components, list) or not isinstance(missing, list):
        raise ValueError("Selected BUILD solution is invalid")  # noqa: TRY004
    total_needed: dict[str, Decimal] = {}
    for component in components:
        if not isinstance(component, dict):
            continue
        thing_id = str(component.get("thing_id", ""))
        quantity = Decimal(str(component.get("quantity", "0")))
        thing = db.scalar(
            select(Thing).where(
                Thing.id == thing_id,
                Thing.lab_id == project.lab_id,
                Thing.archived_at.is_(None),
            )
        )
        if not thing:
            raise ValueError("A proposed inventory item is no longer available")
        total_needed[thing_id] = total_needed.get(thing_id, Decimal(0)) + quantity
    for thing_id, quantity in total_needed.items():
        if available_quantity(db, thing_id) < quantity:
            raise ValueError("Inventory changed; generate a fresh BUILD proposal")
    accepted_roles: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        role_key = str(component["role_key"])
        accepted_roles.add(role_key)
        requirement = db.scalar(
            select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.source == "planner",
                Requirement.role_key == role_key,
            )
        )
        if requirement is None:
            requirement = Requirement(
                project_id=project.id, source="planner", role_key=role_key, name=""
            )
            db.add(requirement)
        requirement.name = str(component["requirement_name"])
        requirement.quantity = Decimal(str(component["quantity"]))
        requirement.priority = "required"
        requirement.selected_thing_id = str(component["thing_id"])
        requirement.match_status = str(component["match_status"])
        constraints = component.get("constraints", {})
        requirement.constraints = {
            **(constraints if isinstance(constraints, dict) else {}),
            "evidence": component.get("evidence", []),
        }
    for missing_component in missing:
        if not isinstance(missing_component, dict):
            continue
        role_key = str(missing_component["role_key"])
        accepted_roles.add(role_key)
        requirement = db.scalar(
            select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.source == "planner",
                Requirement.role_key == role_key,
            )
        )
        if requirement is None:
            requirement = Requirement(
                project_id=project.id, source="planner", role_key=role_key, name=""
            )
            db.add(requirement)
        requirement.name = str(missing_component["name"])
        requirement.quantity = Decimal(str(missing_component["quantity"]))
        requirement.priority = str(missing_component.get("priority", "required"))
        requirement.selected_thing_id = None
        requirement.match_status = "missing"
        constraints = missing_component.get("constraints", {})
        requirement.constraints = constraints if isinstance(constraints, dict) else {}
    existing_planner = db.scalars(
        select(Requirement).where(
            Requirement.project_id == project.id, Requirement.source == "planner"
        )
    ).all()
    for requirement in existing_planner:
        if requirement.role_key not in accepted_roles:
            db.delete(requirement)
    project.design_json = {
        "version": 1,
        "status": "plan_accepted",
        "source_job_id": job.id,
        "goal": job.result.get("goal"),
        "summary": job.result.get("summary"),
        "solution": solution,
    }
    project.revision += 1
    result = dict(job.result)
    result["accepted_solution_id"] = solution_id
    result["accepted_at"] = now.isoformat()
    job.result = result
    db.flush()
    return project


def complete_job(job: Job, result: dict[str, object], *, temporary: bool = False) -> None:
    now = datetime.now(UTC)
    job.status = "completed"
    job.result = result
    job.completed_at = now
    job.expires_at = now + timedelta(hours=24) if temporary else None
    job.last_error = None
    job.leased_until = None


def cleanup_expired_job_results(db: Session) -> int:
    now = datetime.now(UTC)
    jobs = db.scalars(
        select(Job).where(
            Job.expires_at.is_not(None), Job.expires_at <= now, Job.result.is_not(None)
        )
    ).all()
    for job in jobs:
        job.result = None
        job.status = "expired"
    return len(jobs)
