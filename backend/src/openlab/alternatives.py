"""Stock-aware inverse search for functional component alternatives."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .demo_seed import COMMON_MODULES, ModuleSeed
from .intelligence import _compatibility, _normalize, _provider_config, search_inventory
from .models import (
    Capability,
    Job,
    Pin,
    Project,
    Requirement,
    TechnicalFact,
    Thing,
    ThingAlias,
    ThingInterface,
)
from .providers import OpenAICompatibleProvider, ProviderError, decrypt_secret, is_local_endpoint
from .services import available_quantity

MAX_ROLES = 4
MAX_CANDIDATES_PER_ROLE = 5
MAX_COMBINATIONS = 500
MAX_SOLUTIONS = 3


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlternativeRole(StrictModel):
    role_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(default=Decimal(1), gt=0)
    category: str | None = Field(default=None, max_length=120)
    required_capabilities: list[str] = Field(default_factory=list, max_length=12)
    required_interfaces: list[str] = Field(default_factory=list, max_length=12)
    minimum_facts: dict[str, Decimal] = Field(default_factory=dict)


class AlternativeTarget(StrictModel):
    canonical_name: str = Field(min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    summary: str = Field(min_length=1, max_length=600)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    critical_interfaces: list[str] = Field(default_factory=list, max_length=12)
    roles: list[AlternativeRole] = Field(min_length=1, max_length=MAX_ROLES)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        normalized = _normalize(cleaned)
        if cleaned and normalized not in seen:
            seen.add(normalized)
            result.append(cleaned)
    return result


def _role_key(value: str, index: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:100]
    return normalized or f"function_{index + 1}"


def _target_from_fields(
    *,
    name: str,
    category: str | None,
    summary: str,
    capabilities: list[str],
    interfaces: list[str],
) -> AlternativeTarget:
    unique_capabilities = _unique(capabilities)[:MAX_ROLES]
    if unique_capabilities:
        roles = [
            AlternativeRole(
                role_key=_role_key(capability, index),
                name=capability,
                required_capabilities=[capability],
            )
            for index, capability in enumerate(unique_capabilities)
        ]
    else:
        roles = [AlternativeRole(role_key="primary", name=summary[:300])]
    return AlternativeTarget(
        canonical_name=name,
        category=category,
        summary=summary,
        critical_interfaces=_unique(interfaces)[:12],
        roles=roles,
    )


def _thing_target(db: Session, thing: Thing) -> AlternativeTarget:
    capabilities = list(
        db.scalars(select(Capability.value).where(Capability.thing_id == thing.id)).all()
    )
    interfaces = list(
        db.scalars(select(ThingInterface.kind).where(ThingInterface.thing_id == thing.id)).all()
    )
    description = thing.metadata_json.get("description")
    summary = (
        str(description)
        if isinstance(description, str) and description.strip()
        else f"Recorded {thing.category} named {thing.name}."
    )
    return _target_from_fields(
        name=thing.name,
        category=thing.category,
        summary=summary,
        capabilities=capabilities,
        interfaces=interfaces,
    )


def _catalog_target(module: ModuleSeed) -> AlternativeTarget:
    return _target_from_fields(
        name=module.name,
        category=module.category,
        summary=module.description,
        capabilities=list(module.capabilities),
        interfaces=list(module.interfaces),
    )


def _matching_catalog(identities: list[str]) -> ModuleSeed | None:
    normalized = {_normalize(value) for value in identities if value}
    for module in COMMON_MODULES:
        module_identities = {_normalize(module.name), *(_normalize(value) for value in module.aliases)}
        if normalized & module_identities:
            return module
    return None


def _exact_local_target(
    db: Session, lab_id: str, target_name: str
) -> tuple[AlternativeTarget, str, str | None] | None:
    normalized = _normalize(target_name)
    things = list(
        db.scalars(select(Thing).where(Thing.lab_id == lab_id, Thing.archived_at.is_(None))).all()
    )
    aliases: dict[str, list[str]] = {thing.id: [] for thing in things}
    if aliases:
        for thing_id, value in db.execute(
            select(ThingAlias.thing_id, ThingAlias.value).where(ThingAlias.thing_id.in_(aliases))
        ).all():
            aliases[thing_id].append(value)
    for thing in things:
        identities = [thing.name, thing.mpn or "", *aliases[thing.id]]
        if any(_normalize(value) == normalized for value in identities if value):
            target = _thing_target(db, thing)
            catalog = _matching_catalog(identities)
            if catalog and not any(role.required_capabilities for role in target.roles):
                return _catalog_target(catalog), "local_catalog", thing.id
            return target, "inventory", thing.id
    catalog = _matching_catalog([target_name])
    if catalog:
        return _catalog_target(catalog), "local_catalog", None
    return None


def _model_target(
    db: Session, lab_id: str, target_name: str, intended_use: str | None
) -> tuple[AlternativeTarget, Literal["local", "external"]] | None:
    config = _provider_config(db, lab_id)
    if not config or not config.enabled:
        return None
    provider = OpenAICompatibleProvider(
        base_url=config.base_url,
        model=config.model,
        api_key=decrypt_secret(config.secret_ciphertext, get_settings().encryption_key),
    )
    prompt = (
        "Analyze one named electronics component or board for functional substitution. Return "
        "exactly the requested schema. Decompose only the functions needed to replace one unit "
        "into at most four non-overlapping roles. Use generic capabilities, not preferred product "
        "names. Do not claim pin, voltage, electrical, form-factor, or drop-in compatibility unless "
        "the input explicitly supplies it. Do not browse the web or invent numeric requirements. "
        f"Target: {target_name}. Intended use: {intended_use or '[not provided]'}"
    )
    try:
        raw = provider.generate_structured(
            prompt, "AlternativeTarget", AlternativeTarget.model_json_schema()
        )
        target = AlternativeTarget.model_validate(raw)
    except (ProviderError, ValidationError, ValueError) as first_error:
        try:
            repaired = provider.generate_structured(
                f"{prompt}\nPrevious output was invalid: {str(first_error)[:1200]}. Correct it with no extra fields.",
                "AlternativeTarget",
                AlternativeTarget.model_json_schema(),
            )
            target = AlternativeTarget.model_validate(repaired)
        except (ProviderError, ValidationError, ValueError):
            return None
    egress: Literal["local", "external"] = (
        "local" if is_local_endpoint(config.base_url) else "external"
    )
    return target, egress


def _locations_for_match(match: dict[str, object]) -> list[str]:
    value = match.get("locations", [])
    return [str(item) for item in value] if isinstance(value, list) else []


def _technical_missing_checks(db: Session, thing_ids: list[str]) -> list[str]:
    missing: list[str] = []
    for thing_id in thing_ids:
        thing = db.get(Thing, thing_id)
        if not thing:
            continue
        pins = db.scalar(
            select(Pin.id).where(
                Pin.thing_id == thing_id,
                Pin.verification_state.in_(["accepted", "verified"]),
            ).limit(1)
        )
        facts = list(
            db.scalars(
                select(TechnicalFact).where(
                    TechnicalFact.thing_id == thing_id,
                    TechnicalFact.verification_state.in_(["accepted", "verified"]),
                )
            ).all()
        )
        fact_keys = {_normalize(fact.key) for fact in facts}
        if not pins:
            missing.append(f"{thing.name}: reviewed pin data")
        if not any(re.search(r"voltage|logic level|supply|current|power", key) for key in fact_keys):
            missing.append(f"{thing.name}: electrical limits")
        if not any(re.search(r"dimension|form factor|package|footprint|size", key) for key in fact_keys):
            missing.append(f"{thing.name}: physical fit")
    return missing


def _covered_interfaces(db: Session, thing_ids: list[str]) -> set[str]:
    if not thing_ids:
        return set()
    return {
        _normalize(value)
        for value in db.scalars(
            select(ThingInterface.kind).where(ThingInterface.thing_id.in_(thing_ids))
        ).all()
    }


def _solution(
    db: Session,
    target: AlternativeTarget,
    source: str,
    assignments: list[tuple[AlternativeRole, dict[str, object], str, list[str]]],
) -> dict[str, object]:
    line_items_by_thing: dict[str, dict[str, object]] = {}
    thing_ids: list[str] = []
    scores: list[float] = []
    role_statuses: list[str] = []
    for role, match, role_status, evidence in assignments:
        thing_id = str(match["thing_id"])
        if thing_id not in thing_ids:
            thing_ids.append(thing_id)
        scores.append(float(str(match["score"])))
        role_statuses.append(role_status)
        existing = line_items_by_thing.get(thing_id)
        if existing:
            existing["quantity"] = str(
                max(Decimal(str(existing["quantity"])), role.quantity)
            )
            existing_roles = cast(list[str], existing["covered_roles"])
            existing["covered_roles"] = [*existing_roles, role.role_key]
            existing_evidence = cast(list[str], existing["evidence"])
            existing["evidence"] = _unique([*existing_evidence, *evidence])
        else:
            line_items_by_thing[thing_id] = {
                "thing_id": thing_id,
                "thing_name": str(match["name"]),
                "category": str(match["category"]),
                "quantity": str(role.quantity),
                "available_quantity": str(match["available_quantity"]),
                "locations": _locations_for_match(match),
                "covered_roles": [role.role_key],
                "evidence": evidence,
            }
    line_items = list(line_items_by_thing.values())
    required_interfaces = {_normalize(value): value for value in target.critical_interfaces}
    covered_interfaces = _covered_interfaces(db, thing_ids)
    missing_checks = _technical_missing_checks(db, thing_ids)
    for normalized, original in required_interfaces.items():
        if normalized not in covered_interfaces:
            missing_checks.append(f"system interface not documented: {original}")
    missing_checks = _unique(missing_checks)
    if all(status == "pass" for status in role_statuses) and source != "model" and not missing_checks:
        tier = "documented_match"
    elif any(status == "pass" for status in role_statuses):
        tier = "needs_validation"
    else:
        tier = "insufficient_evidence"
    identity = "|".join(
        f"{item['thing_id']}:{item['quantity']}:{','.join(cast(list[str], item['covered_roles']))}"
        for item in line_items
    )
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "status": tier,
        "score": round(
            min(1.0, sum(scores) / max(1, len(scores)) + (0.08 if len(line_items) == 1 else 0)),
            6,
        ),
        "line_items": line_items,
        "covered_functions": [role.name for role, _, _, _ in assignments],
        "evidence": _unique(
            [entry for _, _, _, evidence in assignments for entry in evidence]
        ),
        "missing_checks": missing_checks,
    }


def _single_item_solutions(
    db: Session,
    target: AlternativeTarget,
    source: str,
    options_by_role: list[tuple[AlternativeRole, list[dict[str, object]]]],
) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    for _, options in options_by_role:
        for match in options:
            candidates[str(match["thing_id"])] = match
    solutions: list[dict[str, object]] = []
    for match in candidates.values():
        assignments: list[tuple[AlternativeRole, dict[str, object], str, list[str]]] = []
        for role in target.roles:
            status, evidence = _compatibility(
                db, str(match["thing_id"]), role, Decimal(str(match["available_quantity"]))
            )
            if status == "fail":
                assignments = []
                break
            assignments.append((role, match, status, evidence))
        if not assignments:
            continue
        combined = _solution(db, target, source, assignments)
        solutions.append(combined)
    return solutions


def plan_alternatives(
    db: Session, lab_id: str, target_name: str, intended_use: str | None
) -> dict[str, object]:
    local = _exact_local_target(db, lab_id, target_name)
    target: AlternativeTarget | None
    source: str
    inventory_thing_id: str | None
    egress: str | None = None
    if local:
        target, source, inventory_thing_id = local
    else:
        modeled = _model_target(db, lab_id, target_name, intended_use)
        if not modeled:
            return {
                "status": "insufficient_target_knowledge",
                "target_name": target_name,
                "intended_use": intended_use,
                "message": "OpenLab has no reviewed local record for this target and the configured model could not establish its function.",
                "solutions": [],
                "direct_stock": None,
                "limits": _limits(),
            }
        target, egress = modeled
        source, inventory_thing_id = "model", None

    direct_stock = None
    if inventory_thing_id:
        exact = next(
            (
                row
                for row in search_inventory(
                    db,
                    lab_id,
                    target.canonical_name,
                    limit=30,
                    allow_semantic=source == "model",
                )
                if str(row["thing_id"]) == inventory_thing_id
                and Decimal(str(row["available_quantity"])) > 0
            ),
            None,
        )
        if exact:
            direct_stock = {
                "thing_id": inventory_thing_id,
                "thing_name": str(exact["name"]),
                "available_quantity": str(exact["available_quantity"]),
                "locations": _locations_for_match(exact),
            }

    options_by_role: list[tuple[AlternativeRole, list[dict[str, object]]]] = []
    gaps: list[dict[str, object]] = []
    for role in target.roles:
        query = " ".join(
            [role.name, *role.required_capabilities, *role.required_interfaces]
        ).strip()
        options: list[dict[str, object]] = []
        for match in search_inventory(
            db,
            lab_id,
            query,
            limit=MAX_CANDIDATES_PER_ROLE + 1,
            allow_semantic=source == "model",
        ):
            if inventory_thing_id and str(match["thing_id"]) == inventory_thing_id:
                continue
            available = Decimal(str(match["available_quantity"]))
            if available <= 0:
                continue
            status, evidence = _compatibility(db, str(match["thing_id"]), role, available)
            if status != "fail":
                options.append({**match, "role_status": status, "role_evidence": evidence})
            if len(options) == MAX_CANDIDATES_PER_ROLE:
                break
        if options:
            options_by_role.append((role, options))
        else:
            gaps.append(
                {
                    "role_key": role.role_key,
                    "name": role.name,
                    "reason": "No available stock item covers this function with enough evidence.",
                }
            )

    solutions = _single_item_solutions(db, target, source, options_by_role)
    checked = 0
    if len(options_by_role) == len(target.roles):
        for combination in product(*(options for _, options in options_by_role)):
            if checked >= MAX_COMBINATIONS:
                break
            checked += 1
            ids = [str(match["thing_id"]) for match in combination]
            if len(set(ids)) != len(ids):
                continue
            assignments = []
            for (role, _), match in zip(options_by_role, combination, strict=True):
                assignments.append(
                    (
                        role,
                        match,
                        str(match["role_status"]),
                        [str(value) for value in cast(list[object], match["role_evidence"])],
                    )
                )
            if len(assignments) <= MAX_ROLES:
                solutions.append(_solution(db, target, source, assignments))

    unique_solutions: dict[str, dict[str, object]] = {}
    for solution in solutions:
        existing = unique_solutions.get(str(solution["id"]))
        if not existing or float(str(solution["score"])) > float(str(existing["score"])):
            unique_solutions[str(solution["id"])] = solution
    rank = {"documented_match": 2, "needs_validation": 1, "insufficient_evidence": 0}
    ordered = sorted(
        unique_solutions.values(),
        key=lambda item: (
            rank.get(str(item["status"]), -1),
            float(str(item["score"])),
            str(item["id"]),
        ),
        reverse=True,
    )[:MAX_SOLUTIONS]
    return {
        "status": "ready",
        "target": {
            **target.model_dump(mode="json"),
            "input_name": target_name,
            "intended_use": intended_use,
            "knowledge_source": source,
            "provider_egress": egress,
            "confidence": "reviewed" if source in {"inventory", "local_catalog"} else "model_inferred",
        },
        "direct_stock": direct_stock,
        "solutions": ordered,
        "gaps": gaps,
        "limits": {**_limits(), "combinations_checked": checked},
    }


def _limits() -> dict[str, int]:
    return {
        "roles": MAX_ROLES,
        "candidates_per_role": MAX_CANDIDATES_PER_ROLE,
        "combinations": MAX_COMBINATIONS,
        "line_items": MAX_ROLES,
        "solutions": MAX_SOLUTIONS,
    }


def create_build_from_alternative(
    db: Session, lab_id: str, job_id: str, solution_id: str
) -> tuple[Project, bool]:
    job = db.scalar(
        select(Job)
        .where(Job.id == job_id, Job.lab_id == lab_id, Job.kind == "inventory.inverse_search")
        .with_for_update(of=Job)
    )
    if not job:
        raise ValueError("Alternative search not found")
    if job.status != "completed" or not job.result:
        raise ValueError("Alternative search is not ready")
    if job.expires_at and job.expires_at <= datetime.now(UTC):
        raise ValueError("Alternative search has expired; run it again")
    created = job.result.get("created_projects", {})
    if isinstance(created, dict) and solution_id in created:
        existing = db.scalar(
            select(Project).where(Project.id == str(created[solution_id]), Project.lab_id == lab_id)
        )
        if existing:
            return existing, False
    solutions = job.result.get("solutions", [])
    solution = next(
        (
            item
            for item in solutions
            if isinstance(item, dict) and str(item.get("id", "")) == solution_id
        ),
        None,
    ) if isinstance(solutions, list) else None
    if not solution:
        raise ValueError("Alternative solution not found")
    if solution.get("status") not in {"documented_match", "needs_validation"}:
        raise ValueError("This solution does not have enough evidence to create a Build")
    line_items = solution.get("line_items", [])
    if not isinstance(line_items, list) or not line_items:
        raise ValueError("Alternative solution has no stock line items")
    for line in line_items:
        if not isinstance(line, dict):
            raise TypeError("Alternative solution is invalid")
        thing_id = str(line.get("thing_id", ""))
        quantity = Decimal(str(line.get("quantity", "0")))
        thing = db.scalar(
            select(Thing).where(
                Thing.id == thing_id, Thing.lab_id == lab_id, Thing.archived_at.is_(None)
            )
        )
        if not thing or available_quantity(db, thing_id, lock=True) < quantity:
            raise ValueError("Stock changed since this search; run it again")
    target = job.result.get("target", {})
    target_name = (
        str(target.get("canonical_name", "alternative"))
        if isinstance(target, dict)
        else str(job.payload.get("target_name", "alternative"))
    )
    intended_use = str(job.payload.get("intended_use") or "").strip()
    project = Project(
        lab_id=lab_id,
        name=f"Alternative to {target_name}"[:300],
        description=intended_use or f"Validate a stock-backed functional alternative to {target_name}.",
        status="pending",
        design_json={
            "source": "inverse_search",
            "source_job_id": job.id,
            "target": target if isinstance(target, dict) else {},
            "solution": solution,
            "summary": f"Review and validate the proposed alternative to {target_name} before wiring or allocation.",
        },
    )
    db.add(project)
    db.flush()
    requirements: list[Requirement] = []
    for index, line in enumerate(line_items):
        assert isinstance(line, dict)
        covered_roles = line.get("covered_roles", [])
        requirements.append(
            Requirement(
                project_id=project.id,
                name=str(line.get("thing_name", "Alternative component"))[:300],
                quantity=Decimal(str(line.get("quantity", "1"))),
                priority="required",
                constraints={
                    "covered_roles": covered_roles if isinstance(covered_roles, list) else [],
                    "inverse_search_status": solution.get("status"),
                    "missing_checks": solution.get("missing_checks", []),
                },
                source="inverse",
                role_key=f"alternative_{index + 1}",
                selected_thing_id=str(line.get("thing_id", "")),
                match_status="pass" if solution.get("status") == "documented_match" else "unknown",
            )
        )
    db.add_all(requirements)
    result = dict(job.result)
    created_projects = dict(created) if isinstance(created, dict) else {}
    created_projects[solution_id] = project.id
    result["created_projects"] = created_projects
    job.result = result
    db.flush()
    return project, True
