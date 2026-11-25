"""Model clients: any OpenAI-compatible endpoint (Ollama, llama.cpp server, vLLM) and Claude.

Every client exposes the same `complete_json(messages)` call plus `name`, `model` and `kind`
("local" or "cloud"), so the pipeline can try several local models in order and the UI can say which
model answered and how fast.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .models import Category
from .runbooks import Runbook

SYSTEM_PROMPT = """You are an SRE assistant doing root cause analysis for a Kubernetes incident.
Rules:
- Use ONLY the evidence given. Never invent metrics, logs or events.
- "Detected signals" come from deterministic rules and are reliable facts.
- Pick exactly one category from: {categories}.
- Pick runbook_id only from the candidate runbooks listed, or null if none fits.
- Write root_cause in {language_name}, at most 3 sentences, plain and specific.
- evidence: 1-4 short items copied or closely paraphrased from the context.
- confidence: 0.0-1.0; below 0.5 if evidence is thin or contradictory.
Reply with a single JSON object and nothing else:
{{"category": "...", "root_cause": "...", "confidence": 0.0, "evidence": ["..."], "runbook_id": null}}"""

_LANGUAGES = {"tr": "Turkish", "en": "English"}


def build_messages(context: str, runbooks: list[Runbook], language: str = "en") -> list[dict[str, str]]:
    system = SYSTEM_PROMPT.format(
        categories=", ".join(c.value for c in Category),
        language_name=_LANGUAGES.get(language, language),
    )
    if runbooks:
        rb_text = "\n".join(f"- {rb.id} [{rb.category.value}]: {rb.title}. {rb.description}" for rb in runbooks)
    else:
        rb_text = "- (no matching runbook)"
    user = f"{context}\n\n## Candidate runbooks\n{rb_text}\n\nReturn the JSON object now."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply (tolerates think tags and code fences)."""
    text = _THINK.sub("", text).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in model reply")


class LLMClient:
    """OpenAI-compatible chat completions (Ollama, llama.cpp server, vLLM, or an approved cloud endpoint)."""

    provider = "openai"

    def __init__(self, base_url: str, model: str, api_key: str = "local", timeout_s: float = 180,
                 temperature: float = 0.1, max_tokens: int = 800, client: httpx.AsyncClient | None = None,
                 name: str | None = None, kind: str = "local"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name or model
        self.kind = kind
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def complete_json(self, messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        resp = await self.client.post(f"{self.base_url}/chat/completions", json=payload)
        resp.raise_for_status()
        body = resp.json()
        content = body["choices"][0]["message"].get("content") or ""
        return parse_json_object(content), body.get("usage", {})


class ClaudeClient:
    """Claude through the official Anthropic SDK (optional dependency: pip install 'aiops-copilot[claude]').

    Only ever used as the cloud second opinion, so it only receives the anonymised summary.
    """

    provider = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", api_key: str | None = None, timeout_s: float = 60,
                 max_tokens: int = 16000, effort: str | None = None, fallbacks: bool = True,
                 client: Any = None, name: str | None = None, kind: str = "cloud"):
        if client is None:
            import anthropic  # optional dependency

            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s)
        self.client = client
        self.model = model
        self.name = name or model
        self.kind = kind
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks

    async def complete_json(self, messages: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        chat = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                                  "messages": chat}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            # On a policy decline the API re-runs the request on a fallback model inside the same call.
            resp = await self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = await self.client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        usage = {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens,
                 "served_by": getattr(resp, "model", self.model)}
        return parse_json_object(text), usage
