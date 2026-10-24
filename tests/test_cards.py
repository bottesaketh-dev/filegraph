"""Tests for cards registry, schema compiler, and prompts (Section 9)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from filegraph.cards import (
    Card,
    compile_tool,
    load_card_file,
    load_registry,
    normalize_fact_key,
    system_prompt,
)
from filegraph.config import Settings


def test_registry_loads_builtins():
    """Verify registry loads all built-in cards from cards/ directory."""
    cards_dir = Path(__file__).resolve().parent.parent / "cards"
    reg = load_registry(cards_dir)

    assert "generic" in reg
    assert "identity" in reg
    assert "finance" in reg
    assert "legal" in reg
    assert "code" in reg

    assert reg["generic"].priority == 0
    assert reg["code"].llm is False
    assert len(reg.registry_hash) == 64


def test_invalid_yaml_raises_clear_error():
    """Verify invalid card YAML raises ValueError naming problem."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write("name: INVALID_NAME_CAPS\nversion: -1\n")
        ypath = Path(f.name)

    try:
        with pytest.raises(ValueError) as exc:
            load_card_file(ypath)
        assert "Invalid card YAML" in str(exc.value)
    finally:
        ypath.unlink(missing_ok=True)


def test_compile_tool():
    """Verify tool schema compilation for record_card."""
    card = Card(
        name="test_card",
        version=1,
        description="Test card",
        categories=["cat_a", "cat_b"],
        statuses=["status_1"],
        date_labels=["start", "expires"],
        entity_roles=["client", "provider"],
        preferred_fact_keys=["policy_no"],
        fields={"custom_bool": {"type": "boolean"}},
    )
    tool = compile_tool(card)
    assert tool["name"] == "record_card"
    schema = tool["input_schema"]
    props = schema["properties"]

    assert props["category"]["enum"] == ["cat_a", "cat_b"]
    assert props["status"]["enum"] == ["status_1"]
    assert "fields" in props
    assert props["fields"]["properties"]["custom_bool"]["type"] == ["boolean", "null"]


def test_system_prompt():
    """Verify system prompt includes user information and card instructions."""
    settings = Settings()
    card = Card(
        name="insurance",
        version=1,
        description="Insurance card",
        instructions="Insurer is the main entity.",
    )
    prompt = system_prompt(card, settings)
    assert "record_card" in prompt
    assert "Insurer is the main entity." in prompt


def test_normalize_fact_key():
    """Verify fact key normalization and aliases."""
    card = Card(
        name="finance",
        version=1,
        description="",
        fact_key_aliases={"policy_no": "policy_number"},
    )
    assert normalize_fact_key("Policy No", card) == "policy_number"
    assert normalize_fact_key("Gross--Salary!!", card) == "gross_salary"
