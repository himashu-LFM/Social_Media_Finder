"""Analyst decisions must short-circuit the pipeline and never resurface rejections."""
import verification_pipeline as vp
import verification_service as vs
import wikipedia_service as ws
import serper_service


def _stub(monkeypatch, counter):
    monkeypatch.setattr(serper_service, "discover_by_site",
        lambda t, p, top_n=1, **kw: counter.__setitem__("serper", counter["serper"] + 1) or
        [{"url": f"https://www.instagram.com/found_{t.lower()}", "source": "serper",
          "meta": {"serper_title": "T"}}])
    def verify(p, gt, c, is_person=True, **kw):
        counter["llm"] += 1
        return vs.VerificationResult(platform=p, best_candidate=c[0]["url"],
                                     status=vs.STATUS_MANUAL, confidence=60, reason="stub")
    monkeypatch.setattr(vs, "verify_platform", verify)
    monkeypatch.setattr(vp.verification_service, "verify_platform", verify)
    monkeypatch.setattr(vp, "_enrich_candidates", lambda c, p: None)
    monkeypatch.setattr(vp, "_drop_missing_profiles", lambda c, p: c)


META = ws.WikiMetadata(talent="T", name="T", professions=["actor"], found=True)


def test_confirmed_cell_costs_nothing(monkeypatch):
    c = {"serper": 0, "llm": 0}
    _stub(monkeypatch, c)
    r = vp._resolve_platform_serper("T", "Instagram", META,
        decisions={"verified": {"Instagram": "https://www.instagram.com/confirmed"}, "rejected": {}})
    assert r.status == vs.STATUS_VERIFIED
    assert r.best_candidate == "https://www.instagram.com/confirmed"
    assert c == {"serper": 0, "llm": 0}, "a confirmed cell must not call any paid API"


def test_rejected_candidate_never_resurfaces(monkeypatch):
    c = {"serper": 0, "llm": 0}
    _stub(monkeypatch, c)
    r = vp._resolve_platform_serper("T", "Instagram", META,
        decisions={"verified": {}, "rejected": {"Instagram": "https://www.instagram.com/found_t"}})
    assert r.status == vs.STATUS_NOT_FOUND
    assert c["llm"] == 0, "nothing left to verify, so no LLM spend"


def test_rejection_match_survives_url_variation(monkeypatch):
    """A trailing slash must not smuggle a rejected URL back in."""
    c = {"serper": 0, "llm": 0}
    _stub(monkeypatch, c)
    r = vp._resolve_platform_serper("T", "Instagram", META,
        decisions={"verified": {}, "rejected": {"Instagram": "https://www.instagram.com/found_t/"}})
    assert r.status == vs.STATUS_NOT_FOUND


def test_no_decisions_means_normal_full_price_run(monkeypatch):
    c = {"serper": 0, "llm": 0}
    _stub(monkeypatch, c)
    r = vp._resolve_platform_serper("T", "Instagram", META, decisions={})
    assert c["serper"] == 1 and c["llm"] == 1
    assert r.status == vs.STATUS_MANUAL


def test_decision_store_failure_does_not_break_a_run(monkeypatch):
    import db_service
    monkeypatch.setattr(db_service, "is_configured", lambda: True)
    monkeypatch.setattr(db_service, "fetch_decisions",
                        lambda t: (_ for _ in ()).throw(RuntimeError("db down")))
    assert vp.load_decisions(["anyone"]) == {}


def test_cancellation_marks_cells_not_checked_not_not_found():
    """'Not Checked' must stay distinct from 'Not Found' — one asserts an absence."""
    import excel_service as ex
    df = ex.build_talent_df(["A", "B"])
    out = vp.run_pipeline_on_dataframe(df, should_cancel=lambda: True)
    statuses = {str(out.iloc[i][ex.status_col(p)]) for i in range(len(out)) for p in vp.PLATFORMS}
    assert statuses == {vs.STATUS_STOPPED}
