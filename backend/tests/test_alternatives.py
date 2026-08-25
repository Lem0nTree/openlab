from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from openlab.alternatives import (
    MAX_ROLES,
    AlternativeRole,
    AlternativeTarget,
    _catalog_target,
    _matching_catalog,
    _model_target,
    _role_key,
    _solution,
    _target_from_fields,
    create_build_from_alternative,
    plan_alternatives,
)
from openlab.demo_seed import COMMON_MODULES
from openlab.main import app
from openlab.models import Allocation, Job, Project, Requirement, Thing
from openlab.schemas import AlternativeSearchRequest


def test_openapi_registers_inverse_search_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/alternatives/search" in paths
    assert "/api/v1/alternatives/searches" in paths
    assert "/api/v1/alternatives/{job_id}/solutions/{solution_id}/build" in paths


def test_alternative_search_input_is_strict_and_bounded() -> None:
    request = AlternativeSearchRequest(target_name="ESP32", intended_use="Read an I2C sensor")
    assert request.target_name == "ESP32"
    with pytest.raises(ValidationError):
        AlternativeSearchRequest.model_validate({"target_name": "ESP32", "web_search": True})
    with pytest.raises(ValidationError):
        AlternativeSearchRequest(target_name="")


def test_target_schema_limits_non_overlapping_roles() -> None:
    roles = [AlternativeRole(role_key=f"role_{index}", name=f"Role {index}") for index in range(5)]
    with pytest.raises(ValidationError):
        AlternativeTarget(
            canonical_name="Composite board",
            summary="A board with too many substitute roles.",
            roles=roles,
        )
    assert MAX_ROLES == 4


def test_curated_target_uses_local_capabilities_without_claiming_drop_in_fit() -> None:
    module = next(item for item in COMMON_MODULES if item.key == "esp32-devkit-v1")
    target = _catalog_target(module)
    assert target.canonical_name == "ESP32 DevKit V1"
    assert [role.name for role in target.roles] == list(module.capabilities)[:4]
    assert target.critical_interfaces == list(module.interfaces)
    assert all("drop" not in assumption.lower() for assumption in target.assumptions)
    assert _matching_catalog(["ESP32 development board"]) == module


def test_local_target_role_keys_are_stable_and_limited() -> None:
    target = _target_from_fields(
        name="Example",
        category="board",
        summary="Example board",
        capabilities=["GPIO expansion", "GPIO expansion", "Wi-Fi", "I2C control", "Extra"],
        interfaces=["I2C", "I2C", "UART"],
    )
    assert [role.role_key for role in target.roles] == [
        "gpio_expansion",
        "wi_fi",
        "i2c_control",
        "extra",
    ]
    assert target.critical_interfaces == ["I2C", "UART"]
    assert _role_key("***", 2) == "function_3"


def test_unknown_target_without_local_or_model_knowledge_is_non_actionable(monkeypatch) -> None:
    monkeypatch.setattr("openlab.alternatives._exact_local_target", lambda *_args: None)
    monkeypatch.setattr("openlab.alternatives._model_target", lambda *_args: None)
    result = plan_alternatives(None, "lab", "Unknown board", None)  # type: ignore[arg-type]
    assert result["status"] == "insufficient_target_knowledge"
    assert result["solutions"] == []
    assert result["direct_stock"] is None


def test_model_target_repairs_invalid_output_and_records_external_egress(monkeypatch) -> None:
    config = type(
        "ProviderConfigStub",
        (),
        {
            "enabled": True,
            "base_url": "https://provider.example/v1",
            "model": "test-model",
            "secret_ciphertext": None,
        },
    )()
    responses = [
        {"invalid": True},
        {
            "canonical_name": "Unknown controller",
            "category": "board",
            "summary": "Controls one I2C peripheral.",
            "assumptions": ["Exact pin mapping is unknown."],
            "critical_interfaces": ["I2C"],
            "roles": [
                {
                    "role_key": "controller",
                    "name": "microcontroller",
                    "quantity": 1,
                    "category": None,
                    "required_capabilities": ["microcontroller"],
                    "required_interfaces": ["I2C"],
                    "minimum_facts": {},
                }
            ],
        },
    ]
    prompts: list[str] = []

    class FakeProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def generate_structured(
            self, prompt: str, _schema_name: str, _schema: dict[str, object]
        ) -> dict[str, object]:
            prompts.append(prompt)
            return responses.pop(0)

    monkeypatch.setattr("openlab.alternatives._provider_config", lambda *_args: config)
    monkeypatch.setattr("openlab.alternatives.OpenAICompatibleProvider", FakeProvider)
    result = _model_target(None, "lab", "Unknown controller", "Read I2C")  # type: ignore[arg-type]
    assert result is not None
    target, egress = result
    assert target.roles[0].required_interfaces == ["I2C"]
    assert egress == "external"
    assert len(prompts) == 2
    assert "Do not browse the web" in prompts[0]


def test_model_inference_alone_cannot_become_a_documented_match(monkeypatch) -> None:
    target = AlternativeTarget(
        canonical_name="Inferred board",
        summary="An inferred function.",
        critical_interfaces=["I2C"],
        roles=[
            AlternativeRole(
                role_key="controller",
                name="microcontroller",
                required_capabilities=["microcontroller"],
            )
        ],
    )
    monkeypatch.setattr("openlab.alternatives._technical_missing_checks", lambda *_args: [])
    monkeypatch.setattr("openlab.alternatives._covered_interfaces", lambda *_args: {"i2c"})
    result = _solution(
        None,  # type: ignore[arg-type]
        target,
        "model",
        [
            (
                target.roles[0],
                {
                    "thing_id": "thing",
                    "name": "Stock controller",
                    "category": "board",
                    "score": 1,
                    "available_quantity": 1,
                    "locations": ["Drawer A"],
                },
                "pass",
                ["capability confirmed: microcontroller"],
            )
        ],
    )
    assert result["status"] == "needs_validation"


def test_role_quantities_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        AlternativeRole(role_key="controller", name="Controller", quantity=Decimal(0))


def _completed_job(*, expired: bool = False) -> Job:
    return Job(
        id="job-1",
        lab_id="lab",
        kind="inventory.inverse_search",
        status="completed",
        payload={"target_name": "Target board", "intended_use": "Read a sensor"},
        result={
            "status": "ready",
            "target": {"canonical_name": "Target board"},
            "solutions": [
                {
                    "id": "solution-1",
                    "status": "needs_validation",
                    "line_items": [
                        {
                            "thing_id": "thing-1",
                            "thing_name": "Stock controller",
                            "quantity": "1",
                            "covered_roles": ["controller"],
                        }
                    ],
                    "missing_checks": ["Stock controller: electrical limits"],
                }
            ],
        },
        expires_at=datetime.now(UTC) + (-timedelta(minutes=1) if expired else timedelta(hours=1)),
    )


class FakeBuildDB:
    def __init__(self, scalar_results: list[object]) -> None:
        self.scalar_results = scalar_results
        self.added: list[object] = []

    def scalar(self, _statement: object) -> object:
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    def flush(self) -> None:
        for value in self.added:
            if isinstance(value, Project) and value.id is None:
                value.id = "project-1"


def test_create_build_materializes_requirements_without_allocating_stock(monkeypatch) -> None:
    job = _completed_job()
    thing = Thing(id="thing-1", lab_id="lab", name="Stock controller", category="board")
    db = FakeBuildDB([job, thing])
    monkeypatch.setattr("openlab.alternatives.available_quantity", lambda *_args, **_kwargs: Decimal(2))
    project, created = create_build_from_alternative(
        cast(Session, db), "lab", "job-1", "solution-1"
    )
    requirements = [value for value in db.added if isinstance(value, Requirement)]
    assert created is True
    assert project.id == "project-1"
    assert project.design_json["source"] == "inverse_search"
    assert project.design_json["source_job_id"] == "job-1"
    assert len(requirements) == 1
    assert requirements[0].selected_thing_id == "thing-1"
    assert requirements[0].constraints["covered_roles"] == ["controller"]
    assert not any(isinstance(value, Allocation) for value in db.added)
    assert job.result is not None
    assert job.result["created_projects"] == {"solution-1": "project-1"}


def test_create_build_is_idempotent_for_the_same_solution() -> None:
    job = _completed_job()
    assert job.result is not None
    job.result = {**job.result, "created_projects": {"solution-1": "project-existing"}}
    existing = Project(id="project-existing", lab_id="lab", name="Existing", status="pending")
    db = FakeBuildDB([job, existing])
    project, created = create_build_from_alternative(
        cast(Session, db), "lab", "job-1", "solution-1"
    )
    assert project is existing
    assert created is False
    assert db.added == []


def test_create_build_rejects_expired_foreign_and_stale_searches(monkeypatch) -> None:
    with pytest.raises(ValueError, match="not found"):
        create_build_from_alternative(
            cast(Session, FakeBuildDB([None])), "other-lab", "job-1", "solution-1"
        )
    with pytest.raises(ValueError, match="expired"):
        create_build_from_alternative(
            cast(Session, FakeBuildDB([_completed_job(expired=True)])),
            "lab",
            "job-1",
            "solution-1",
        )
    job = _completed_job()
    thing = Thing(id="thing-1", lab_id="lab", name="Stock controller", category="board")
    monkeypatch.setattr("openlab.alternatives.available_quantity", lambda *_args, **_kwargs: Decimal(0))
    with pytest.raises(ValueError, match="Stock changed"):
        create_build_from_alternative(
            cast(Session, FakeBuildDB([job, thing])), "lab", "job-1", "solution-1"
        )
