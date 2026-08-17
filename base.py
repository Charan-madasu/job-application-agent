"""Job source plumbing: HTTP helpers, HTML stripping, base adapter."""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..models import JobPosting, Profile

UA = "jobagent/1.0 (+personal job search assistant)"


def http_json(url: str, timeout: int = 30, retries: int = 3,
              headers: dict | None = None):
    last = None
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 - sources are best-effort
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n{3,}")


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return _WS.sub("\n\n", s).strip()


def guess_remote(text: str) -> bool | None:
    t = (text or "").lower()
    if any(k in t for k in ("remote", "work from home", "distributed", "anywhere")):
        if "no remote" in t or "not remote" in t or "onsite only" in t:
            return False
        return True
    if any(k in t for k in ("on-site", "onsite", "in office", "hybrid")):
        return False
    return None


class JobSource:
    name = "base"

    def fetch(self, profile: Profile) -> list[JobPosting]:
        raise NotImplementedError
