"""Card registry and schema compiler (Section 9)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from filegraph.config import Settings

# JSON schema for validating card YAML files (Section 9.1)
CARD_YAML_SCHEMA = {
    "type": "object",
    "required": ["name", "version", "description", "llm", "priority"],
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z0-9_]+$"},
        "version": {"type": "integer", "minimum": 1},
        "description": {"type": "string"},
        "llm": {"type": "boolean"},
        "priority": {"type": "integer"},
        "absorbs": {"type": "array", "items": {"type": "string"}},
        "match": {
            "type": ["object", "null"],
            "properties": {
                "extensions": {"type": "array", "items": {"type": "string"}},
                "path_globs": {"type": "array", "items": {"type": "string"}},
                "filename_regex": {"type": "string"},
                "keywords_any": {"type": "array", "items": {"type": "string"}},
                "min_keyword_hits": {"type": "integer"},
            },
        },
        "categories": {"type": "array", "items": {"type": "string"}},
        "statuses": {"type": "array", "items": {"type": "string"}},
        "main_entity": {
            "type": ["object", "null"],
            "properties": {
                "role": {"type": "string"},
                "kind": {"type": "string"},
            },
        },
        "main_date": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "null"},
            ]
        },
        "date_labels": {"type": "array", "items": {"type": "string"}},
        "entity_roles": {"type": "array", "items": {"type": "string"}},
        "preferred_fact_keys": {"type": "array", "items": {"type": "string"}},
        "fact_key_aliases": {"type": "object", "additionalProperties": {"type": "string"}},
        "fields": {"type": "object"},
        "relations": {"type": "array", "items": {"type": "string"}},
        "instructions": {"type": "string"},
        "review_rules": {"type": "object"},
    },
}

_YAML_VALIDATOR = Draft202012Validator(CARD_YAML_SCHEMA)


@dataclass
class Card:
    """A card definition loaded from YAML (Section 9.1)."""
    name: str
    version: int
    description: str
    llm: bool = True
    priority: int = 0
    absorbs: list[str] = field(default_factory=list)
    match: dict[str, Any] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    main_entity: dict[str, Any] = field(default_factory=dict)
    main_date: str | list[str] = "date"
    date_labels: list[str] = field(default_factory=list)
    entity_roles: list[str] = field(default_factory=list)
    preferred_fact_keys: list[str] = field(default_factory=list)
    fact_key_aliases: dict[str, str] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    relations: list[str] = field(default_factory=list)
    instructions: str = ""
    review_rules: dict[str, Any] = field(default_factory=dict)


class Registry:
    """Card registry managing loaded cards and schema compilation (Section 9.5)."""

    def __init__(self, cards: dict[str, Card]):
        self.cards = cards
        # Calculate registry hash for caching
        self.registry_hash = self._compute_hash()

    def get(self, name: str) -> Card | None:
        return self.cards.get(name)

    def __getitem__(self, name: str) -> Card:
        if name not in self.cards:
            raise KeyError(f"Unknown card type: '{name}'")
        return self.cards[name]

    def __contains__(self, name: str) -> bool:
        return name in self.cards

    def _compute_hash(self) -> str:
        # sha256 of sorted (name, version, match, absorbs)
        items = []
        for name in sorted(self.cards.keys()):
            c = self.cards[name]
            items.append((
                c.name,
                c.version,
                json.dumps(c.match, sort_keys=True),
                json.dumps(sorted(c.absorbs)),
            ))
        payload = json.dumps(items, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def load_card_file(path: Path) -> Card:
    """Load and validate one card YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    errors = list(_YAML_VALIDATOR.iter_errors(raw))
    if errors:
        msg = f"Invalid card YAML '{path.name}': " + "; ".join(e.message for e in errors)
        raise ValueError(msg)

    return Card(
        name=raw["name"],
        version=raw.get("version", 1),
        description=raw.get("description", ""),
        llm=raw.get("llm", True),
        priority=raw.get("priority", 0),
        absorbs=raw.get("absorbs") or [],
        match=raw.get("match") or {},
        categories=raw.get("categories") or [],
        statuses=raw.get("statuses") or [],
        main_entity=raw.get("main_entity") or {},
        main_date=raw.get("main_date") or "date",
        date_labels=raw.get("date_labels") or [],
        entity_roles=raw.get("entity_roles") or [],
        preferred_fact_keys=raw.get("preferred_fact_keys") or [],
        fact_key_aliases=raw.get("fact_key_aliases") or {},
        fields=raw.get("fields") or {},
        relations=raw.get("relations") or [],
        instructions=raw.get("instructions") or "",
        review_rules=raw.get("review_rules") or {},
    )


def load_registry(cards_dir: Path | str) -> Registry:
    """Load all cards from cards_dir (excluding proposed/)."""
    cdir = Path(cards_dir).resolve()
    cards: dict[str, Card] = {}

    if cdir.is_dir():
        for ypath in sorted(cdir.glob("*.yaml")):
            # Skip proposed cards directory
            if "proposed" in ypath.parts:
                continue
            card = load_card_file(ypath)
            cards[card.name] = card

    if "generic" not in cards:
        # Fallback generic card definition (Appendix B.1)
        cards["generic"] = Card(
            name="generic",
            version=1,
            description="Any document that does not fit a more specific card.",
            llm=True,
            priority=0,
            absorbs=[],
            categories=[
                "finance", "medical", "identity", "legal", "work", "education",
                "travel", "housing", "vehicle", "insurance", "tax", "personal", "other",
            ],
            statuses=["active", "expired", "draft", "executed", "final", "superseded", "cancelled", "unknown"],
            date_labels=["expires", "due", "effective", "start", "issued", "event", "signed", "other"],
            entity_roles=["counterparty", "issuer", "holder", "subject", "provider", "client", "employer", "employee", "sender", "recipient", "other"],
        )

    return Registry(cards)


# ---------------------------------------------------------------------------
# Schema and prompt compiler (Section 9.3, 9.4)
# ---------------------------------------------------------------------------

def _field_to_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert typed card field to JSON Schema property (Section 9.2)."""
    ftype = spec.get("type", "text")
    desc = spec.get("description")

    if ftype == "text":
        s: dict[str, Any] = {"type": ["string", "null"]}
    elif ftype == "number":
        s = {"type": ["number", "null"]}
    elif ftype == "boolean":
        s = {"type": ["boolean", "null"]}
    elif ftype == "date":
        s = {"type": ["string", "null"], "description": "ISO YYYY-MM-DD or null"}
    elif ftype == "amount":
        s = {
            "type": ["object", "null"],
            "properties": {
                "value": {"type": "number"},
                "currency": {"type": "string"},
                "as_written": {"type": "string"},
            },
        }
    elif ftype == "enum":
        vals = spec.get("values", [])
        s = {"type": ["string", "null"], "enum": list(vals) + [None]}
    elif ftype == "list":
        s = {"type": "array", "items": {"type": "string"}}
    else:
        s = {"type": ["string", "null"]}

    if desc:
        s["description"] = desc
    return s


def compile_tool(card: Card) -> dict[str, Any]:
    """Compile a Card into the record_card tool specification (Section 9.3)."""
    date_labels = list(card.date_labels)
    if "other" not in date_labels:
        date_labels.append("other")

    relations = list(card.relations)
    if "references" not in relations:
        relations.append("references")

    main_role = card.main_entity.get("role", "counterparty")

    props: dict[str, Any] = {
        "category": {"type": "string", "enum": card.categories or ["other"]},
        "title": {
            "type": "string",
            "description": "Short human title, e.g. 'ICICI Lombard - Car insurance policy (2026)'",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentences: what this is and what it does",
        },
        "status": {"type": "string", "enum": card.statuses or ["unknown"]},
        "language": {
            "type": ["string", "null"],
            "description": "ISO 639-1 code of the main language",
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["person", "organization", "place", "product", "other"],
                    },
                    "role": {
                        "type": ["string", "null"],
                        "description": f"One of: {', '.join(card.entity_roles)}" if card.entity_roles else "Role",
                    },
                },
                "required": ["name", "kind"],
            },
        },
        "main_entity": {
            "type": ["string", "null"],
            "description": f"The {main_role}: full name as written; never the user or user's organizations",
        },
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": date_labels},
                    "label_detail": {"type": ["string", "null"]},
                    "date": {
                        "type": "string",
                        "description": "ISO YYYY-MM-DD; YYYY-MM if only month known",
                    },
                    "basis": {
                        "type": "string",
                        "enum": ["stated", "inferred", "signature", "partial"],
                    },
                    "quote": {
                        "type": "string",
                        "description": "Exact words, max 300 chars",
                    },
                    "page": {"type": ["integer", "null"]},
                },
                "required": ["label", "date", "quote"],
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": f"snake_case. Prefer: {', '.join(card.preferred_fact_keys)}" if card.preferred_fact_keys else "snake_case key",
                    },
                    "value": {"type": "string"},
                    "number": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"], "description": "ISO currency code or unit"},
                    "quote": {"type": ["string", "null"]},
                    "page": {"type": ["integer", "null"]},
                },
                "required": ["key", "value"],
            },
        },
        "references": {
            "type": "array",
            "description": "Other documents this one points to",
            "items": {
                "type": "object",
                "properties": {
                    "relation": {"type": "string", "enum": relations},
                    "title": {"type": "string"},
                    "date": {"type": ["string", "null"]},
                    "entity": {"type": ["string", "null"]},
                    "quote": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                },
                "required": ["relation", "title", "quote"],
            },
        },
        "suggested_type": {
            "type": "string",
            "description": "The most specific document type in snake_case (e.g. car_insurance_policy, salary_slip)",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "review_reasons": {"type": "array", "items": {"type": "string"}},
    }

    required_fields = [
        "category", "title", "summary", "status", "entities",
        "dates", "facts", "references", "suggested_type", "confidence", "review_reasons"
    ]

    if card.fields:
        f_props = {fname: _field_to_schema(fspec) for fname, fspec in card.fields.items()}
        props["fields"] = {
            "type": "object",
            "properties": f_props,
        }
        required_fields.append("fields")

    return {
        "name": "record_card",
        "description": f"Record structured card for document of type {card.name}.",
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": required_fields,
        },
    }


def system_prompt(card: Card, settings: Settings) -> str:
    """Concatenate core extraction prompt, card specifics, and custom instructions (Section 9.4)."""
    me_names = ", ".join(settings.me.names) or "the user"
    me_orgs = ", ".join(settings.me.organizations) or "none"

    core = f"""You catalogue documents from a person's computer so they can later be found and answered about precisely.
The user (never "the other party"): {me_names}. The user's own organizations: {me_orgs}.

Read the document content you are given and call record_card exactly once.

General
- The file path and folder names are hints only; the content decides.
- Use only what the document says. Never invent names, numbers or dates. If something is not in the document, leave it out.
- title: short and human, "<Who> - <What> (<year>)", e.g. "ICICI Lombard - Car insurance policy (2026)".
- summary: 2-3 plain sentences: what this is, who it involves, what it does or asks for.
- suggested_type: the most specific kind of document in snake_case (car_insurance_policy, salary_slip, rental_agreement, electricity_bill, passport, offer_letter). Always fill it, even when the card type already fits.

Entities
- List the people and organizations that matter (parties, issuer, sender, recipient, employer, provider). Full names as written.
- main_entity is the most important party that is NOT the user or the user's own organizations.

Dates
- Every date as ISO YYYY-MM-DD. If only month and year are given, use YYYY-MM and it will be flagged.
- Give each date the best label from the allowed list; use "other" with label_detail when none fits.
- basis: "stated" when the document states the date for that purpose; "signature" when it comes from a signature or stamp; "inferred" when you derived it (say how in review_reasons).
- quote: the exact words the date comes from (max 300 chars), and the page/section number from the "=== Page N ===" or "=== Section N ===" markers. Handwritten or stamped dates on scanned pages count.

Facts
- Record the specific values a person would look up later: numbers, amounts, identifiers, names of plans or products, addresses.
- Use the preferred keys when they fit; otherwise a short snake_case key.
- Amounts: value as written, number as a plain number, unit as an ISO currency code.
- Payment card and bank account numbers: record only the last 4 digits, e.g. "XXXX1234".

References
- List other documents this one points to (amends, renews, replies to, pays, is issued under...), with the referenced document's title and date if stated, and the exact quote.

Quality
- confidence: high only when the key fields are clearly stated; low when the content is partial, illegible or ambiguous.
- review_reasons: anything a careful person should double-check. Empty when nothing.
- Keep quotes verbatim and short.
"""

    parts = [core.strip()]

    # Card section
    card_sec = [
        f"\nCard type: {card.name} - {card.description}",
        f"Allowed categories: {', '.join(card.categories)}",
        f"Allowed statuses: {', '.join(card.statuses)}",
        f"Allowed date labels: {', '.join(card.date_labels)}",
        f"Allowed entity roles: {', '.join(card.entity_roles)}",
    ]
    if card.preferred_fact_keys:
        card_sec.append(f"Preferred fact keys: {', '.join(card.preferred_fact_keys)}")

    parts.append("\n".join(card_sec))

    if card.instructions:
        parts.append(f"\nSpecific Instructions for {card.name}:\n{card.instructions.strip()}")

    return "\n\n".join(parts)


def normalize_fact_key(key: str, card: Card | None = None, generic_card: Card | None = None) -> str:
    """Normalize fact keys: lowercase, alphanumeric -> _, collapse, apply aliases (Section 9.5)."""
    k = key.lower().strip()
    k = re.sub(r"[^a-z0-9]+", "_", k)
    k = re.sub(r"_+", "_", k).strip("_")

    if card and k in card.fact_key_aliases:
        return card.fact_key_aliases[k]
    if generic_card and k in generic_card.fact_key_aliases:
        return generic_card.fact_key_aliases[k]

    return k
