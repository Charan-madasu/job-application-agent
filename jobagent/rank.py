"""Two-stage ranking.

Stage 1 is a free, deterministic heuristic that throws out obvious mismatches so
we never pay for an LLM call on a warehouse job when you're a backend engineer.
Stage 2 sends the survivors to Claude for real judgment.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .embed import Embedder
from .llm import LLM, LLMError
from .models import JobPosting, Profile, Score

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "staff", "principal", "lead",
                   "manager", "director", "vp", "head"]

_SALARY = re.compile(r"(\d[\d,\.]*\d|\d)\s*(k)?\b", re.I)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\+\#\.]{2,}", (text or "").lower()))


def heuristic_score(job: JobPosting, profile: Profile) -> float:
    """0.0–1.0. Returns 0.0 for hard disqualifications."""
    p = profile.preferences
    hay = f"{job.title} {job.company} {job.location} {job.description}".lower()

    # Hard filters -----------------------------------------------------------
    if any(c.lower() in job.company.lower() for c in p.exclude_companies if c):
        return 0.0
    if any(k.lower() in hay for k in p.exclude_keywords if k):
        return 0.0
    if p.must_have_keywords and not all(k.lower() in hay for k in p.must_have_keywords):
        return 0.0
    if job.remote is True and not p.remote_ok:
        return 0.0
    if job.remote is False and not p.onsite_ok and not _location_match(job, p):
        return 0.0

    score, weight = 0.0, 0.0

    # Title alignment (heaviest single signal) --------------------------------
    if p.titles:
        title_l = job.title.lower()
        best = 0.0
        for t in p.titles:
            t_tokens = _tokens(t)
            if not t_tokens:
                continue
            overlap = len(t_tokens & _tokens(title_l)) / len(t_tokens)
            best = max(best, overlap)
        score += best * 3.0
        weight += 3.0

    # Skill overlap against the description -----------------------------------
    skills = profile.skills_lower()
    if skills:
        desc_tokens = _tokens(job.description + " " + job.title)
        hits = sum(1 for s in skills if s in desc_tokens or s in hay)
        score += min(hits / max(len(skills) * 0.4, 1), 1.0) * 2.5
        weight += 2.5

    # Location / remote --------------------------------------------------------
    loc_ok = _location_match(job, p) or (job.remote and p.remote_ok)
    score += (1.0 if loc_ok else 0.25) * 1.5
    weight += 1.5

    # Seniority ---------------------------------------------------------------
    if p.seniority:
        title_l = job.title.lower()
        want = {s.lower() for s in p.seniority}
        found = {s for s in SENIORITY_ORDER if s in title_l}
        if not found:
            score += 0.6
        elif found & want:
            score += 1.0
        else:
            score += 0.1
        weight += 1.0

    # Salary floor -------------------------------------------------------------
    if p.min_salary and job.salary_text:
        top = _max_salary(job.salary_text)
        if top:
            score += (1.0 if top >= p.min_salary else 0.0)
            weight += 1.0

    # Freshness ----------------------------------------------------------------
    if job.posted_at:
        score += 0.5
        weight += 0.5

    return round(score / weight, 4) if weight else 0.5


def _location_match(job: JobPosting, prefs) -> bool:
    if not prefs.locations:
        return True
    loc = (job.location or "").lower()
    return any(l.lower() in loc for l in prefs.locations if l)


def _max_salary(text: str) -> int | None:
    best = None
    for num, k in _SALARY.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if k:
            v *= 1000
        if v < 1000:                      # hourly rate or headcount, not a salary
            continue
        if not k and 1900 <= v <= 2100:   # a year that leaked into the text
            continue
        best = max(best or 0, int(v))
    return best


RANK_SYSTEM = """You are a ruthless, experienced technical recruiter screening \
job postings on behalf of one specific candidate. You are not a cheerleader: \
your value comes from being accurate about fit, including when the fit is poor.

Judge on: role/skill overlap, seniority match, domain relevance, location and \
work-authorization feasibility, compensation signal, and any warning signs in \
the posting (unpaid work, vague responsibilities, obvious churn roles, \
"rockstar/ninja" language, unrealistic requirement stacking, pay far below market).

Return JSON with exactly these keys:
{
  "fit": <integer 0-100>,
  "verdict": "strong" | "good" | "stretch" | "weak",
  "reasons": [<up to 4 short strings: concrete evidence for the score>],
  "gaps": [<up to 4 short strings: what the candidate lacks vs the posting>],
  "red_flags": [<0-3 short strings, empty list if none>],
  "keywords": [<up to 12 exact terms from the posting worth mirroring in a resume>]
}

Calibration: 85+ means the candidate would likely clear the recruiter screen \
today. 70-84 means a solid application. 50-69 is a stretch worth a shot only if \
volume allows. Below 50 means don't bother. Do not inflate."""


def llm_rank_one(llm: LLM, job: JobPosting, profile: Profile, heuristic: float) -> Score:
    prompt = (
        "=== CANDIDATE ===\n"
        f"{profile.to_prompt_block()}\n\n"
        "=== JOB POSTING ===\n"
        f"{job.to_prompt_block()}\n"
    )
    try:
        data = llm.complete_json(RANK_SYSTEM, prompt, max_tokens=900, task="rank")
    except LLMError as e:
        return Score(job_id=job.id, heuristic=heuristic, fit=0, verdict="weak",
                     red_flags=[f"scoring failed: {e}"])

    fit = int(max(0, min(100, data.get("fit", 0))))
    return Score(
        job_id=job.id,
        heuristic=heuristic,
        fit=fit,
        verdict=str(data.get("verdict", "")) or _verdict_from(fit),
        reasons=[str(x) for x in (data.get("reasons") or [])][:4],
        gaps=[str(x) for x in (data.get("gaps") or [])][:4],
        red_flags=[str(x) for x in (data.get("red_flags") or [])][:3],
        keywords=[str(x) for x in (data.get("keywords") or [])][:12],
    )


def _verdict_from(fit: int) -> str:
    return "strong" if fit >= 85 else "good" if fit >= 70 else "stretch" if fit >= 50 else "weak"


def rank_jobs(
    llm: LLM,
    jobs: list[JobPosting],
    profile: Profile,
    max_llm_calls: int = 25,
    min_heuristic: float = 0.35,
    workers: int = 3,
    embedder: Embedder | None = None,
    semantic_weight: float = 0.4,
    progress=None,
) -> list[Score]:
    """Prefilter (lexical, optionally + semantic), then score survivors."""
    if not jobs:
        return []

    lexical = [(j, heuristic_score(j, profile)) for j in jobs]

    use_semantic = embedder is not None and embedder.available
    if use_semantic:
        query = profile.to_prompt_block()
        docs = [f"{j.title}\n{j.company}\n{j.location}\n{j.description[:2000]}"
                for j, _ in lexical]
        sims = embedder.similarities(query, docs)
        scored = []
        for (j, lex), sem in zip(lexical, sims):
            # Hard lexical disqualifications (exclusions, must-haves) stay hard —
            # semantic similarity must not resurrect a filtered posting.
            blended = 0.0 if lex == 0.0 else round(
                (1 - semantic_weight) * lex + semantic_weight * sem, 4)
            scored.append((j, blended))
        if progress:
            progress(f"semantic stage: {embedder.model_name}")
    else:
        scored = lexical
        if progress and embedder is not None:
            progress(f"semantic stage unavailable ({embedder.error}) — "
                     "ranking is lexical overlap + LLM only")

    prefiltered = [(j, h) for j, h in scored if h >= min_heuristic]
    prefiltered.sort(key=lambda t: t[1], reverse=True)
    shortlist = prefiltered[:max_llm_calls]

    if progress:
        progress(f"prefilter kept {len(prefiltered)}/{len(jobs)}; "
                 f"sending {len(shortlist)} to the model")

    scores: list[Score] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(llm_rank_one, llm, j, profile, h): j
                   for j, h in shortlist}
        for fut in as_completed(futures):
            s = fut.result()
            scores.append(s)
            if progress:
                job = futures[fut]
                progress(f"  scored {s.fit:>3}  {job.company} — {job.title}")
    scores.sort(key=lambda s: s.combined, reverse=True)
    return scores
