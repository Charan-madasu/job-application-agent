"""Thin, dependency-free Claude API client.

Uses urllib so the package installs with almost nothing. Supports an offline
stub mode so the whole pipeline can be exercised without an API key.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"

RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    pass


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(text[start:], start):
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
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"Could not parse JSON from model output: {text[:400]}")


class LLM:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        offline: bool = False,
        max_retries: int = 5,
        timeout: int = 120,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model
        self.offline = bool(offline or not self.api_key)
        self.max_retries = max_retries
        self.timeout = timeout
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    # ---------------------------------------------------------------- public

    def complete(
        self,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        task: str = "generic",
    ) -> str:
        if self.offline:
            return _stub(task, prompt)

        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = self._post(payload)
        self.calls += 1
        usage = data.get("usage", {})
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()

    def complete_json(
        self,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        task: str = "generic",
    ):
        strict = (
            system
            + "\n\nOutput rules: respond with a single valid JSON value and nothing "
              "else. No prose, no markdown fences, no commentary."
        )
        raw = self.complete(strict, prompt, max_tokens, temperature, task=task)
        return _extract_json(raw)

    # --------------------------------------------------------------- private

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
        }
        last = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                last = LLMError(f"HTTP {e.code}: {detail}")
                if e.code not in RETRYABLE:
                    raise last
            except (urllib.error.URLError, TimeoutError) as e:
                last = LLMError(f"network error: {e}")
            sleep = min(2 ** attempt, 20) + random.random()
            time.sleep(sleep)
        raise last or LLMError("request failed")


# ---------------------------------------------------------------- offline stub

def _stub(task: str, prompt: str) -> str:
    """Deterministic placeholder output so the pipeline runs with no API key."""
    if task == "rank":
        seed = sum(ord(c) for c in prompt[:200]) % 35
        return json.dumps({
            "fit": 55 + seed,
            "verdict": "good",
            "reasons": ["[offline stub] keyword overlap with target role",
                        "[offline stub] seniority appears aligned"],
            "gaps": ["[offline stub] set ANTHROPIC_API_KEY for real analysis"],
            "red_flags": [],
            "keywords": ["python", "api", "distributed systems"],
        })
    if task == "resume":
        return "# [offline stub resume]\n\nSet ANTHROPIC_API_KEY to generate a real tailored resume."
    if task == "cover":
        return "[offline stub cover letter] Set ANTHROPIC_API_KEY to generate a real letter."
    if task == "screening":
        return json.dumps({"answers": {}})
    return "[offline stub]"
