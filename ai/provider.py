#!/usr/bin/env python3
"""Shared AI provider wrapper for ArgusReach.
Supports OpenAI primary/fallback and Anthropic fallback/primary via env config.
"""

import json
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _primary_provider(task: str) -> str:
    return (_env(f"AI_PROVIDER_{task.upper()}") or _env("AI_PROVIDER", "openai")).lower()


def _fallback_provider(task: str) -> str:
    return (_env(f"AI_FALLBACK_PROVIDER_{task.upper()}") or _env("AI_FALLBACK_PROVIDER", "")).lower()


def _model_for(provider: str, task: str) -> str:
    provider = provider.lower()
    task = task.upper()
    specific = _env(f"AI_MODEL_{task}_{provider.upper()}")
    if specific:
        return specific
    if provider == "openai":
        return _env("OPENAI_MODEL_DEFAULT", "gpt-4o-mini")
    if provider == "anthropic":
        return _env("ANTHROPIC_MODEL_DEFAULT", "claude-haiku-4-5")
    return ""


def _call_openai(prompt: str, task: str, json_mode: bool = False, max_output_tokens: int = 1200) -> str:
    from openai import OpenAI
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = OpenAI(api_key=api_key)
    model = _model_for("openai", task)
    kwargs = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }
    if json_mode:
        kwargs["text"] = {"format": {"type": "json_object"}}
    resp = client.responses.create(**kwargs)
    return (getattr(resp, "output_text", "") or "").strip()


def _call_anthropic(prompt: str, task: str, max_tokens: int = 1200) -> str:
    import anthropic
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)
    model = _model_for("anthropic", task)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def generate_text(task: str, prompt: str, max_tokens: int = 1200, json_mode: bool = False) -> str:
    primary = _primary_provider(task)
    fallback = _fallback_provider(task)
    errors = []
    for provider in [primary, fallback]:
        if not provider:
            continue
        try:
            if provider == "openai":
                return _call_openai(prompt, task, json_mode=json_mode, max_output_tokens=max_tokens)
            if provider == "anthropic":
                return _call_anthropic(prompt, task, max_tokens=max_tokens)
            raise RuntimeError(f"Unsupported provider: {provider}")
        except Exception as e:
            errors.append(f"{provider}: {e}")
    raise RuntimeError(" | ".join(errors) if errors else "No AI provider configured")


def generate_json(task: str, prompt: str, max_tokens: int = 1200) -> dict:
    raw = generate_text(task, prompt, max_tokens=max_tokens, json_mode=True)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)
