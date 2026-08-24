from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pypdf import PdfWriter

from openlab.demo_seed import COMMON_MODULES
from openlab.intelligence import (
    BuildBreakdown,
    _inventory_retrieval_values,
    _manual_breakdown,
    _token_score,
    _valid_vector,
)
from openlab.main import app, confirm_candidate_identity, update_candidate_proposal
from openlab.models import InboxCandidate, Thing
from openlab.providers import OpenAICompatibleProvider, ProviderError
from openlab.schemas import InboxCandidateConfirm, InboxCandidateInput, InboxCandidatePatch
from openlab.schematics import WiringProposal, export_kicad_schematic
from openlab.services import canonical_profile, extract_pdf_text


def test_candidate_schema_separates_identity_confidence_and_rejects_unknown_fields() -> None:
    candidate = InboxCandidateInput.model_validate(
        {
            "name": "MCP23017",
            "description": "16-bit I2C GPIO expander module",
            "quantity": 2,
            "category": "module",
            "identity_confidence": "high",
            "observations": ["Source also mentions MCP23S17"],
        }
    )
    assert candidate.name == "MCP23017"
    assert candidate.quantity == Decimal(2)
    assert candidate.identity_confidence == "high"
    with pytest.raises(ValidationError):
        InboxCandidateInput.model_validate(
            {
                **candidate.model_dump(),
                "manufacturer": "invented",
            }
        )


def test_openapi_registers_item_intelligence_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/knowledge/search" in paths
    assert "/api/v1/projects/{project_id}/plan" in paths
    assert "/api/v1/projects/{project_id}/schematic" in paths
    assert "/api/v1/projects/{project_id}/jobs" in paths
    assert "/api/v1/things/{thing_id}/pins" in paths
    candidate_path = "/api/v1/inbox/{inbox_id}/candidates/{candidate_id}"
    assert {"patch", "delete"}.issubset(paths[candidate_path])


def test_common_module_seed_has_exactly_100_unique_semantic_profiles() -> None:
    assert len(COMMON_MODULES) == 100
    assert len({module.key for module in COMMON_MODULES}) == 100
    assert len({module.name for module in COMMON_MODULES}) == 100
    assert all(module.description and module.capabilities for module in COMMON_MODULES)


def test_proposed_candidate_can_be_edited_without_confirmation() -> None:
    candidate = InboxCandidate(
        inbox_item_id="inbox",
        name="Long marketplace title",
        quantity=Decimal(1),
        category="other",
        identity_confidence="high",
        status="proposed",
        provenance={"raw_title": "retained evidence"},
    )
    update_candidate_proposal(
        candidate,
        InboxCandidatePatch(
            name="MCP23017",
            description="GPIO expansion module controlled over I2C.",
            quantity=Decimal(2),
            category="module",
        ),
    )
    assert candidate.name == "MCP23017"
    assert candidate.quantity == Decimal(2)
    assert candidate.provenance == {
        "raw_title": "retained evidence",
        "description": "GPIO expansion module controlled over I2C.",
    }
    assert candidate.status == "proposed"


def test_unresolved_candidate_requires_product_link_before_confirmation() -> None:
    candidate = InboxCandidate(
        inbox_item_id="inbox",
        name="Unknown electronics item",
        quantity=Decimal(1),
        category="other",
        identity_confidence="unresolved",
        status="proposed",
        provenance={},
    )
    with pytest.raises(HTTPException, match="provide a product link"):
        confirm_candidate_identity(  # type: ignore[arg-type]
            None, None, None, candidate, InboxCandidateConfirm()
        )


def test_received_candidate_cannot_be_reconfirmed_into_a_new_thing() -> None:
    candidate = InboxCandidate(
        inbox_item_id="inbox",
        name="MCP23017",
        quantity=Decimal(1),
        category="module",
        identity_confidence="high",
        status="received",
        provenance={},
    )
    with pytest.raises(HTTPException, match="already confirmed"):
        confirm_candidate_identity(  # type: ignore[arg-type]
            None, None, None, candidate, InboxCandidateConfirm(name="MCP23017 module")
        )


def test_pdf_capture_text_extraction_accepts_local_pdf_bytes() -> None:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    assert extract_pdf_text(output.getvalue()) == ""


def test_build_and_wiring_outputs_are_strict() -> None:
    with pytest.raises(ValidationError):
        BuildBreakdown.model_validate(
            {
                "summary": "Test build",
                "roles": [{"role_key": "controller", "name": "controller", "extra": True}],
            }
        )
    with pytest.raises(ValidationError):
        WiringProposal.model_validate(
            {"summary": "Wire it", "nets": [], "required_support": [], "unknown": "field"}
        )


def test_embedding_vectors_must_be_nonempty_finite_numbers() -> None:
    assert _valid_vector([0, 0.25, -1]) == [0.0, 0.25, -1.0]
    for invalid in ([], [1, float("nan")], [1, "bad"], [True, 0]):
        with pytest.raises(ProviderError):
            _valid_vector(invalid)


def test_plant_monitor_fallback_uses_functional_inventory_roles(monkeypatch) -> None:
    monkeypatch.setattr("openlab.intelligence._manual_roles", lambda _db, _project: [])
    project = type("ProjectStub", (), {})()
    breakdown = _manual_breakdown(  # type: ignore[arg-type]
        None,
        project,
        "I want to make a plant monitor to know when it needs water",
    )
    assert [role.role_key for role in breakdown.roles] == [
        "moisture_sensor",
        "controller",
        "indicator",
    ]
    assert breakdown.roles[0].required_capabilities == ["soil moisture sensing"]


def test_inventory_text_retrieval_includes_capabilities_and_interfaces() -> None:
    thing = Thing(
        lab_id="lab",
        name="WS2812B 8-LED Ring",
        category="module",
        metadata_json={"description": "Addressable RGB LED ring."},
    )
    values = _inventory_retrieval_values(
        thing,
        ["NeoPixel ring"],
        ["RGB lighting", "visual indicator"],
        ["single-wire digital"],
    )
    assert _token_score("visual indicator", values) == 1
    assert "single-wire digital" in values


def test_canonical_profile_contains_only_approved_retrieval_fields() -> None:
    profile, fingerprint = canonical_profile(
        name="MCP23017",
        category="module",
        description="16-bit GPIO expander",
        aliases=["I/O expander"],
        capabilities=["adds GPIO"],
        interfaces=["I2C"],
        facts=["address 32"],
    )
    assert profile == (
        "MCP23017 is an electronics module: 16-bit GPIO expander. "
        "Also known as: I/O expander. Functions and interfaces: adds GPIO, I2C. "
        "Recorded facts: address 32."
    )
    assert "Ships From" not in profile
    assert len(fingerprint) == 64


def test_strict_provider_output_falls_back_to_json_object_mode(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"ok"}'}}]
            }

    responses = [FakeResponse(400), FakeResponse(200)]
    requests: list[dict[str, object]] = []

    def fake_post(_url: str, **kwargs: object) -> FakeResponse:
        requests.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("openlab.providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1/v1", model="test", api_key=None
    )
    assert provider.generate_structured(
        "Return a summary", "Summary", {"type": "object", "properties": {}}
    ) == {"summary": "ok"}
    assert requests[0]["json"]["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert requests[1]["json"]["response_format"]["type"] == "json_object"  # type: ignore[index]


def test_kicad_export_is_a_deterministic_sourced_wiring_document() -> None:
    design = {
        "project_id": "project-1",
        "summary": "Connect the bus",
        "components": [
            {
                "role_key": "controller",
                "name": "ESP32-C3",
                "thing_id": "thing-1",
                "pins": [
                    {
                        "id": "pin-1",
                        "name": "SDA",
                        "number": "4",
                        "electrical_type": "output",
                    }
                ],
            },
            {
                "role_key": "expander",
                "name": "MCP23017 module",
                "thing_id": "thing-2",
                "pins": [
                    {
                        "id": "pin-2",
                        "name": "SDA",
                        "number": "3",
                        "electrical_type": "input",
                    }
                ],
            },
        ],
        "nets": [
            {
                "name": "SDA",
                "endpoints": [
                    {"role_key": "controller", "pin_id": "pin-1"},
                    {"role_key": "expander", "pin_id": "pin-2"},
                ],
            }
        ],
    }
    first = export_kicad_schematic(design)
    assert first == export_kicad_schematic(design)
    assert first.startswith("(kicad_sch")
    assert "(generator openlab)" in first
    assert "NET SDA: controller.pin-1 -- expander.pin-2" in first
    assert "(pin output line" in first
    assert "(pin input line" in first
    assert "(wire (pts" in first
    assert first.count("(") == first.count(")")
