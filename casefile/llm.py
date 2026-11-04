"""Thin client over OpenAI-compatible Chat Completions.

The same code drives Muse Spark on Meta Model API and Muse Spark Open on
Baseten (or any vLLM/SGLang server), so the agent and the verifier can run on
different Meta models.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import ModelConfig


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed(self) -> dict:
        try:
            value = json.loads(self.arguments or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {"__invalid_json__": self.arguments}


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall]
    message: dict  # assistant message to append back into the conversation
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: ModelConfig, mock=None) -> None:
        self.cfg = cfg
        self.mock = mock
        self._client = None
        if cfg.provider != "mock":
            from openai import OpenAI

            if not cfg.api_key:
                raise LLMError(f"Missing API key for provider {cfg.provider!r}; see .env.example")
            self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=180, max_retries=2)

    @property
    def label(self) -> str:
        return self.cfg.label

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: Any = None,
             json_mode: bool = False, max_tokens: int = 16000, reasoning_effort: str | None = None) -> Reply:
        if self.cfg.provider == "mock":
            return self.mock.chat(messages, tools=tools, json_mode=json_mode)
        kwargs: dict[str, Any] = {"model": self.cfg.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        effort = reasoning_effort or self.cfg.reasoning_effort
        if effort and self.cfg.reasoning_effort:  # only send it to providers configured for it
            kwargs["reasoning_effort"] = effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # retry once without optional params some servers reject
            dropped = [k for k in ("reasoning_effort", "response_format") if k in kwargs]
            if not dropped:
                raise LLMError(f"{self.label}: {exc}") from exc
            for k in dropped:
                kwargs.pop(k)
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise LLMError(f"{self.label}: {exc2}") from exc2
        choice = resp.choices[0]
        msg = choice.message
        if not (msg.content or msg.tool_calls) and choice.finish_reason == "length":
            # Reasoning used the whole token budget before any answer: retry with more room, less thinking.
            kwargs["max_tokens"] = max_tokens * 2
            if "reasoning_effort" in kwargs:
                kwargs["reasoning_effort"] = "low"
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                raise LLMError(f"{self.label}: {exc}") from exc
            choice = resp.choices[0]
            msg = choice.message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (msg.tool_calls or [])]
        assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return Reply(msg.content or "", calls, assistant, usage, time.monotonic() - t0)


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (tolerates code fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")
