"""The agent loop: discover -> dedupe -> rank -> tailor -> queue for review."""
from __future__ import annotations

from pathlib import Path

from .embed import Embedder
from .llm import LLM
from .models import ApplicationPackage, JobPosting, Profile
from .rank import rank_jobs
from .render import write_package
from .sources import build_sources
from .store import Store
from .submit import write_prep_sheet
from .tailor import build_package


class Agent:
    def __init__(self, profile: Profile, settings: dict, log=print):
        self.profile = profile
        self.settings = settings
        self.log = log
        self.store = Store(settings["db_path"])
        self.llm = LLM(
            model=settings.get("model"),
            offline=bool(settings.get("offline")),
        )
        if self.llm.offline:
            self.log("! running OFFLINE (no ANTHROPIC_API_KEY): output is stubbed")

        self.embedder = None
        if settings.get("use_semantic_ranking"):
            self.embedder = Embedder(settings.get("embedding_model")
                                     or Embedder.__init__.__defaults__[0])
            if not self.embedder.available:
                self.log(f"! semantic ranking unavailable: {self.embedder.error}")
                self.log("  ranking will be lexical overlap + LLM. Describe it that "
                         "way if you describe it to anyone.")

    # ------------------------------------------------------------- discovery

    def discover(self) -> list[JobPosting]:
        sources = build_sources(self.settings.get("sources", {}))
        if not sources:
            self.log("no sources configured — see config/profile.example.yaml")
            return []

        found, new = [], 0
        for src in sources:
            self.log(f"fetching {src.name} ...")
            try:
                jobs = src.fetch(self.profile)
            except Exception as e:  # noqa: BLE001
                self.log(f"  {src.name} failed: {e}")
                continue
            self.log(f"  {len(jobs)} postings")
            for j in jobs:
                if not j.title or not j.url:
                    continue
                if self.store.upsert_job(j):
                    new += 1
                found.append(j)
        self.log(f"discovered {len(found)} postings ({new} new since last run)")
        return found

    # --------------------------------------------------------------- ranking

    def rank(self, only_new: bool = True) -> int:
        jobs = self.store.unscored_jobs() if only_new else self.store.all_jobs()
        if not jobs:
            self.log("nothing to rank")
            return 0
        scores = rank_jobs(
            self.llm, jobs, self.profile,
            max_llm_calls=int(self.settings.get("max_rank", 25)),
            min_heuristic=float(self.settings.get("min_heuristic", 0.35)),
            workers=int(self.settings.get("rank_workers", 3)),
            embedder=self.embedder,
            semantic_weight=float(self.settings.get("semantic_weight", 0.4)),
            progress=self.log,
        )
        for s in scores:
            self.store.save_score(s)
        self.log(f"scored {len(scores)} postings")
        return len(scores)

    # --------------------------------------------------------------- tailoring

    def tailor(self, limit: int | None = None, min_fit: int | None = None) -> list[ApplicationPackage]:
        limit = limit or int(self.settings.get("max_tailor", 10))
        min_fit = min_fit if min_fit is not None else int(
            self.settings.get("min_fit_to_tailor", 65))

        out_dir = Path(self.settings["output_dir"]) / "applications"
        made: list[ApplicationPackage] = []

        for job, score in self.store.ranked(limit=limit * 3, min_fit=min_fit):
            if len(made) >= limit:
                break
            if self.store.get_application(job.id):
                continue
            if self.store.already_applied(job.company, job.title):
                self.log(f"  skip (already applied): {job.company} — {job.title}")
                continue

            self.log(f"tailoring [{score.fit}] {job.company} — {job.title}")
            app = build_package(
                self.llm, job, self.profile, score,
                language=self.settings.get("cover_letter_language", "en"),
                level_cap=self.settings.get("language_level_cap"),
            )
            app.artifacts = write_package(app, out_dir)
            folder = next(iter(app.artifacts.values()), None)
            if folder:
                app.artifacts["apply_sheet"] = write_prep_sheet(
                    app, Path(folder).parent)
            self.store.save_application(app)
            self.store.log(job.id, "tailored", f"fit={score.fit}")
            if app.notes:
                for line in app.notes.splitlines()[:4]:
                    self.log(f"  ⚠ {line}")
            made.append(app)

        self.log(f"drafted {len(made)} application packages")
        return made

    # ------------------------------------------------------------------- run

    def run(self, tailor_limit: int | None = None) -> dict:
        self.discover()
        self.rank()
        self.tailor(limit=tailor_limit)
        stats = self.store.stats()
        self.log(
            f"\nsummary: {stats['jobs']} jobs tracked | {stats['scored']} scored | "
            f"{stats['drafted']} awaiting review | {stats['submitted']} submitted"
        )
        if stats["drafted"]:
            self.log("next: `review` — and read every line before you approve.")
        if not self.llm.offline:
            self.log(f"llm: {self.llm.calls} calls, "
                     f"{self.llm.input_tokens:,} in / {self.llm.output_tokens:,} out tokens")
        return stats

    def close(self) -> None:
        self.store.close()
