"""
Incremental row persistence (LFAI-153 follow-up): a stopped/crashed run must keep
the work done so far instead of persisting 0 rows. The pipeline checkpoints the
live frame as rows fill in — crucially once, forced, at the end of Phase A, right
before the batched Apify pass (the stretch a run died in) — and the API layer
throttles those checkpoints to the DB.
"""
import json

from app.output import excel as ex
from app.persistence import db as db_service
from app.pipeline import orchestrator as vp
from app.verification import verifier as vs


META = vp.wikipedia_service.WikiMetadata(
    talent="T", name="T", professions=["actor"], found=True,
    wikipedia_url="https://en.wikipedia.org/wiki/T")


def _stub_offline(monkeypatch):
    """Replace every network-touching phase with a deterministic offline stub, so
    the only thing under test is the checkpoint wiring."""
    real_inputs = vp._row_inputs
    # Force every row to carry a Wikipedia URL (has_wiki=True) so it reaches the
    # batched Apify pass — otherwise no-wiki rows skip it and nothing is tested.
    monkeypatch.setattr(vp, "_row_inputs", lambda df, rl: (
        (lambda t, _w, m, h, d: (t, "https://en.wikipedia.org/wiki/T", m, h, d))
        (*real_inputs(df, rl))))
    monkeypatch.setattr(vp, "_ground_truth", lambda *a, **k: META)
    monkeypatch.setattr(vp, "_row_bio_link_phase", lambda *a, **k: {})
    # Every platform Manual -> the talent is "failing" -> it enters the Apify batch.
    monkeypatch.setattr(vp, "_row_serper_phase", lambda *a, **k: {
        p: vs.VerificationResult(platform=p, best_candidate=f"https://x/{p}",
                                 status=vs.STATUS_MANUAL, confidence=50,
                                 decision="manual_review")
        for p in vp.PLATFORMS})
    monkeypatch.setattr(vp, "_row_apify_phase",
                        lambda talent, wiki_meta, phase1, amap, options=None: phase1)
    monkeypatch.setattr(vp, "_corroborate_row",
                        lambda talent, wiki_meta, final, options=None: final)


def test_phase_a_forces_a_checkpoint_before_the_apify_batch(monkeypatch):
    _stub_offline(monkeypatch)
    events = []
    monkeypatch.setattr(vp.apify_service, "find_social_links_batch",
                        lambda talents: events.append(("apify", None)) or {})

    df = ex.build_talent_df(["Alpha", "Beta"])
    vp.run_pipeline_on_dataframe(
        df, checkpoint=lambda frame, force=False: events.append(("cp", force)))

    assert ("apify", None) in events, "the batched Apify pass should have run"
    apify_at = events.index(("apify", None))
    forced_before = [i for i, e in enumerate(events)
                     if e == ("cp", True) and i < apify_at]
    assert forced_before, "a FORCED checkpoint must precede the Apify batch"


def test_checkpoints_fire_through_both_phases(monkeypatch):
    _stub_offline(monkeypatch)
    monkeypatch.setattr(vp.apify_service, "find_social_links_batch", lambda talents: {})

    calls = {"n": 0}
    df = ex.build_talent_df(["Alpha", "Beta"])
    vp.run_pipeline_on_dataframe(
        df, checkpoint=lambda frame, force=False: calls.__setitem__("n", calls["n"] + 1))

    # 2 rows: Phase A writes 2 + 1 forced + Phase B writes 2 = 5 checkpoint calls.
    assert calls["n"] >= 2 + 1 + 2


# ── API-layer throttle + DB write shape ──────────────────────────────────────

def test_persist_partial_throttles_row_bound_writes(monkeypatch):
    from app import main

    writes = {"n": 0}
    monkeypatch.setattr(main.db_service, "save_job_rows",
                        lambda jid, rows: writes.__setitem__("n", writes["n"] + 1))
    monkeypatch.setattr(main, "_frame_to_rows", lambda df: [{"r": 1}])

    jid = "throttle-test-job"
    # Seed the time trigger far in the future so ONLY the row-count path can fire.
    main._checkpoint_state[jid] = {"t": main.time.time() + 1e9, "c": 0.0}
    try:
        for _ in range(60):
            main._persist_partial(jid, object(), force=False)
        # 60 calls, flush every 25 -> writes at the 25th and 50th call.
        assert writes["n"] == 2
        # A forced call always writes, regardless of the throttle.
        main._persist_partial(jid, object(), force=True)
        assert writes["n"] == 3
    finally:
        main._checkpoint_state.pop(jid, None)


def test_save_job_rows_issues_a_rows_only_update(monkeypatch):
    sink = []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): sink.append((sql, params))

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self, **k): return _Cur()

    monkeypatch.setattr(db_service, "is_configured", lambda: True)
    monkeypatch.setattr(db_service, "_connection", lambda: _Conn())

    rows = [{"Talent": "Alpha"}]
    db_service.save_job_rows("job-123", rows)

    assert len(sink) == 1
    sql, params = sink[0]
    assert "UPDATE job SET rows" in sql
    assert params == (json.dumps(rows), "job-123")


def test_save_job_rows_is_a_noop_without_a_database(monkeypatch):
    monkeypatch.setattr(db_service, "is_configured", lambda: False)
    # Must not raise or touch any connection when unconfigured.
    db_service.save_job_rows("job-123", [{"r": 1}])


def test_results_endpoint_gates_on_job_status(monkeypatch):
    """Now that checkpointing writes job.rows mid-run, /api/results/latest must not
    serve those partial rows as final. A queued/running job reports ``pending`` with no
    rows; a terminal job serves its rows, flagged ``partial`` only for failed/cancelled."""
    from app import main
    # DB always has (partial) rows — the gate must decide whether to serve them.
    monkeypatch.setattr(main.db_service, "load_job_rows", lambda job_id: [{"Talent": "A"}])

    def _result(status: str) -> dict:
        main._jobs[f"job-{status}"] = {"status": status}
        try:
            return main.api_results_latest(job_id=f"job-{status}", user=None)
        finally:
            main._jobs.pop(f"job-{status}", None)

    running = _result("running")
    assert running.get("pending") is True and running["rows"] == []   # not served as final

    done = _result("completed")
    assert done.get("pending") is not True
    assert done["rows"] == [{"Talent": "A"}] and done.get("partial") is False

    failed = _result("failed")
    assert failed["rows"] == [{"Talent": "A"}] and failed.get("partial") is True


def test_unreached_rows_checkpoint_as_not_checked(monkeypatch):
    """A mid-run checkpoint must show "Not Checked" for rows the pipeline hasn't reached
    yet — not a blank cell — so a crash-rebuilt export never has empty status cells. The
    seed is overwritten the moment a row is actually verified."""
    _stub_offline(monkeypatch)
    monkeypatch.setattr(vp.apify_service, "find_social_links_batch", lambda talents: {})

    df = ex.build_talent_df(["Alpha", "Beta", "Gamma"])
    snapshots = []
    vp.run_pipeline_on_dataframe(
        df, checkpoint=lambda frame, force=False: snapshots.append(frame.copy()))

    # The first checkpoint fires after row 0's provisional write, before the later rows
    # are reached. Row 0 carries a real (verified) status; the un-reached last row must
    # read STATUS_STOPPED ("Not Checked") with a reason, not a blank cell.
    first = snapshots[0]
    assert str(first.iloc[0][ex.status_col("Instagram")]) != vs.STATUS_STOPPED   # processed
    assert str(first.iloc[-1][ex.status_col("Instagram")]) == vs.STATUS_STOPPED  # unreached
    assert str(first.iloc[-1][ex.reason_col("Instagram")]) == vp.NOT_REACHED_REASON
