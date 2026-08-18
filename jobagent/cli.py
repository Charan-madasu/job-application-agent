"""Command line interface.

    jobagent run       --config profile.yaml      # full pipeline
    jobagent search    --config profile.yaml      # discover only
    jobagent rank      --config profile.yaml
    jobagent tailor    --config profile.yaml -n 5
    jobagent list      --config profile.yaml
    jobagent show      --config profile.yaml <job_id>
    jobagent review    --config profile.yaml      # interactive approve/reject
    jobagent apply     --config profile.yaml [--mode prep|assist] [--job <id>]
    jobagent status    --config profile.yaml
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from .agent import Agent
from .config import ConfigError, load_profile, load_settings
from .store import Store
from .submit import assist_fill, prep_sheet


def _agent(args) -> Agent:
    profile = load_profile(args.config)
    settings = load_settings(args.config)
    if getattr(args, "offline", False):
        settings["offline"] = True
    if getattr(args, "out", None):
        settings["output_dir"] = args.out
        settings["db_path"] = str(Path(args.out) / "jobagent.sqlite3")
    return Agent(profile, settings)


def _short(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ commands

def cmd_run(args):
    a = _agent(args)
    try:
        a.run(tailor_limit=args.number)
    finally:
        a.close()


def cmd_search(args):
    a = _agent(args)
    try:
        a.discover()
    finally:
        a.close()


def cmd_rank(args):
    a = _agent(args)
    try:
        a.rank(only_new=not args.all)
        cmd_list(args)
    finally:
        a.close()


def cmd_tailor(args):
    a = _agent(args)
    try:
        a.tailor(limit=args.number, min_fit=args.min_fit)
    finally:
        a.close()


def cmd_list(args):
    settings = load_settings(args.config)
    store = Store(settings["db_path"])
    rows = store.ranked(limit=args.number or 25, min_fit=args.min_fit or 0)
    if not rows:
        print("nothing scored yet — run `jobagent run` first")
        return
    print(f"\n{'FIT':>4} {'ID':<10} {'COMPANY':<22} {'TITLE':<38} LOCATION")
    print("-" * 108)
    for job, score in rows:
        flag = " ⚑" if score.red_flags else ""
        print(f"{score.fit:>4} {job.id[:10]:<10} {_short(job.company,22):<22} "
              f"{_short(job.title,38):<38} {_short(job.location,20)}{flag}")
    store.close()


def cmd_show(args):
    settings = load_settings(args.config)
    store = Store(settings["db_path"])
    matches = [(j, s) for j, s in store.ranked(limit=9999)
               if j.id.startswith(args.job_id)]
    if not matches:
        print(f"no scored job matching '{args.job_id}'")
        return
    job, score = matches[0]
    app = store.get_application(job.id)
    print(f"\n{job.company} — {job.title}")
    print(f"{job.location}   {job.url}")
    print(f"\nfit {score.fit}/100 ({score.verdict}), heuristic {score.heuristic}")
    for label, items in (("why", score.reasons), ("gaps", score.gaps),
                         ("red flags", score.red_flags)):
        if items:
            print(f"\n{label}:")
            for i in items:
                print(textwrap.fill(f"  - {i}", 96, subsequent_indent="    "))
    if score.keywords:
        print("\nkeywords: " + ", ".join(score.keywords))
    if app:
        print(f"\napplication status: {app.status}")
        for kind, path in app.artifacts.items():
            print(f"  {kind}: {path}")
        if app.notes:
            print(f"\n{app.notes}")
    store.close()


def cmd_review(args):
    """Approval requires reading. That is the point of this command."""
    settings = load_settings(args.config)
    store = Store(settings["db_path"])
    pending = store.applications(status="drafted")
    if not pending:
        print("no drafted applications to review")
        return

    print(f"{len(pending)} draft(s) to review.\n"
          "The drafts are checked, not trusted. You are the last check.\n")

    for app in pending:
        print("\n" + "=" * 90)
        print(f"{app.company} — {app.title}\n{app.url}")

        if app.notes:
            print("\n" + app.notes)

        print("\n" + "-" * 90)
        print("COVER LETTER")
        print("-" * 90)
        print(app.cover_letter_md)
        print("\n" + "-" * 90)
        print("RESUME")
        print("-" * 90)
        print(app.resume_md)
        print("-" * 90)

        print("\n[a]pprove  [r]eject  [s]kip  [q]uit")
        while True:
            choice = input("> ").strip().lower()
            if choice == "a":
                confirm = input(
                    "  You've read both documents and can defend every line "
                    "in an interview? [yes/no] > ").strip().lower()
                if confirm != "yes":
                    print("  not approved — left as a draft")
                    break
                store.set_status(app.job_id, "approved")
                print("  approved")
            elif choice == "r":
                note = input("  reason (optional) > ").strip()
                store.set_status(app.job_id, "rejected", note)
                print("  rejected")
            elif choice == "q":
                store.close()
                return
            elif choice != "s":
                continue
            break
    store.close()


def cmd_apply(args):
    settings = load_settings(args.config)
    store = Store(settings["db_path"])
    if args.job:
        apps = [a for a in store.applications() if a.job_id.startswith(args.job)]
    else:
        apps = store.applications(status="approved")
    if not apps:
        print("no approved applications — run `jobagent review` first")
        return

    for app in apps[: args.number or len(apps)]:
        print()
        if args.mode == "prep":
            print(prep_sheet(app))
        else:
            print(f">>> {app.company} — {app.title}")
            try:
                assist_fill(app, settings)
            except RuntimeError as e:
                print(f"  error: {e}")
                continue

        confirm = input("  did you submit it? [y/N] > ").strip().lower()
        store.set_status(app.job_id, "submitted" if confirm == "y" else "approved")
    store.close()


def cmd_status(args):
    settings = load_settings(args.config)
    store = Store(settings["db_path"])
    s = store.stats()
    print("\n  jobs tracked   ", s["jobs"])
    print("  scored         ", s["scored"])
    print("  awaiting review", s["drafted"])
    print("  approved       ", s["approved"])
    print("  submitted      ", s["submitted"])
    print("  rejected       ", s["rejected"])
    subs = store.applications(status="submitted")
    if subs:
        print("\n  recent submissions:")
        for a in subs[:10]:
            print(f"    {a.submitted_at[:10]}  {a.company} — {a.title}")
    store.close()


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jobagent",
        description="Agentic job-search assistant: find, rank, tailor, apply.",
    )
    p.add_argument("-c", "--config", default="config/profile.yaml",
                   help="path to your profile/settings YAML")
    p.add_argument("--out", help="override output directory")
    p.add_argument("--offline", action="store_true",
                   help="run without API calls (stubbed output, for testing)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full pipeline")
    r.add_argument("-n", "--number", type=int, help="max applications to draft")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("search", help="discover postings only")
    s.set_defaults(func=cmd_search)

    k = sub.add_parser("rank", help="score postings")
    k.add_argument("--all", action="store_true", help="rescore everything")
    k.add_argument("-n", "--number", type=int, default=25)
    k.add_argument("--min-fit", type=int, default=0)
    k.set_defaults(func=cmd_rank)

    t = sub.add_parser("tailor", help="draft resumes + letters for top matches")
    t.add_argument("-n", "--number", type=int)
    t.add_argument("--min-fit", type=int)
    t.set_defaults(func=cmd_tailor)

    l = sub.add_parser("list", help="show ranked postings")
    l.add_argument("-n", "--number", type=int, default=25)
    l.add_argument("--min-fit", type=int, default=0)
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="detail for one posting")
    sh.add_argument("job_id")
    sh.set_defaults(func=cmd_show)

    rv = sub.add_parser("review", help="approve or reject drafts")
    rv.set_defaults(func=cmd_review)

    ap = sub.add_parser("apply", help="prepare approved applications for sending")
    ap.add_argument("--mode", choices=["prep", "assist"], default="prep",
                    help="prep: print a paste-ready sheet (no automation). "
                         "assist: fill a visible browser form, never submits.")
    ap.add_argument("--job", help="apply to one job id prefix")
    ap.add_argument("-n", "--number", type=int)
    ap.set_defaults(func=cmd_apply)

    st = sub.add_parser("status", help="pipeline stats")
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
