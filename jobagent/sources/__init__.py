"""Job board adapters.

All of these hit documented, public, unauthenticated job-board endpoints — the
same ones a company's own careers page uses. Nothing here scrapes behind a login
or defeats a bot check.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from ..models import JobPosting, Profile
from .base import JobSource, guess_remote, http_json, strip_html


class GreenhouseSource(JobSource):
    """https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true"""
    name = "greenhouse"

    def __init__(self, boards: list[str]):
        self.boards = boards

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for token in self.boards:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            try:
                data = http_json(url)
            except RuntimeError:
                continue
            for j in data.get("jobs", []):
                desc = strip_html(j.get("content", ""))
                loc = (j.get("location") or {}).get("name", "")
                out.append(JobPosting(
                    source=self.name,
                    company=token.replace("-", " ").title(),
                    title=j.get("title", ""),
                    url=j.get("absolute_url", ""),
                    description=desc,
                    location=loc,
                    remote=guess_remote(f"{loc} {desc[:1500]}"),
                    posted_at=(j.get("updated_at") or "")[:10],
                    department=", ".join(d.get("name", "") for d in j.get("departments", [])),
                    external_id=str(j.get("id", "")),
                    raw={"board": token, "id": j.get("id")},
                ))
        return out


class LeverSource(JobSource):
    """https://api.lever.co/v0/postings/<company>?mode=json"""
    name = "lever"

    def __init__(self, companies: list[str]):
        self.companies = companies

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for co in self.companies:
            url = f"https://api.lever.co/v0/postings/{co}?mode=json"
            try:
                data = http_json(url)
            except RuntimeError:
                continue
            for j in data:
                cat = j.get("categories", {}) or {}
                desc = strip_html(j.get("descriptionPlain") or j.get("description", ""))
                lists = "\n\n".join(
                    f"{s.get('text','')}\n{strip_html(s.get('content',''))}"
                    for s in j.get("lists", [])
                )
                loc = cat.get("location", "")
                out.append(JobPosting(
                    source=self.name,
                    company=co.replace("-", " ").title(),
                    title=j.get("text", ""),
                    url=j.get("hostedUrl", ""),
                    description=(desc + "\n\n" + lists).strip(),
                    location=loc,
                    remote=guess_remote(f"{loc} {cat.get('commitment','')} {desc[:1500]}"),
                    posted_at=str(j.get("createdAt", ""))[:10],
                    department=cat.get("team", ""),
                    external_id=j.get("id", ""),
                    raw={"company": co},
                ))
        return out


class AshbySource(JobSource):
    """https://api.ashbyhq.com/posting-api/job-board/<org>"""
    name = "ashby"

    def __init__(self, orgs: list[str]):
        self.orgs = orgs

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for org in self.orgs:
            url = (f"https://api.ashbyhq.com/posting-api/job-board/"
                   f"{org}?includeCompensation=true")
            try:
                data = http_json(url)
            except RuntimeError:
                continue
            for j in data.get("jobs", []):
                desc = strip_html(j.get("descriptionHtml") or j.get("descriptionPlain", ""))
                out.append(JobPosting(
                    source=self.name,
                    company=j.get("organizationName") or org.title(),
                    title=j.get("title", ""),
                    url=j.get("jobUrl", ""),
                    description=desc,
                    location=j.get("location", ""),
                    remote=bool(j.get("isRemote")) if "isRemote" in j else guess_remote(desc[:1500]),
                    salary_text=json.dumps(j.get("compensation", {}))[:300]
                    if j.get("compensation") else "",
                    posted_at=(j.get("publishedAt") or "")[:10],
                    department=j.get("department", ""),
                    external_id=j.get("id", ""),
                    raw={"org": org},
                ))
        return out


class RemotiveSource(JobSource):
    """https://remotive.com/api/remote-jobs — public aggregated remote feed."""
    name = "remotive"

    def __init__(self, queries: list[str], limit_per_query: int = 50):
        self.queries = queries
        self.limit = limit_per_query

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for q in self.queries:
            url = ("https://remotive.com/api/remote-jobs?limit="
                   f"{self.limit}&search={urllib.parse.quote(q)}")
            try:
                data = http_json(url)
            except RuntimeError:
                continue
            for j in data.get("jobs", []):
                out.append(JobPosting(
                    source=self.name,
                    company=j.get("company_name", ""),
                    title=j.get("title", ""),
                    url=j.get("url", ""),
                    description=strip_html(j.get("description", "")),
                    location=j.get("candidate_required_location", "Remote"),
                    remote=True,
                    salary_text=j.get("salary", "") or "",
                    posted_at=(j.get("publication_date") or "")[:10],
                    department=j.get("category", ""),
                    external_id=str(j.get("id", "")),
                    raw={"query": q},
                ))
        return out


class ArbeitsagenturSource(JobSource):
    """Bundesagentur für Arbeit Jobsuche API.

    This is the source that actually covers Werkstudent, Praktikum and
    Abschlussarbeit roles in places like Weimar, Jena and Erfurt — those
    postings are largely absent from Greenhouse and Lever, which skew towards
    venture-funded tech companies.

    CAVEAT: I could not reach this endpoint from the sandbox where this was
    written, so the request shape is from documentation rather than a live
    test. Run `jobagent search` once and confirm you get results before you
    rely on it. If the schema has moved, the field mapping below is the only
    thing that needs changing.

    Config entries are "<what>|<where>|<radius_km>", e.g.
        "Werkstudent Informatik|Weimar|30"
    """
    name = "arbeitsagentur"
    ENDPOINT = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    API_KEY = "jobboerse-jobsuche"

    def __init__(self, queries: list[str], size: int = 50):
        self.queries = queries
        self.size = size

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for q in self.queries:
            parts = [x.strip() for x in str(q).split("|")]
            what = parts[0] if parts else ""
            where = parts[1] if len(parts) > 1 else ""
            radius = parts[2] if len(parts) > 2 else "25"

            params = {"was": what, "size": str(self.size), "page": "1"}
            if where:
                params["wo"] = where
                params["umkreis"] = radius
            url = f"{self.ENDPOINT}?{urllib.parse.urlencode(params)}"

            try:
                data = http_json(url, headers={"X-API-Key": self.API_KEY})
            except RuntimeError:
                continue

            for j in data.get("stellenangebote", []) or []:
                ort = (j.get("arbeitsort") or {})
                loc = ", ".join(x for x in [ort.get("ort"), ort.get("region")] if x)
                ref = j.get("refnr", "")
                out.append(JobPosting(
                    source=self.name,
                    company=j.get("arbeitgeber", ""),
                    title=j.get("titel") or j.get("beruf", ""),
                    url=(f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"
                         if ref else ""),
                    description=strip_html(j.get("stellenbeschreibung", "")
                                           or j.get("beruf", "")),
                    location=loc,
                    remote=guess_remote(loc),
                    posted_at=(j.get("eintrittsdatum")
                               or j.get("aktuelleVeroeffentlichungsdatum") or "")[:10],
                    department=j.get("beruf", ""),
                    external_id=ref,
                    raw={"query": q},
                ))
        return out


class LocalJSONSource(JobSource):
    """Read postings from a local JSON file. Useful for testing or for feeding
    in listings you exported yourself from a site that has no public API."""
    name = "local"

    def __init__(self, paths: list[str]):
        self.paths = paths

    def fetch(self, profile: Profile) -> list[JobPosting]:
        out: list[JobPosting] = []
        for path in self.paths:
            p = Path(path)
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            for j in data if isinstance(data, list) else data.get("jobs", []):
                out.append(JobPosting(
                    source=j.get("source", self.name),
                    company=j.get("company", ""),
                    title=j.get("title", ""),
                    url=j.get("url", ""),
                    description=j.get("description", ""),
                    location=j.get("location", ""),
                    remote=j.get("remote"),
                    salary_text=j.get("salary_text", ""),
                    posted_at=j.get("posted_at", ""),
                    department=j.get("department", ""),
                    raw=j,
                ))
        return out


REGISTRY = {
    "greenhouse": (GreenhouseSource, "boards"),
    "lever": (LeverSource, "companies"),
    "ashby": (AshbySource, "orgs"),
    "remotive": (RemotiveSource, "queries"),
    "arbeitsagentur": (ArbeitsagenturSource, "queries"),
    "local": (LocalJSONSource, "paths"),
}


def build_sources(cfg: dict) -> list[JobSource]:
    """cfg looks like {"greenhouse": ["stripe", "figma"], "remotive": ["python"]}"""
    sources: list[JobSource] = []
    for key, values in (cfg or {}).items():
        entry = REGISTRY.get(key)
        if not entry or not values:
            continue
        cls, _argname = entry
        sources.append(cls(list(values)))
    return sources
