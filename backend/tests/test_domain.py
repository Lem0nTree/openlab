from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openlab.models import InboxStatus, User
from openlab.providers import OpenAICompatibleProvider, is_local_endpoint, parse_json_object
from openlab.schemas import LocationCreate, StockAdjustment
from openlab.services import (
    _safe_http_url,
    adjustment_delta,
    canonical_profile,
    cosine_similarity,
    email_candidates,
    fallback_candidates,
    get_lab_thing,
    location_capture_url,
)


def test_inbox_states_are_explicit() -> None:
    assert InboxStatus.CAPTURED == "captured"
    assert InboxStatus.COMMITTED == "committed"
    assert InboxStatus.PARTIALLY_CONFIRMED == "partially_confirmed"
    assert InboxStatus.PARTIALLY_RECEIVED == "partially_received"


def test_quantity_math_is_decimal() -> None:
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


def test_count_adjustment_uses_exact_decimal_delta() -> None:
    assert adjustment_delta(Decimal("4.5"), Decimal("6.25")) == Decimal("1.75")
    assert adjustment_delta(Decimal("4.5"), Decimal(1)) == Decimal("-3.5")


def test_count_adjustment_rejects_negative_physical_counts() -> None:
    with pytest.raises(ValueError, match="negative"):
        adjustment_delta(Decimal(1), Decimal(-1))


def test_stock_adjustment_schema_rejects_negative_count_and_blank_reason() -> None:
    with pytest.raises(ValidationError):
        StockAdjustment(
            thing_id="thing",
            location_id="drawer",
            counted_quantity=-1,
            revision=1,
            note="count",
        )
    with pytest.raises(ValidationError):
        StockAdjustment(
            thing_id="thing", location_id="drawer", counted_quantity=1, revision=1, note=""
        )


def test_drawer_creation_rejects_hierarchy_fields() -> None:
    with pytest.raises(ValidationError):
        LocationCreate(name="Drawer B", parent_id="drawer-a")


def test_active_thing_lookup_excludes_archived_records() -> None:
    db = MagicMock()
    db.scalar.side_effect = ["lab-1", None]
    user = User(
        id="user-1",
        email="test@example.invalid",
        password_hash="unused",
        display_name="Test",
        is_owner=True,
    )
    with pytest.raises(HTTPException, match="Thing not found"):
        get_lab_thing(db, user, "thing-1")
    statement = db.scalar.call_args_list[1].args[0]
    assert "things.archived_at IS NULL" in str(statement)


def test_drawer_capture_url_prefers_canonical_public_url() -> None:
    assert location_capture_url(
        "drawer code",
        configured_url="http://pi3b.local:3000/",
        request_url="http://127.0.0.1:8000/",
    ) == "http://pi3b.local:3000/inbox?location=drawer+code"


def test_drawer_capture_url_falls_back_to_visible_origin() -> None:
    assert location_capture_url(
        "abc",
        configured_url=None,
        request_url="https://openlab.example/lab/",
    ) == "https://openlab.example/lab/inbox?location=abc"


def test_offline_inbox_parser_preserves_quantity_without_claiming_identity() -> None:
    candidate = fallback_candidates("7 x MCP23017 I2C GPIO expander")[0]
    assert candidate.quantity == Decimal(7)
    assert candidate.name == "MCP23017 I2C GPIO expander"
    assert candidate.identity_confidence == "low"


def test_provider_helpers_accept_openai_compatible_local_endpoints() -> None:
    assert is_local_endpoint("http://host.docker.internal:11434/v1")
    assert not is_local_endpoint("https://openrouter.ai/api/v1")
    assert parse_json_object('{"candidates": []}') == {"candidates": []}
    assert parse_json_object('I found one item:\n{"candidates": []}\nDone.') == {"candidates": []}


def test_openrouter_truncated_completion_retries_with_a_larger_budget(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self, content: object, finish_reason: str) -> None:
            self.content = content
            self.finish_reason = finish_reason

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {"finish_reason": self.finish_reason, "message": {"content": self.content}}
                ]
            }

    responses = [
        FakeResponse('{"candidates": [', "length"),
        FakeResponse('{"candidates": []}', "stop"),
    ]
    requests: list[dict[str, object]] = []

    def fake_post(_url: str, **kwargs: object) -> FakeResponse:
        requests.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("openlab.providers.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(
        base_url="https://openrouter.ai/api/v1", model="test", api_key="key"
    )
    assert provider.extract_inbox("MCP23017") == {"candidates": []}
    assert requests[0]["json"]["max_tokens"] == 2048  # type: ignore[index]
    assert requests[1]["json"]["max_tokens"] == 4096  # type: ignore[index]


def test_provider_accepts_openai_content_blocks(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:1/v1", model="test", api_key=None
    )

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": [{"type": "text", "text": '{"candidates": []}'}]},
                    }
                ]
            }

    monkeypatch.setattr("openlab.providers.httpx.post", lambda *_args, **_kwargs: FakeResponse())
    assert provider.extract_inbox("MCP23017") == {"candidates": []}


def test_email_extraction_keeps_order_lines_pending_and_classifies_tracking_links() -> None:
    candidates, evidence, body = email_candidates(
        b"From: orders@example.com\nSubject: Your order AB-1234\n\n2 x ESP32-C3 board\n3 x MCP23017 module\nTrack: https://example.com/tracking/AB-1234"
    )
    assert [candidate.name for candidate in candidates] == ["ESP32-C3 board", "MCP23017 module"]
    assert [candidate.quantity for candidate in candidates] == [Decimal(2), Decimal(3)]
    assert evidence["message_type"] == "order"
    assert evidence["links"] == [
        {"url": "https://example.com/tracking/AB-1234", "role": "tracking"}
    ]
    assert "ESP32-C3" in body


def test_product_link_rejects_loopback_before_fetching() -> None:
    try:
        _safe_http_url("http://127.0.0.1/private")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("loopback URLs must be rejected")


def test_canonical_profile_is_stable_and_excludes_inventory_state() -> None:
    one = canonical_profile(
        name="MCP23017",
        category="module",
        aliases=["GPIO expander", "GPIO expander"],
        capabilities=["I2C"],
    )
    two = canonical_profile(
        name="MCP23017", category="module", aliases=["GPIO expander"], capabilities=["I2C"]
    )
    assert one == two
    assert "quantity" not in one[0]
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
