"""
OpenRouter HTTP helpers — single OpenAI-compatible base URL for chat + embeddings.
"""

from __future__ import annotations

from typing import Any

import httpx


def openrouter_headers(api_key: str, referer: str | None, title: str | None) -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if referer:
        h["HTTP-Referer"] = referer
    if title:
        h["X-Title"] = title
    return h


def normalize_chat_usage(payload: dict[str, Any]) -> dict[str, int]:
    """
    Normalize usage from OpenRouter/OpenAI-compatible chat completion responses.
    """
    usage = payload.get("usage") or {}
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    reasoning = 0
    if isinstance(usage.get("completion_tokens_details"), dict):
        reasoning = int(usage["completion_tokens_details"].get("reasoning_tokens") or 0)
    reasoning = reasoning or int(usage.get("reasoning_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)

    def _nz(x: Any) -> int:
        try:
            return max(0, int(x))
        except (TypeError, ValueError):
            return 0

    return {
        "prompt_tokens": _nz(prompt),
        "completion_tokens": _nz(completion),
        "reasoning_tokens": _nz(reasoning),
        "cache_read_input_tokens": _nz(cache_read),
    }


async def chat_completions_json(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1000,
    temperature: float = 0.7,
    stream: bool = False,
    extra_body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], httpx.Response]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if extra_body:
        merged = dict(body)
        merged.update(extra_body)
        body = merged
    r = await client.post(f"{url.rstrip('/')}/chat/completions", headers=headers, json=body)
    r.raise_for_status()
    return r.json(), r


async def embeddings_json(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    model: str,
    input_text: str,
) -> tuple[list[float] | None, dict[str, Any]]:
    r = await client.post(
        f"{url.rstrip('/')}/embeddings",
        headers=headers,
        json={"model": model, "input": input_text},
    )
    r.raise_for_status()
    data = r.json()
    emb = (data.get("data") or [{}])[0].get("embedding")
    return emb, data
