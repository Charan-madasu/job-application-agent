"""Run with: python -m pytest tests/ -q   (or plain: python tests/test_core.py)"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobagent.llm import _extract_json, LLM  # noqa: E402
from jobagent.models import JobPosting, Preferences, Profile, Score  # noqa: E402
from jobagent.rank import heuristic_score, _max_salary  # noqa: E402
from jobagent.render import markdown_to_docx, markdown_to_html, safe_slug  # noqa: E402
from jobagent.store import Store  # noqa: E402
from jobagent import drift  # noqa: E402
from jobagent import language  # noqa: E402
from jobagent import submit  # noqa: E402
from jobagent.models import Experience  # noqa: E402


def _profile(**pref):
    p = Profile(name="A B", email="a@b.com", skills=["Python", "Kubernetes", "Go"],
                summary="backend engineer")
    p.preferences = Preferences(titles=["Backend Engineer"], locations=["Berlin"],
                                seniority=["senior"], **pref)
    return p


def _job(**kw):
    base = dict(source="local", company="Acme", title="Senior Backend Engineer",
                url="https://x/1", description="Python Kubernetes Go distributed systems",
                location="Berlin, Germany", remote=False)
    base.update(kw)
    return JobPosting(**base)


def test_heuristic_ranks_relevant_above_irrelevant():
    p = _profile()
    good = heuristic_score(_job(), p)
    bad = heuristic_score(_job(title="Warehouse Supervisor", location="Leipzig",
                               description="forklift certification night shifts"), p)
    assert good > bad, (good, bad)
    assert 0.0 <= bad <= good <= 1.0


def test_excluded_keyword_hard_fails():
    p = _profile(exclude_keywords=["unpaid"])
    assert heuristic_score(_job(description="unpaid trial period"), p) == 0.0


def test_excluded_company_hard_fails():
    p = _profile(exclude_companies=["Acme"])
    assert heuristic_score(_job(), p) == 0.0


def test_must_have_keyword_enforced():
    p = _profile(must_have_keywords=["rust"])
    assert heuristic_score(_job(), p) == 0.0
    assert heuristic_score(_job(description="rust and python"), p) > 0.0


def test_remote_preference_respected():
    p = _profile(remote_ok=False)
    assert heuristic_score(_job(remote=True), p) == 0.0


def test_salary_parsing():
    assert _max_salary("€95,000 - €120,000") == 120000
    assert _max_salary("120k - 150k USD") == 150000
    assert _max_salary("competitive") is None


def test_json_extraction_survives_fences_and_prose():
    assert _extract_json('```json\n{"fit": 80}\n```')["fit"] == 80
    assert _extract_json('Here you go: {"fit": 42, "s": "}"} done')["fit"] == 42
    assert _extract_json("[1,2,3]") == [1, 2, 3]


def test_offline_llm_produces_parseable_rank():
    llm = LLM(api_key="", offline=True)
    data = llm.complete_json("sys", "prompt", task="rank")
    assert 0 <= data["fit"] <= 100 and "verdict" in data


def test_store_dedupes_and_tracks():
    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "t.sqlite3")
        j = _job()
        assert s.upsert_job(j) is True
        assert s.upsert_job(j) is False          # same job -> not new
        assert len(s.all_jobs()) == 1
        assert len(s.unscored_jobs()) == 1
        s.save_score(Score(job_id=j.id, heuristic=0.8, fit=90, verdict="strong"))
        assert not s.unscored_jobs()
        ranked = s.ranked()
        assert ranked[0][1].fit == 90
        assert ranked[0][1].combined == round(0.25 * 80 + 0.75 * 90, 2)
        s.close()


def test_combined_score_blend():
    assert Score(job_id="x", heuristic=1.0, fit=100).combined == 100.0
    assert Score(job_id="x", heuristic=0.0, fit=0).combined == 0.0


def test_render_docx_and_html():
    md = "# Name\n## Summary\nText with **bold**\n## Skills\n- Python\n- Go\n"
    with tempfile.TemporaryDirectory() as d:
        dx = markdown_to_docx(md, Path(d) / "r.docx")
        ht = markdown_to_html(md, Path(d) / "r.html")
        assert dx.stat().st_size > 5000
        html = ht.read_text()
        assert "<h1>Name</h1>" in html and "<li>Python</li>" in html
        assert "<strong>bold</strong>" in html


def test_safe_slug():
    assert safe_slug("Acme, Inc.", "Sr/Staff Eng") == "Acme_Inc._SrStaff_Eng"


# ---------------------------------------------------------------- drift checks

def _rich_profile():
    p = Profile(name="Sam Lee", email="s@l.com", skills=["Python", "PyTorch", "YOLOv8"],
                summary="CS student")
    p.experience = [Experience(
        title="Student Project", company="Coursework", start="2025-10", end="2026-02",
        bullets=["Built a YOLOv8 pipeline for a custom dataset",
                 "Handled annotation and evaluation"],
        tech=["Python", "PyTorch"])]
    return p


def test_drift_catches_invented_metrics():
    p = _rich_profile()
    flags = drift.check_numbers("- Built a pipeline achieving 94% mAP on 12,000 images", p)
    texts = {f.text.strip() for f in flags}
    assert any("94" in t for t in texts), texts
    assert any("12,000" in t for t in texts), texts


def test_drift_catches_escalation_verbs():
    p = _rich_profile()
    flags = drift.check_escalation("- Led and architected the pipeline", p)
    kinds = {f.text.lower() for f in flags}
    assert "led" in kinds and "architected" in kinds


def test_drift_catches_unknown_tools():
    p = _rich_profile()
    flags = drift.check_unknown_terms("- Deployed with TensorRT on edge devices", p)
    assert any(f.text == "TensorRT" for f in flags)


def test_drift_clean_resume_passes():
    p = _rich_profile()
    clean = "# Sam Lee\n## Experience\n- Built a YOLOv8 pipeline for a custom dataset\n"
    assert drift.run_all(clean, p) == []


def test_escalation_ok_when_profile_says_it():
    p = _rich_profile()
    p.experience[0].bullets.append("Led the weekly review")
    assert drift.check_escalation("- Led the pipeline work", p) == []


# ------------------------------------------------------------- language control

def test_cefr_ordering():
    assert language._at_or_below("A2", "B1")
    assert language._at_or_below("B1", "B1")
    assert not language._at_or_below("C1", "B1")


def test_language_instruction_english_has_no_cefr_rules():
    assert "CEFR" not in language.language_instruction("en", "B1")


def test_language_instruction_german_carries_cap():
    txt = language.language_instruction("de", "A2")
    assert "A2" in txt and "Sentences under 12 words" in txt


def test_enforce_level_regenerates_then_flags():
    calls = []

    class FakeLLM:
        offline = False
        def complete_json(self, system, prompt, **kw):
            return {"level": "C1", "above_level_constructions": ["unter Berücksichtigung"]}

    def regen(feedback):
        calls.append(feedback)
        return "simpler text"

    text, report = language.enforce_level(FakeLLM(), "fancy text", "B1", regen,
                                          max_attempts=2)
    assert len(calls) == 2, calls              # tried twice
    assert report["ok"] is False               # and admitted failure
    assert "B1" in language.language_note(report, "de")
    assert "OVER CEILING" in language.language_note(report, "de")


def test_enforce_level_passes_when_within_cap():
    class FakeLLM:
        offline = False
        def complete_json(self, system, prompt, **kw):
            return {"level": "A2", "above_level_constructions": []}

    def regen(fb):
        raise AssertionError("should not regenerate")

    text, report = language.enforce_level(FakeLLM(), "einfacher Text", "B1", regen)
    assert report["ok"] and text == "einfacher Text"


# ------------------------------------------------------ no auto-submit, at all

def test_no_submit_capability_exists():
    """Regression guard: nothing in this codebase may click a submit button."""
    src = Path(submit.__file__).read_text()
    assert "SUBMIT_SELECTORS" not in src
    assert "allow_auto_submit" not in src
    assert not hasattr(submit, "assist_submit")
    assert "headless=False" in src and "headless=True" not in src


def test_automation_config_block_is_rejected():
    import tempfile as tf
    from jobagent.config import ConfigError, load_settings
    with tf.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("settings:\n  automation:\n    allow_auto_submit: true\n")
        path = f.name
    try:
        load_settings(path)
    except ConfigError as e:
        assert "auto-submit" in str(e)
    else:
        raise AssertionError("stale automation config was silently accepted")


def test_prep_sheet_contains_everything_needed():
    from jobagent.models import ApplicationPackage
    app = ApplicationPackage(
        job_id="x", company="Acme", title="Werkstudent ML", url="https://x/1",
        cover_letter_md="Sehr geehrte Damen und Herren,",
        autofill={"full_name": "Sam Lee", "email": "s@l.com", "phone": "+49"},
        screening_answers={"Why us?": "NEEDS_USER_INPUT"},
        artifacts={"resume_docx": "/tmp/r.docx"})
    sheet = submit.prep_sheet(app)
    assert "Sam Lee" in sheet and "s@l.com" in sheet
    assert "/tmp/r.docx" in sheet
    assert "⚠" in sheet                        # unanswered question is marked
    assert "Sehr geehrte" in sheet


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
