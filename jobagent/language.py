"""Language control.

The problem this solves: a model writing German will default to roughly C1. If
you are at A2 working toward B1, a C1 Motivationsschreiben gets you invited to
an interview you cannot hold. The document and the person have to match.

Two mechanisms:
  1. Generation is constrained to a declared CEFR ceiling.
  2. A separate pass assesses the produced text's actual level and regenerates
     if it drifted above the ceiling. If it still drifts, the package is flagged
     rather than silently shipped.
"""
from __future__ import annotations

from .llm import LLM, LLMError

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

CEFR_RULES = {
    "A1": (
        "Present tense only. Sentences under 8 words. Main clauses joined with "
        "'und' or 'aber' only — no subordinate clauses at all. Only the most "
        "common everyday vocabulary. No passive, no Konjunktiv, no genitive."
    ),
    "A2": (
        "Mostly present tense; Perfekt for the past. Sentences under 12 words. "
        "Subordinate clauses limited to 'weil', 'dass', 'wenn'. Everyday "
        "vocabulary plus basic job-related terms. No passive voice. No Konjunktiv II "
        "beyond 'ich würde' and 'ich könnte'. No genitive constructions. No "
        "nominalisation chains. Simple, direct, slightly plain — that is correct, "
        "not a defect."
    ),
    "B1": (
        "Present, Perfekt and Präteritum for common verbs. Sentences under 18 "
        "words. Subordinate clauses with weil/dass/wenn/obwohl/damit are fine. "
        "Konjunktiv II for polite requests. Occasional simple passive. Avoid "
        "idiom, avoid long nominal phrases, avoid Partizipialkonstruktionen."
    ),
    "B2": (
        "Full range of common tenses, passive, and Konjunktiv II. Sentences may "
        "run to 25 words. Avoid literary register and heavy nominalisation."
    ),
    "C1": "No constraints beyond clear professional register.",
    "C2": "No constraints.",
}

# Constructions that reliably read as far above B1 in a German cover letter.
_OVERSHOOT_HINTS = (
    "Partizipialkonstruktionen, Genitivketten, Konjunktiv I, "
    "Funktionsverbgefüge (e.g. 'in Erfahrung bringen'), heavy nominalisation "
    "(e.g. 'unter Berücksichtigung der'), and idiomatic business phrases"
)


def _at_or_below(level: str, cap: str) -> bool:
    try:
        return CEFR_ORDER.index(level) <= CEFR_ORDER.index(cap)
    except ValueError:
        return True


def language_instruction(lang: str, cap: str | None) -> str:
    """Prompt fragment injected into cover-letter generation."""
    if lang.lower() in ("en", "english"):
        return "Write the letter in English."
    if not cap:
        return f"Write the letter in {lang}."

    rules = CEFR_RULES.get(cap.upper(), CEFR_RULES["B1"])
    return (
        f"Write the letter in German, constrained to CEFR level {cap.upper()}.\n"
        f"Level rules: {rules}\n"
        f"Explicitly avoid: {_OVERSHOOT_HINTS}.\n"
        "This constraint is not stylistic preference — the candidate has to speak "
        "at this level in the interview that follows. A letter written above their "
        "level is a liability, not a polish. Simpler and correct beats fluent and "
        "unrepresentative. Do not apologise for the level or mention it in the letter."
    )


ASSESS_SYSTEM = """You are a CEFR assessor. Given a piece of German writing, \
estimate the level a learner would need to have produced it unaided, and list \
the specific constructions that push it above that line.

Return JSON:
{"level": "A1"|"A2"|"B1"|"B2"|"C1"|"C2",
 "above_level_constructions": ["<exact phrase from the text>", ...],
 "note": "<one sentence>"}

Judge the hardest constructions present, not the average sentence. A single \
Partizipialkonstruktion or genitive chain in an otherwise simple text still \
puts it at B2+."""


def assess_level(llm: LLM, text: str) -> dict:
    if llm.offline:
        return {"level": "unchecked", "above_level_constructions": [],
                "note": "offline"}
    try:
        return llm.complete_json(ASSESS_SYSTEM, text[:6000], max_tokens=700,
                                 task="assess")
    except LLMError as e:
        return {"level": "unchecked", "above_level_constructions": [],
                "note": str(e)}


def enforce_level(
    llm: LLM,
    text: str,
    cap: str,
    regenerate,
    max_attempts: int = 2,
) -> tuple[str, dict]:
    """Assess, and regenerate up to `max_attempts` times if above the cap.

    `regenerate(feedback: str) -> str` produces a new draft.
    Returns (final_text, report). The report always tells the truth about what
    happened, including failure.
    """
    report: dict = {"cap": cap, "attempts": 0, "assessed": "", "ok": True,
                    "flagged": []}

    for attempt in range(max_attempts + 1):
        result = assess_level(llm, text)
        level = str(result.get("level", "unchecked"))
        report["attempts"] = attempt + 1
        report["assessed"] = level

        if level == "unchecked" or _at_or_below(level, cap):
            report["ok"] = True
            report["flagged"] = []
            return text, report

        offenders = [str(x) for x in (result.get("above_level_constructions") or [])][:8]
        report["flagged"] = offenders
        report["ok"] = False

        if attempt >= max_attempts:
            break

        feedback = (
            f"The previous draft assessed at {level}, above the {cap} ceiling.\n"
            f"These constructions were too advanced: {'; '.join(offenders) or 'general register'}\n"
            f"Rewrite it strictly at {cap}: {CEFR_RULES.get(cap.upper(), '')}\n"
            "Shorter sentences. Simpler verbs. Cut anything you would not expect "
            f"a {cap} learner to produce."
        )
        text = regenerate(feedback)

    return text, report


def language_note(report: dict, lang: str) -> str:
    """Human-readable warning attached to the application package."""
    if lang.lower() in ("en", "english") or not report:
        return ""
    if report.get("assessed") == "unchecked":
        return "Language level was not verified (offline or check failed)."
    if report.get("ok"):
        return (f"German assessed at {report['assessed']} "
                f"(ceiling {report['cap']}) after {report['attempts']} pass(es).")
    return (
        f"⚠ LANGUAGE LEVEL OVER CEILING: assessed {report['assessed']}, "
        f"ceiling {report['cap']}. Above-level phrasing: "
        f"{'; '.join(report.get('flagged', [])) or 'general register'}\n"
        "  Simplify these lines yourself, or send the letter in English instead. "
        "An interview invitation earned by a letter you could not have written is "
        "an interview you start behind."
    )
