"""Model registry — reads every secret from environment variables (Section 5.2).

Constructing a client must not make a network call.  Entries whose credentials
are missing are silently skipped so tests can import this module.
"""

from __future__ import annotations

import os
from typing import Any

LLM_INSTANCES: dict[str, dict[str, Any]] = {}


def _env(key: str, default: str | None = None) -> str | None:
    """Read an environment variable, returning *default* if empty/absent."""
    val = os.environ.get(key, "")
    return val if val else default


def build_instances() -> dict[str, dict[str, Any]]:
    """Build the model registry dict.

    Called once at module import.  Models whose credentials are absent are
    simply left out, keeping the rest of the application functional.
    """
    instances: dict[str, dict[str, Any]] = {}

    # --- OpenAI models ---
    openai_key = _env("OPENAI_API_KEY") or _env("LLMFOUNDRY_TOKEN")
    openai_base = _env("OPENAI_BASE_URL")
    if openai_key:
        try:
            from langchain_openai import ChatOpenAI

            instances["GPT_4_1_MINI"] = {
                "MODEL": ChatOpenAI(
                    model="gpt-4.1-mini",
                    temperature=0,
                    api_key=openai_key,
                    base_url=openai_base,
                ),
                "VISION": True,
            }
            instances["GPT_4_1"] = {
                "MODEL": ChatOpenAI(
                    model="gpt-4.1",
                    temperature=0,
                    api_key=openai_key,
                    base_url=openai_base,
                ),
                "VISION": True,
            }
            instances["GPT_5"] = {
                "MODEL": ChatOpenAI(
                    model="gpt-5",
                    temperature=0,
                    api_key=openai_key,
                    base_url=openai_base,
                ),
                "VISION": True,
            }
        except Exception:
            pass  # langchain_openai not installed or construction failed

    # --- Anthropic models ---
    anthropic_key = _env("ANTHROPIC_API_KEY")
    anthropic_base = _env("ANTHROPIC_BASE_URL")
    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic

            instances["CLAUDE_SONNET"] = {
                "MODEL": ChatAnthropic(
                    model="claude-sonnet-4-20250514",
                    api_key=anthropic_key,
                    base_url=anthropic_base,
                ),
                "VISION": True,
            }
        except Exception:
            pass

    # --- Google Gemini (optional) ---
    google_key = _env("GOOGLE_API_KEY")
    if google_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            instances["GEMINI_PRO"] = {
                "MODEL": ChatGoogleGenerativeAI(
                    model="gemini-2.5-pro",
                    google_api_key=google_key,
                    temperature=0,
                ),
                "VISION": True,
            }
        except Exception:
            pass

    # --- Ollama (local) ---
    ollama_model = _env("OLLAMA_MODEL", "qwen2.5:14b")
    try:
        from langchain_openai import ChatOpenAI

        instances["OLLAMA_LOCAL"] = {
            "MODEL": ChatOpenAI(
                model=ollama_model,
                api_key="ollama",
                base_url="http://localhost:11434/v1",
                temperature=0,
            ),
            "VISION": False,
        }
    except Exception:
        pass

    return instances


# Build at import time (no network calls).
LLM_INSTANCES = build_instances()
