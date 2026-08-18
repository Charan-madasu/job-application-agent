"""SQLite-backed tracking store: jobs seen, scores, application state."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import ApplicationPackage, JobPosting, Score

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, company TEXT, title TEXT, url TEXT,
    location TEXT, remote INTEGER, salary_text TEXT,
    posted_at TEXT, department TEXT, description TEXT,
    first_seen TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    job_id TEXT PRIMARY KEY,
    heuristic REAL, fit INTEGER, verdict TEXT, combined REAL,
    reasons TEXT, gaps TEXT, red_flags TEXT, keywords TEXT, scored_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY,
    company TEXT, title TEXT, url TEXT,
    resume_md TEXT, cover_letter_md TEXT,
    screening_answers TEXT, autofill TEXT,
    status TEXT, notes TEXT,
    created_at TEXT, submitted_at TEXT, artifacts TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, job_id TEXT, kind TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_combined ON scores(combined DESC);
CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ jobs

    def upsert_job(self, job: JobPosting) -> bool:
        """Returns True if this job is newly seen."""
        cur = self.conn.execute("SELECT 1 FROM jobs WHERE id=?", (job.id,))
        is_new = cur.fetchone() is None
        self.conn.execute(
            """INSERT INTO jobs (id, source, company, title, url, location, remote,
                                 salary_text, posted_at, department, description,
                                 first_seen, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 description=excluded.description,
                 salary_text=excluded.salary_text,
                 location=excluded.location""",
            (job.id, job.source, job.company, job.title, job.url, job.location,
             None if job.remote is None else int(job.remote), job.salary_text,
             job.posted_at, job.department, job.description, _now(),
             json.dumps(job.raw)[:200000]),
        )
        self.conn.commit()
        return is_new

    def get_job(self, job_id: str) -> JobPosting | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def all_jobs(self) -> list[JobPosting]:
        rows = self.conn.execute("SELECT * FROM jobs").fetchall()
        return [_row_to_job(r) for r in rows]

    def unscored_jobs(self) -> list[JobPosting]:
        rows = self.conn.execute(
            "SELECT j.* FROM jobs j LEFT JOIN scores s ON s.job_id=j.id WHERE s.job_id IS NULL"
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    # ---------------------------------------------------------------- scores

    def save_score(self, score: Score) -> None:
        self.conn.execute(
            """INSERT INTO scores (job_id, heuristic, fit, verdict, combined,
                                   reasons, gaps, red_flags, keywords, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 heuristic=excluded.heuristic, fit=excluded.fit,
                 verdict=excluded.verdict, combined=excluded.combined,
                 reasons=excluded.reasons, gaps=excluded.gaps,
                 red_flags=excluded.red_flags, keywords=excluded.keywords,
                 scored_at=excluded.scored_at""",
            (score.job_id, score.heuristic, score.fit, score.verdict, score.combined,
             json.dumps(score.reasons), json.dumps(score.gaps),
             json.dumps(score.red_flags), json.dumps(score.keywords), score.scored_at),
        )
        self.conn.commit()

    def ranked(self, limit: int = 50, min_fit: int = 0) -> list[tuple[JobPosting, Score]]:
        rows = self.conn.execute(
            """SELECT j.*, s.heuristic, s.fit, s.verdict, s.combined, s.reasons,
                      s.gaps, s.red_flags, s.keywords, s.scored_at
               FROM scores s JOIN jobs j ON j.id = s.job_id
               WHERE s.fit >= ?
               ORDER BY s.combined DESC LIMIT ?""",
            (min_fit, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append((_row_to_job(r), Score(
                job_id=r["id"], heuristic=r["heuristic"], fit=r["fit"],
                verdict=r["verdict"], reasons=json.loads(r["reasons"] or "[]"),
                gaps=json.loads(r["gaps"] or "[]"),
                red_flags=json.loads(r["red_flags"] or "[]"),
                keywords=json.loads(r["keywords"] or "[]"), scored_at=r["scored_at"],
            )))
        return out

    # ---------------------------------------------------------- applications

    def save_application(self, app: ApplicationPackage) -> None:
        d = asdict(app)
        self.conn.execute(
            """INSERT INTO applications (job_id, company, title, url, resume_md,
                   cover_letter_md, screening_answers, autofill, status, notes,
                   created_at, submitted_at, artifacts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 resume_md=excluded.resume_md,
                 cover_letter_md=excluded.cover_letter_md,
                 screening_answers=excluded.screening_answers,
                 autofill=excluded.autofill, status=excluded.status,
                 notes=excluded.notes, submitted_at=excluded.submitted_at,
                 artifacts=excluded.artifacts""",
            (d["job_id"], d["company"], d["title"], d["url"], d["resume_md"],
             d["cover_letter_md"], json.dumps(d["screening_answers"]),
             json.dumps(d["autofill"]), d["status"], d["notes"], d["created_at"],
             d["submitted_at"], json.dumps(d["artifacts"])),
        )
        self.conn.commit()

    def get_application(self, job_id: str) -> ApplicationPackage | None:
        r = self.conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        return _row_to_app(r) if r else None

    def applications(self, status: str | None = None) -> list[ApplicationPackage]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM applications WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM applications ORDER BY created_at DESC").fetchall()
        return [_row_to_app(r) for r in rows]

    def set_status(self, job_id: str, status: str, note: str = "") -> None:
        submitted = _now() if status == "submitted" else ""
        self.conn.execute(
            "UPDATE applications SET status=?, notes=COALESCE(NULLIF(?,''), notes),"
            " submitted_at=CASE WHEN ?<>'' THEN ? ELSE submitted_at END WHERE job_id=?",
            (status, note, submitted, submitted, job_id),
        )
        self.conn.commit()
        self.log(job_id, "status", status)

    def already_applied(self, company: str, title: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM applications WHERE lower(company)=lower(?) "
            "AND lower(title)=lower(?) AND status IN ('submitted','approved')",
            (company, title),
        ).fetchone()
        return r is not None

    # ---------------------------------------------------------------- events

    def log(self, job_id: str, kind: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (ts, job_id, kind, detail) VALUES (?,?,?,?)",
            (_now(), job_id, kind, detail[:2000]),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        q = lambda s: self.conn.execute(s).fetchone()[0]  # noqa: E731
        return {
            "jobs": q("SELECT COUNT(*) FROM jobs"),
            "scored": q("SELECT COUNT(*) FROM scores"),
            "drafted": q("SELECT COUNT(*) FROM applications WHERE status='drafted'"),
            "approved": q("SELECT COUNT(*) FROM applications WHERE status='approved'"),
            "submitted": q("SELECT COUNT(*) FROM applications WHERE status='submitted'"),
            "rejected": q("SELECT COUNT(*) FROM applications WHERE status='rejected'"),
        }


def _row_to_job(r: sqlite3.Row) -> JobPosting:
    return JobPosting(
        source=r["source"], company=r["company"], title=r["title"], url=r["url"],
        description=r["description"] or "", location=r["location"] or "",
        remote=None if r["remote"] is None else bool(r["remote"]),
        salary_text=r["salary_text"] or "", posted_at=r["posted_at"] or "",
        department=r["department"] or "", raw={},
    )


def _row_to_app(r: sqlite3.Row) -> ApplicationPackage:
    return ApplicationPackage(
        job_id=r["job_id"], company=r["company"], title=r["title"], url=r["url"],
        resume_md=r["resume_md"] or "", cover_letter_md=r["cover_letter_md"] or "",
        screening_answers=json.loads(r["screening_answers"] or "{}"),
        autofill=json.loads(r["autofill"] or "{}"), status=r["status"],
        notes=r["notes"] or "", created_at=r["created_at"],
        submitted_at=r["submitted_at"] or "",
        artifacts=json.loads(r["artifacts"] or "{}"),
    )
