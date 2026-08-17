# jobagent

Finds job postings, ranks them against your profile, and drafts a tailored
resume and cover letter for the good ones. You read them, you send them.

```
discover → prefilter → rank → tailor → check → YOU READ IT → apply
```

## What it does not do

**It does not submit applications.** There is no auto-submit mode, no config
flag that enables one, and no submit-button selector anywhere in the codebase.
A test enforces this. The reasoning: a flag you can flip is a flag you flip at
1am after a rejection, an application cannot be recalled, and unattended
submission is prohibited by most ATS terms of service. The ten minutes it would
save is not worth what it costs when it goes wrong.

It is also not a newly trained language model. It's an agent — orchestration,
ranking, generation, and verification built on the Claude API. That's what the
funded products in this category are too.

## Quickstart

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."

cp config/profile.example.yaml config/profile.yaml
$EDITOR config/profile.yaml          # this step is the whole product

python -m jobagent.cli -c config/profile.yaml run       # find, rank, draft
python -m jobagent.cli -c config/profile.yaml list      # see the ranking
python -m jobagent.cli -c config/profile.yaml review     # read and approve
python -m jobagent.cli -c config/profile.yaml apply      # get a paste-ready sheet
```

Try it with no key and no network first:

```bash
python -m jobagent.cli -c config/profile.yaml --offline run
```

## The honest reading of what's worth using

The **discovery and ranking half is the valuable part.** It reads a lot of
postings so you don't, and it tells you which five are worth your evening.

The **drafting half is a first draft, not a deliverable.** It gets you past the
blank page. It does not get you past a recruiter without you rewriting parts of
it.

The **submission half barely exists on purpose**, because for a search measured
in dozens of applications over months rather than thousands per week, form-
filling was never the bottleneck. Your bottleneck is finding the right 30
postings and writing 5 good applications, not typing your phone number faster.

## Three layers of checking, and why none of them are enough

**Layer 1 — deterministic drift detection** (`drift.py`). No model involved,
pure string matching against your profile. Catches:

- *Invented numbers.* Every figure on the resume must trace to your profile. "94% mAP", "12,000 images", "35% reduction" get flagged if you never wrote them.
- *Escalation verbs.* If the resume says "led", "architected", "owned", or "scaled" and your profile says "built" or "contributed to", it flags the promotion and suggests the honest verb.
- *Unknown tools.* A tool name that appears from nowhere — TensorRT, Kubernetes, whatever — gets flagged.

This layer cannot be argued with, which is its whole value.

**Layer 2 — model verification** (`tailor.py`). A separate Claude pass re-reads
the resume against your profile and flags invented employers, degrees, and
credentials. This is an LLM checking an LLM. It catches the obvious things and
will miss the subtle ones. Do not treat a clean layer 2 as a clean bill of health.

**Layer 3 — you.** `review` prints the full cover letter and full resume, then
asks whether you can defend every line in an interview. You have to type `yes`.
This is not theatre; it's the only layer that actually works. If you're going to
sit across from someone who asks how you got that mAP number, the number has to
be one you measured.

## Language control

This exists because it's the failure mode that costs you an interview rather
than just wasting one.

A model writing German defaults to roughly C1. If you're at A2 working toward
B1, a C1 Motivationsschreiben earns you an invitation to a conversation you
cannot hold, and the mismatch is obvious in the first five minutes. That is
worse than a plain, correct B1 letter — and much worse than English, which is
completely normal for engineering Werkstudent roles.

So:

```yaml
cover_letter_language: "de"
language_level_cap: "B1"      # required whenever language is not English
```

Generation is constrained to the ceiling, then **a separate pass assesses the
produced text's actual CEFR level.** If it drifted above the cap, it regenerates
with the specific offending constructions named. If it still drifts after two
attempts, the package is flagged with the exact phrases that are too advanced —
it does not quietly ship a letter above your level.

Default is English, and that's the recommended setting until your German catches
up with what you'd want the letter to say.

The line you want to keep true: *"I used AI to draft and check my German, the
substance is mine."* Everything in this tool is built so that sentence stays
accurate.

## Ranking

**Stage 1 — lexical prefilter** (free). Hard-filters exclusions, remote/onsite
fit, must-have terms. Then scores title overlap, skill overlap, location,
seniority, salary floor, freshness. Anything below the floor never costs a token.

**Stage 2 — semantic** (optional, local, free). Off by default. With
`sentence-transformers` installed and `use_semantic_ranking: true`, a
multilingual MiniLM embeds your profile and each posting and blends cosine
similarity into the prefilter. This is what catches "Werkstudent Datenanalyse"
matching a profile written in English. Hard disqualifications stay hard —
semantic similarity cannot resurrect a posting your exclusions killed.

**Stage 3 — LLM judgment.** Claude scores 0–100 with a calibrated rubric and
returns evidence, gaps, red flags, and keywords worth mirroring. Final rank is
`0.25 × prefilter + 0.75 × fit`.

> **If you put this on your CV**, describe it accurately. With
> `use_semantic_ranking: false` it is *lexical matching plus LLM ranking* — there
> are no embeddings in the pipeline, and calling it an "embeddings hybrid" would
> be exactly the kind of drift this tool is built to catch. Turn the semantic
> stage on and it becomes true. The tool prints which mode it ran in for this
> reason.

## Job sources

All hit public, unauthenticated endpoints — the same ones the companies' own
careers pages call.

| Source | Covers |
|---|---|
| `arbeitsagentur` | Bundesagentur für Arbeit — **the one that actually covers Werkstudent/Praktikum roles in Thuringia**. Format: `"what\|where\|radius_km"` |
| `greenhouse` | `boards-api.greenhouse.io` — venture-funded tech |
| `lever` | `api.lever.co` |
| `ashby` | `api.ashbyhq.com` |
| `remotive` | public remote-jobs feed |
| `local` | your own JSON export |

⚠ **The Arbeitsagentur adapter is untested.** I could not reach that endpoint
from the sandbox this was written in, so the request shape comes from
documentation rather than a live call. Run `search` once and confirm you get
results before relying on it; if the schema moved, the field mapping in
`sources/__init__.py` is the only thing to change.

No LinkedIn or Indeed adapter — both prohibit scraping and both actively block
it.

## Commands

| Command | What it does |
|---|---|
| `run` | discover → rank → tailor |
| `search` | fetch postings only, no LLM calls |
| `rank` | score unscored postings (`--all` to redo) |
| `tailor` | draft packages for top matches |
| `list` | ranked table |
| `show <id>` | full reasoning, gaps, red flags, files |
| `review` | prints both documents in full, then asks you to confirm you read them |
| `apply` | `--mode prep` (default, paste-ready sheet) or `--mode assist` |

## `apply --mode assist`

Opens a **visible** browser (never headless — that's not configurable), fills
what it can, and stops. It never clicks submit.

Even so: driving a form with Playwright can trip bot detection on some platforms,
Workday and SmartRecruiters especially, even though a human presses the button.
Low probability, non-zero, and what you're buying is ten minutes. `prep` mode is
the better default and it works on every ATS including the ones that break
browser drivers.

## Cost

With the shipped defaults (`max_rank: 25`, `max_tailor: 5`): roughly 25 short
ranking calls plus ~20 generation calls per run. Well under a dollar. The
prefilter stage is free and does most of the work.

## Layout

```
jobagent/
  models.py     Profile, JobPosting, Score, ApplicationPackage
  config.py     YAML loading, validation, rejects stale automation blocks
  llm.py        Claude client (stdlib only), retries, JSON extraction, offline stub
  embed.py      optional local sentence-transformers stage
  rank.py       lexical prefilter → optional semantic → LLM scoring
  tailor.py     resume, cover letter, model-based fabrication check
  drift.py      deterministic drift detection (no model)
  language.py   CEFR ceiling enforcement and level assessment
  render.py     Markdown → DOCX / HTML
  submit.py     prep sheet + opt-in browser fill. No submit path.
  store.py      SQLite: jobs, scores, applications, dedupe, already-applied
  agent.py      orchestration
  cli.py        CLI
  sources/      arbeitsagentur, greenhouse, lever, ashby, remotive, local
```

## Tests

```bash
python tests/test_core.py     # 25 tests, no network, no API key
```

Includes a regression test asserting no submit capability exists, so it can't
come back by accident.
