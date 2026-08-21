from decimal import Decimal

from openlab.models import InboxStatus
from openlab.providers import is_local_endpoint, parse_json_object
from openlab.services import fallback_candidates


def test_inbox_states_are_explicit() -> None:
    assert InboxStatus.CAPTURED == "captured"
    assert InboxStatus.COMMITTED == "committed"


def test_quantity_math_is_decimal() -> None:
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


def test_offline_inbox_parser_preserves_quantity_without_claiming_identity() -> None:
    candidate = fallback_candidates("7 x MCP23017 I2C GPIO expander")[0]
    assert candidate.quantity == Decimal(7)
    assert candidate.name == "MCP23017 I2C GPIO expander"
    assert candidate.confidence == "generic"


def test_provider_helpers_accept_openai_compatible_local_endpoints() -> None:
    assert is_local_endpoint("http://host.docker.internal:11434/v1")
    assert not is_local_endpoint("https://openrouter.ai/api/v1")
    assert parse_json_object('{"candidates": []}') == {"candidates": []}
