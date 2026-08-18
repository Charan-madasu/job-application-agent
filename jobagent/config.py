"""Loading and validating the user profile + run configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import Education, Experience, Preferences, Profile

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class ConfigError(RuntimeError):
    pass


def _read(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        if yaml is None:
            raise ConfigError("PyYAML is required for .yaml configs: pip install pyyaml")
        return yaml.safe_load(text) or {}
    return json.loads(text)


def load_profile(path: str | Path) -> Profile:
    data = _read(path)
    prof = data.get("profile", data)

    prefs = Preferences(**{
        k: v for k, v in (prof.get("preferences") or {}).items()
        if k in Preferences.__dataclass_fields__
    })

    experience = [
        Experience(**{k: v for k, v in e.items() if k in Experience.__dataclass_fields__})
        for e in prof.get("experience", []) or []
    ]
    education = [
        Education(**{k: v for k, v in e.items() if k in Education.__dataclass_fields__})
        for e in prof.get("education", []) or []
    ]

    p = Profile(
        name=prof.get("name", ""),
        email=prof.get("email", ""),
        phone=prof.get("phone", ""),
        location=prof.get("location", ""),
        links=prof.get("links", {}) or {},
        summary=prof.get("summary", ""),
        skills=prof.get("skills", []) or [],
        experience=experience,
        education=education,
        preferences=prefs,
        standard_answers=prof.get("standard_answers", {}) or {},
    )
    _validate(p)
    return p


def _validate(p: Profile) -> None:
    missing = [f for f in ("name", "email") if not getattr(p, f)]
    if missing:
        raise ConfigError(f"profile is missing required field(s): {', '.join(missing)}")
    if not p.experience and not p.summary:
        raise ConfigError("profile needs at least a summary or one experience entry")


def load_settings(path: str | Path) -> dict[str, Any]:
    """Everything that isn't the profile: sources, automation flags, paths."""
    data = _read(path)
    settings = data.get("settings", {}) or {}
    settings.setdefault("sources", {})
    settings.setdefault("output_dir", "out")
    settings.setdefault("db_path", "out/jobagent.sqlite3")
    settings.setdefault("model", os.getenv("JOBAGENT_MODEL", "claude-sonnet-4-6"))

    # Tuned for a real search — dozens of relevant postings over months, not
    # thousands per week. Raise these only if your actual funnel is bigger.
    settings.setdefault("max_rank", 25)
    settings.setdefault("min_heuristic", 0.35)
    settings.setdefault("rank_workers", 3)
    settings.setdefault("max_tailor", 5)
    settings.setdefault("min_fit_to_tailor", 65)

    # Semantic stage is off unless asked for, so the tool never overstates
    # what it is doing.
    settings.setdefault("use_semantic_ranking", False)
    settings.setdefault("embedding_model",
                        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    settings.setdefault("semantic_weight", 0.4)

    # Language of the cover letter, and a hard ceiling on its complexity.
    settings.setdefault("cover_letter_language", "en")
    settings.setdefault("language_level_cap", None)

    if "automation" in settings:
        raise ConfigError(
            "the `automation:` block no longer exists — auto-submit was removed "
            "from this tool by design. Delete that block from your config. "
            "See `apply --mode prep|assist`."
        )
    return settings
