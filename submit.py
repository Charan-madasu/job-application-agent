"""Application preparation.

There is no auto-submit in this module. There is no code path that clicks a
submit button. This is deliberate and it is not configurable — a flag you can
flip is a flag you flip at 1am after a rejection, and an application cannot be
recalled.

Two modes:

  1. "prep"   (default) — writes your files and a field-by-field sheet you paste
                from. Zero automation, zero detection surface, works on every
                ATS including the ones that break browser drivers.

  2. "assist" (opt-in)  — opens a visible browser, fills what it can, and stops.
                You review, fix, and submit. Read the caveat below first.

On "assist": driving a form with Playwright can trip bot detection on some
platforms (Workday and SmartRecruiters are the usual suspects) even though a
human presses submit. The probability is low and the consequence is usually just
a failed application, but it is not zero, and what you are buying is about ten
minutes of typing. For a search measured in dozens of applications rather than
thousands, "prep" is the better trade. Use "assist" only where you have seen it
work and the role is not one you care most about.
"""
from __future__ import annotations

import time
from pathlib import Path

from .models import ApplicationPackage

FIELD_SELECTORS: dict[str, list[str]] = {
    "first_name": ["input[name*='first' i]", "#first_name", "input[autocomplete='given-name']"],
    "last_name": ["input[name*='last' i]", "#last_name", "input[autocomplete='family-name']"],
    "full_name": ["input[name*='name' i]:not([name*='first' i]):not([name*='last' i])",
                  "#name", "input[autocomplete='name']"],
    "email": ["input[type='email']", "input[name*='email' i]", "#email"],
    "phone": ["input[type='tel']", "input[name*='phone' i]", "#phone"],
    "location": ["input[name*='location' i]", "input[name*='city' i]", "#location"],
    "linkedin": ["input[name*='linkedin' i]", "input[placeholder*='linkedin' i]"],
    "github": ["input[name*='github' i]", "input[placeholder*='github' i]"],
    "portfolio": ["input[name*='website' i]", "input[name*='portfolio' i]"],
}

RESUME_UPLOAD_SELECTORS = [
    "input[type='file'][name*='resume' i]",
    "input[type='file'][name*='lebenslauf' i]",
    "input[type='file'][id*='resume' i]",
    "input[type='file']",
]

COVER_TEXTAREA_SELECTORS = [
    "textarea[name*='cover' i]",
    "textarea[name*='motivation' i]",
    "textarea[id*='cover' i]",
    "textarea",
]

FIELD_LABELS = {
    "full_name": "Name",
    "first_name": "First name",
    "last_name": "Last name",
    "email": "Email",
    "phone": "Phone",
    "location": "Location",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "portfolio": "Website",
    "work_authorization": "Work authorization",
    "notice_period": "Availability / notice",
    "desired_salary": "Desired salary",
}


# ------------------------------------------------------------------- mode 1

def prep_sheet(app: ApplicationPackage) -> str:
    """Everything needed to fill the form by hand, in one pasteable block."""
    lines = [
        "=" * 78,
        f"{app.company} — {app.title}",
        app.url,
        "=" * 78,
        "",
        "FIELDS",
    ]
    for key, label in FIELD_LABELS.items():
        val = app.autofill.get(key, "")
        if val:
            lines.append(f"  {label:<22} {val}")

    if app.screening_answers:
        lines += ["", "SCREENING ANSWERS"]
        for q, a in app.screening_answers.items():
            marker = "  ⚠ " if a == "NEEDS_USER_INPUT" else "    "
            lines.append(f"{marker}{q}")
            lines.append(f"      {a}")

    lines += ["", "FILES TO UPLOAD"]
    for kind, path in app.artifacts.items():
        lines.append(f"  {kind:<14} {path}")

    if app.notes:
        lines += ["", "REVIEW NOTES", *(f"  {ln}" for ln in app.notes.splitlines())]

    lines += ["", "-" * 78, "COVER LETTER (paste)", "-" * 78, "",
              app.cover_letter_md, ""]
    return "\n".join(lines)


def write_prep_sheet(app: ApplicationPackage, folder: str | Path) -> str:
    p = Path(folder) / "APPLY.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(prep_sheet(app), encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------- mode 2

def _fill(page, selectors: list[str], value: str, log) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() == 0 or not el.is_visible(timeout=1500):
                continue
            el.fill(value, timeout=4000)
            log(f"    filled {sel} <- {value[:40]}")
            return True
        except Exception:  # noqa: BLE001 - selector misses are expected
            continue
    return False


def assist_fill(app: ApplicationPackage, settings: dict, log=print) -> dict:
    """Open the posting in a visible browser, fill what we can, then stop.

    Never clicks submit. There is no argument or config that makes it click
    submit — the selectors for doing so are not in this file.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "assist mode needs Playwright:\n"
            "  pip install playwright && playwright install chromium\n"
            "Or use --mode prep, which needs nothing."
        ) from e

    log("  note: driven form-filling can trip bot detection on some ATS "
        "platforms.\n        If this posting matters, use --mode prep instead.")

    resume_path = app.artifacts.get("resume_docx") or app.artifacts.get("resume_md")
    result: dict = {"filled": [], "missed": [], "url": app.url}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)   # never headless, by design
        ctx = browser.new_context(viewport={"width": 1400, "height": 950})
        page = ctx.new_page()
        log(f"  opening {app.url}")
        page.goto(app.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        for label in ("Apply for this job", "Jetzt bewerben", "Apply now", "Apply"):
            try:
                btn = page.get_by_role("button", name=label, exact=False).first
                if btn.count() and btn.is_visible(timeout=1200):
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:  # noqa: BLE001
                continue

        for field, selectors in FIELD_SELECTORS.items():
            value = app.autofill.get(field, "")
            if not value:
                continue
            (result["filled"] if _fill(page, selectors, value, log)
             else result["missed"]).append(field)

        if resume_path and Path(resume_path).exists():
            for sel in RESUME_UPLOAD_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if el.count():
                        el.set_input_files(resume_path, timeout=6000)
                        log(f"    attached resume via {sel}")
                        result["filled"].append("resume_upload")
                        break
                except Exception:  # noqa: BLE001
                    continue
            else:
                result["missed"].append("resume_upload")

        if app.cover_letter_md:
            if _fill(page, COVER_TEXTAREA_SELECTORS, app.cover_letter_md, log):
                result["filled"].append("cover_letter")
            else:
                result["missed"].append("cover_letter")

        unanswered = [q for q, a in app.screening_answers.items()
                      if a == "NEEDS_USER_INPUT"]
        if unanswered:
            result["needs_input"] = unanswered
            log("\n  These still need your answer:")
            for q in unanswered:
                log(f"    - {q}")

        if result["missed"]:
            log(f"\n  Could not auto-fill: {', '.join(result['missed'])}")
        log("\n  Form is filled. Read it, fix it, and submit it yourself.")
        log("  Close the browser window when you're done.")

        try:
            while len(ctx.pages) and not page.is_closed():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    return result
