"""LLM access layer + fake LLM (Section 5).

Public API
----------
get_chat(model_key)            -> LangChain chat model (memoised)
call_tool(model_key, ...)      -> (args_dict, usage_dict)
chat_with_tools(model_key, tools) -> chat model bound to tools (for agent)
supports_vision(model_key)     -> bool
model_for_policy(policy, purpose) -> model_key | None
check_credentials(model_key)   -> None (raise on failure)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Fake LLM support (Section 5.3)
# ---------------------------------------------------------------------------

_FAKE_LLM = os.environ.get("FG_FAKE_LLM", "0") == "1"
_fake_responses: list[dict] | None = None


def _load_fake_responses() -> list[dict]:
    global _fake_responses
    if _fake_responses is not None:
        return _fake_responses
    repo_root = Path(__file__).resolve().parent.parent
    for candidate in (
        repo_root / "tests" / "fake_responses.json",
        Path.cwd() / "tests" / "fake_responses.json",
        Path(__file__).resolve().parent / "tests" / "fake_responses.json",
    ):
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                _fake_responses = json.load(f)
                return _fake_responses
    _fake_responses = []
    return _fake_responses


def _minimal_args_from_schema(schema: dict) -> dict:
    """Generate minimal valid args from a JSON schema (required fields only)."""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    result: dict[str, Any] = {}
    for key, prop in props.items():
        if key not in required:
            continue
        result[key] = _default_for_prop(prop)
    return result


def _default_for_prop(prop: dict) -> Any:
    """Pick the simplest default for a JSON Schema property."""
    t = prop.get("type", "string")
    if isinstance(t, list):
        # nullable types like ["string", "null"]
        if "null" in t:
            return None
        t = t[0]
    if t == "string":
        enum = prop.get("enum")
        if enum:
            non_null = [v for v in enum if v is not None]
            return non_null[0] if non_null else None
        return "unknown"
    if t == "number" or t == "integer":
        return 0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return None


def _flatten_content(content: Any) -> str:
    """Flatten content (string or list of blocks) to a single string for matching."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _fake_call_tool(
    tool: dict, content: Any
) -> tuple[dict, dict]:
    """Return a canned or minimal response without network access."""
    responses = _load_fake_responses()
    flat = _flatten_content(content).lower()
    tool_name = tool["name"]
    # Search for a matching canned response
    for entry in responses:
        if entry.get("tool") == tool_name:
            match_str = entry.get("match", "")
            if match_str.lower() in flat:
                return entry["args"], {"input": 0, "output": 0}
    # Fallback: generate minimal valid args from the schema
    schema = tool.get("input_schema", tool.get("parameters", {}))
    return _minimal_args_from_schema(schema), {"input": 0, "output": 0}


# ---------------------------------------------------------------------------
# Real LLM helpers
# ---------------------------------------------------------------------------

def _get_instances() -> dict:
    """Import the registry lazily to avoid circular imports."""
    # llm_instances.py lives at the repo root (next to fg.py)
    import importlib
    try:
        mod = importlib.import_module("llm_instances")
    except ModuleNotFoundError:
        # Might be in a test environment — try relative
        parent = Path(__file__).resolve().parent.parent
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        mod = importlib.import_module("llm_instances")
    return mod.LLM_INSTANCES


@lru_cache(maxsize=None)
def get_chat(model_key: str):
    """Return the LangChain chat model for *model_key* (memoised)."""
    instances = _get_instances()
    if model_key not in instances:
        raise ValueError(
            f"Model key '{model_key}' not found in LLM_INSTANCES. "
            f"Available: {list(instances.keys())}. "
            f"Check your .env for the required API key."
        )
    return instances[model_key]["MODEL"]


def supports_vision(model_key: str) -> bool:
    """Check if the model supports image/vision content."""
    instances = _get_instances()
    entry = instances.get(model_key, {})
    return entry.get("VISION", True)


def model_for_policy(policy: str, purpose: str) -> str | None:
    """Choose a model key based on the root's LLM policy and the purpose.

    *purpose* ∈ {"classify", "extract", "escalate", "agent", "filing"}.
    """
    if policy == "metadata_only":
        return None
    env_map = {
        "classify": "FG_CLASSIFY_MODEL",
        "extract": "FG_EXTRACT_MODEL",
        "escalate": "FG_ESCALATE_MODEL",
        "agent": "FG_AGENT_MODEL",
        "filing": "FG_FILING_MODEL",
    }
    if policy == "local":
        return os.environ.get("FG_LOCAL_MODEL", "OLLAMA_LOCAL")
    env_key = env_map.get(purpose)
    if env_key:
        return os.environ.get(env_key, "GPT_4_1")
    return "GPT_4_1"


def check_credentials(model_key: str) -> None:
    """Fail early with a clear message if the model is not configured."""
    if _FAKE_LLM:
        return
    instances = _get_instances()
    if model_key not in instances:
        raise ValueError(
            f"Model '{model_key}' is not available. "
            f"Configured models: {list(instances.keys())}. "
            f"Check your .env for the required API key."
        )


# ---------------------------------------------------------------------------
# Content conversion
# ---------------------------------------------------------------------------

def _convert_content(content: Any) -> Any:
    """Convert Anthropic-style content blocks to LangChain message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        from langchain_core.messages import HumanMessage
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    src = block.get("source", {})
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{src.get('media_type','image/jpeg')};base64,{src.get('data','')}"
                        },
                    })
            elif isinstance(block, str):
                parts.append({"type": "text", "text": block})
        return parts
    return str(content)


def _to_openai_tool(tool: dict) -> dict:
    """Convert an Anthropic-shaped tool dict to OpenAI/LangChain shape."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", tool.get("parameters", {})),
        },
    }


# ---------------------------------------------------------------------------
# Public call_tool — forced tool calling with fallback (Section 5.1)
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_RETRY_DELAYS = [2, 8, 30]


def _is_auth_error(exc: Exception) -> bool:
    """Check if the exception is an authentication / permission / not-found error."""
    name = type(exc).__name__
    if name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        return True
    # Check for HTTP status codes
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status in (401, 403, 404):
        return True
    return False


def _is_retryable(exc: Exception) -> bool:
    """Check if the exception is a rate-limit or server error worth retrying."""
    name = type(exc).__name__
    if "RateLimitError" in name:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status and status in _RETRYABLE_STATUS_CODES:
        return True
    return False


def call_tool(
    model_key: str,
    arg1: Any = None,
    arg2: Any = None,
    arg3: Any = None,
    *,
    system: str = "",
    tool: dict | None = None,
    content: Any = None,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> tuple[dict, dict]:
    """Force one tool call. Returns (args, usage)."""
    actual_system = system
    actual_tool = tool
    actual_content = content

    if isinstance(arg1, dict):
        # call_tool(model_key, tool, content, system=...)
        actual_tool = arg1
        if arg2 is not None:
            actual_content = arg2
        if arg3 is not None and isinstance(arg3, str):
            actual_system = arg3
    elif isinstance(arg1, str):
        # call_tool(model_key, system, content, tool)
        actual_system = arg1
        if arg2 is not None:
            actual_content = arg2
        if arg3 is not None and isinstance(arg3, dict):
            actual_tool = arg3

    if actual_tool is None:
        raise ValueError("Missing tool argument in call_tool")

    # ---- Fake LLM path ----
    if _FAKE_LLM:
        return _fake_call_tool(actual_tool, actual_content)

    from langchain_core.messages import HumanMessage, SystemMessage

    chat = get_chat(model_key)
    messages = [
        SystemMessage(content=actual_system),
        HumanMessage(content=_convert_content(actual_content)),
    ]

    lc_tools = [_to_openai_tool(actual_tool)]
    tool_name = actual_tool["name"]

    # Attempt 1: tool_choice = specific tool name
    for attempt in range(3):
        try:
            bound = chat.bind_tools(lc_tools, tool_choice=tool_name)
            resp = bound.invoke(messages, config={"max_tokens": max_tokens})
            args, usage = _extract_tool_call(resp, tool_name, actual_tool)
            if args is not None:
                return args, usage
            break  # no tool call — fall through to retry strategies
        except Exception as exc:
            if _is_auth_error(exc):
                raise
            err_str = str(exc).lower()
            if "does not support tools" in err_str or "tool_choice" in err_str or "tools" in err_str:
                break  # provider doesn't support tools — fall through to Attempt 3
            if _is_retryable(exc) and attempt < 2:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            if attempt == 2:
                raise
            break

    # Attempt 2: tool_choice = "any" / "required"
    for tc in ("any", "required"):
        try:
            bound = chat.bind_tools(lc_tools, tool_choice=tc)
            resp = bound.invoke(messages, config={"max_tokens": max_tokens})
            args, usage = _extract_tool_call(resp, tool_name, actual_tool)
            if args is not None:
                return args, usage
        except Exception:
            continue

    # Attempt 3: call WITHOUT tools, append schema to prompt instructions, and parse JSON from text output
    try:
        schema_json = json.dumps(actual_tool.get("input_schema", actual_tool.get("parameters", {})), indent=2)
        json_instruction = (
            f"\n\nYou MUST record this document by outputting a valid JSON object matching the following schema for '{tool_name}':\n"
            f"```json\n{schema_json}\n```\n"
            f"Keep your response concise. Include only the top 5 most important facts so that the JSON is complete and never truncated.\n"
            f"Respond ONLY with the JSON object, with no markdown fences or other text."
        )

        fallback_messages = list(messages)
        if fallback_messages and isinstance(fallback_messages[-1].content, str):
            fallback_messages[-1] = HumanMessage(content=fallback_messages[-1].content + json_instruction)
        elif fallback_messages and isinstance(fallback_messages[-1].content, list):
            new_blocks = list(fallback_messages[-1].content) + [{"type": "text", "text": json_instruction}]
            fallback_messages[-1] = HumanMessage(content=new_blocks)
        else:
            fallback_messages.append(HumanMessage(content=json_instruction))

        resp = chat.invoke(fallback_messages, config={"max_tokens": max_tokens})
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        args = _parse_json_from_text(text)
        if args is not None:
            return args, _extract_usage(resp)
    except Exception:
        pass

    raise ValueError(f"Model did not call {tool_name}")


def _extract_tool_call(
    resp: Any, tool_name: str, tool: dict
) -> tuple[dict | None, dict]:
    """Extract tool call args from a LangChain response."""
    usage = _extract_usage(resp)
    if hasattr(resp, "tool_calls") and resp.tool_calls:
        for tc in resp.tool_calls:
            if tc.get("name") == tool_name:
                return tc.get("args", {}), usage
        # Return the first tool call if names don't match exactly
        return resp.tool_calls[0].get("args", {}), usage

    # If tool_calls attribute is empty, check if the model wrote JSON or a tool call in text content
    content = getattr(resp, "content", "")
    if isinstance(content, str) and content.strip():
        parsed = _parse_json_from_text(content)
        if parsed and isinstance(parsed, dict):
            return parsed, usage

    return None, usage


def _extract_usage(resp: Any) -> dict:
    """Extract token usage from a LangChain response."""
    meta = getattr(resp, "usage_metadata", None)
    if meta:
        return {
            "input": getattr(meta, "input_tokens", 0) or meta.get("input_tokens", 0),
            "output": getattr(meta, "output_tokens", 0) or meta.get("output_tokens", 0),
        }
    return {"input": 0, "output": 0}


def _repair_truncated_json(s: str) -> dict | None:
    """Attempt to repair and parse truncated JSON (e.g. cut off by max_tokens)."""
    start = s.find("{")
    if start == -1:
        return None
    s = s[start:].strip()

    # Progressively trim incomplete trailing tokens and balance brackets
    for trim_len in range(0, min(len(s), 600)):
        sub = s if trim_len == 0 else s[:-trim_len].rstrip()
        stk = []
        in_s = False
        esc = False
        for c in sub:
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"':
                in_s = not in_s
                continue
            if not in_s:
                if c in ("{", "["):
                    stk.append("}" if c == "{" else "]")
                elif c in ("}", "]") and stk and stk[-1] == c:
                    stk.pop()

        cand = sub + ('"' if in_s else "") + "".join(reversed(stk))
        try:
            val = json.loads(cand)
            if isinstance(val, dict):
                return val
        except Exception:
            continue
    return None


def _parse_json_from_text(text: str) -> dict | None:
    """Try to find and parse a JSON object from model text output."""
    if not text:
        return None

    # Handle Qwen/Ollama <tool_call> tags
    tc_match = re.search(r"<tool_call>([\s\S]*?)</tool_call>", text)
    if tc_match:
        text = tc_match.group(1)

    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()

    # Find the first { ... } block
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if depth == 0 and end > start:
        try:
            data = json.loads(text[start:end])
            if isinstance(data, dict):
                # If wrapped in {name: ..., arguments: {...}}
                if "arguments" in data and isinstance(data["arguments"], dict):
                    return data["arguments"]
                if "parameters" in data and isinstance(data["parameters"], dict):
                    return data["parameters"]
                return data
        except json.JSONDecodeError:
            pass

    # If direct parse failed or JSON was truncated (depth > 0), attempt repair
    repaired = _repair_truncated_json(text[start:])
    if repaired:
        if "arguments" in repaired and isinstance(repaired["arguments"], dict):
            return repaired["arguments"]
        if "parameters" in repaired and isinstance(repaired["parameters"], dict):
            return repaired["parameters"]
        return repaired

    return None


# ---------------------------------------------------------------------------
# chat_with_tools — for the agent loop (Section 15)
# ---------------------------------------------------------------------------

def chat_with_tools(model_key: str, tools: list[dict]):
    """Return a chat model bound to multiple tools, for the agent loop.

    Under FG_FAKE_LLM, returns a FakeChatModel.
    """
    if _FAKE_LLM:
        return _FakeAgentChat(tools)

    chat = get_chat(model_key)
    lc_tools = [_to_openai_tool(t) for t in tools]
    return chat.bind_tools(lc_tools)


class _FakeAgentChat:
    """A fake chat model for agent testing (Section 5.3)."""

    def __init__(self, tools: list[dict]):
        self._tools = tools
        self._script: list[dict] | None = None
        self._step = 0
        script_path = os.environ.get("FG_FAKE_AGENT_SCRIPT")
        if script_path and Path(script_path).exists():
            with open(script_path, encoding="utf-8") as f:
                self._script = json.load(f)

    def invoke(self, messages: list, **kwargs) -> Any:
        from langchain_core.messages import AIMessage

        if self._script and self._step < len(self._script):
            entry = self._script[self._step]
            self._step += 1
            if "tool_calls" in entry:
                return AIMessage(
                    content="",
                    tool_calls=entry["tool_calls"],
                )
            return AIMessage(content=entry.get("content", "(fake answer)"))

        return AIMessage(content="(fake answer)")
