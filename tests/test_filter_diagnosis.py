"""Tests for the round-based media-bed diagnosis.

Covers resistance fitting, wash/replace/abstain verdicts, version binding
across a mid-round recalibration, verdict immutability, audit consistency and
the console routes.
"""

import pytest

from desalplant.audit.recorder import Recorder
from desalplant.clock import LogicalClock
from desalplant.errors import RoundNotOpenError, UnknownRoundError
from desalplant.filter import (
    Baseline,
    BedSample,
    Decision,
    FilterDiagnoser,
    fit_resistance,
)
from desalplant.store.store import Store

from support import call, ok


def _samples(k, flows=(20.0, 40.0, 60.0), restored=False):
    return [BedSample(flow=q, drop=k * q, restored=restored) for q in flows]


@pytest.fixture()
def diagnoser(store):
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    return FilterDiagnoser(store, clock, recorder)


# ------------------------------------------------------------- resistance fit


def test_fit_resistance_is_the_linear_slope():
    assert abs(fit_resistance(_samples(0.42)) - 0.42) < 1e-9
    assert abs(fit_resistance(_samples(0.18)) - 0.18) < 1e-9


def test_fit_resistance_raises_without_flow():
    with pytest.raises(ValueError):
        fit_resistance([BedSample(flow=0.0, drop=1.0)])


# --------------------------------------------------------------- verdicts


def test_dirty_running_bed_calls_for_a_wash(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.42):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.WASH
    assert verdict.label == "该洗"
    assert verdict.actionable() is True
    assert abs(verdict.resistance - 0.42) < 1e-9


def test_restored_bed_that_stays_high_means_replace(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.65, restored=True):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop, restored=True)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.REPLACE
    assert verdict.label == "该换"
    assert verdict.actionable() is True


def test_clean_bed_is_ok(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.18):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.OK
    assert verdict.label == "正常"
    assert verdict.actionable() is False


def test_high_running_resistance_alone_is_a_wash_not_replace(diagnoser):
    # A dirty bed masks the media baseline: it must be washed first, then the
    # restored baseline re-checked before replace is ever concluded.
    round_ = diagnoser.begin_round()
    for sample in _samples(0.65):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.WASH


# -------------------------------------------------------------- abstention


def test_too_few_samples_abstains(diagnoser):
    round_ = diagnoser.begin_round()
    diagnoser.record_sample(round_.round_id, 20.0, 8.0)
    diagnoser.record_sample(round_.round_id, 40.0, 16.0)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.ABSTAIN
    assert verdict.label == "判不了"
    assert verdict.reason
    assert verdict.resistance is None
    assert verdict.actionable() is False


def test_same_flow_samples_abstain(diagnoser):
    round_ = diagnoser.begin_round()
    for _ in range(3):
        diagnoser.record_sample(round_.round_id, 50.0, 20.0)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.ABSTAIN


def test_narrow_flow_range_abstains(diagnoser):
    round_ = diagnoser.begin_round()
    for flow in (49.0, 50.0, 51.0):
        diagnoser.record_sample(round_.round_id, flow, 0.42 * flow)
    verdict = diagnoser.close_round(round_.round_id)
    assert verdict.decision == Decision.ABSTAIN


# ------------------------------------------------------- version binding


def test_mid_round_recalibration_does_not_rebind_the_running_round(diagnoser):
    round_ = diagnoser.begin_round()  # bound to version 1
    baseline = diagnoser.recalibrate(wash_limit=0.35)  # now version 2
    assert baseline.version == 2
    for sample in _samples(0.42):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    verdict = diagnoser.close_round(round_.round_id)
    # The round opened under v1 and finishes under v1: old criteria apply.
    assert verdict.baseline_version == 1
    assert verdict.decision == Decision.WASH
    # A round opened after the migration uses the new criteria.
    next_round = diagnoser.begin_round()
    for sample in _samples(0.42):
        diagnoser.record_sample(next_round.round_id, sample.flow, sample.drop)
    next_verdict = diagnoser.close_round(next_round.round_id)
    assert next_verdict.baseline_version == 2
    assert next_verdict.decision == Decision.WASH


def test_recalibrate_carries_forward_unchanged_gates(diagnoser):
    baseline = diagnoser.recalibrate(wash_limit=0.35)
    assert baseline.version == 2
    assert baseline.wash_limit == 0.35
    assert baseline.replace_limit == 0.60  # carried forward from v1


# ---------------------------------------------------------- immutability


def test_verdict_is_immutable_after_recalibration(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.42):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    verdict = diagnoser.close_round(round_.round_id)
    diagnoser.recalibrate(wash_limit=0.99)  # would change the outcome if recomputed
    again = diagnoser.close_round(round_.round_id)
    assert again.to_dict() == verdict.to_dict()
    assert again.baseline_version == 1
    assert again.decision == Decision.WASH


def test_closed_round_rejects_new_samples(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.42):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    diagnoser.close_round(round_.round_id)
    with pytest.raises(RoundNotOpenError):
        diagnoser.record_sample(round_.round_id, 80.0, 33.6)


def test_unknown_round_is_not_found(diagnoser):
    assert diagnoser.get_verdict(999) is None
    with pytest.raises(UnknownRoundError):
        diagnoser.record_sample(999, 1.0, 1.0)


# ------------------------------------------------------- audit consistency


def test_consistency_is_clean_after_a_normal_lifecycle(diagnoser):
    round_ = diagnoser.begin_round()
    for sample in _samples(0.42):
        diagnoser.record_sample(round_.round_id, sample.flow, sample.drop)
    diagnoser.close_round(round_.round_id)
    diagnoser.recalibrate()
    report = diagnoser.audit_consistency()
    assert report["consistent"] is True
    assert report["mismatches"] == []
    assert report["verdicts"] == 1


# ------------------------------------------------------------- console routes


def test_filter_diagnosis_route_reports_current_baseline(console):
    body = ok(console, "GET", "/api/filter/diagnosis")
    assert body["baseline"]["version"] == 1
    assert body["open_round"] is None


def test_filter_round_lifecycle_through_the_console(console):
    opened = ok(console, "POST", "/api/filter/round", {})
    round_id = opened["round"]["round_id"]
    for flow in (20.0, 40.0, 60.0):
        ok(console, "POST", "/api/filter/sample", {"round_id": round_id, "flow": flow, "drop": 0.42 * flow})
    closed = ok(console, "POST", "/api/filter/close", {"round_id": round_id})
    assert closed["verdict"]["decision"] == "wash"
    assert closed["verdict"]["label"] == "该洗"
    fetched = ok(console, "GET", "/api/filter/verdict")
    assert fetched["decision"] == "wash"
    rounds = ok(console, "GET", "/api/filter/rounds")
    assert len(rounds["rounds"]) == 1


def test_filter_consistency_route(console):
    opened = ok(console, "POST", "/api/filter/round", {})
    round_id = opened["round"]["round_id"]
    for flow in (20.0, 40.0, 60.0):
        ok(console, "POST", "/api/filter/sample", {"round_id": round_id, "flow": flow, "drop": 0.18 * flow})
    ok(console, "POST", "/api/filter/close", {"round_id": round_id})
    report = ok(console, "GET", "/api/filter/consistency")
    assert report["consistent"] is True


def test_filter_calibrate_route_mints_a_new_version(console):
    body = ok(console, "POST", "/api/filter/calibrate", {"wash_limit": 0.35})
    assert body["baseline"]["version"] == 2
    assert body["baseline"]["wash_limit"] == 0.35


def test_baseline_versions_are_frozen_after_recalibration(console):
    ok(console, "POST", "/api/filter/calibrate", {"wash_limit": 0.35})
    v1 = ok(console, "GET", "/api/filter/baseline?version=1")
    assert v1["version"] == 1
    assert v1["wash_limit"] == 0.30  # unchanged by the v2 migration
    v2 = ok(console, "GET", "/api/filter/baseline?version=2")
    assert v2["version"] == 2
    assert v2["wash_limit"] == 0.35
    response = call(console, "GET", "/api/filter/baseline?version=99")
    assert response.status == 404


def test_posting_a_sample_to_a_closed_round_is_a_conflict(console):
    opened = ok(console, "POST", "/api/filter/round", {})
    round_id = opened["round"]["round_id"]
    for flow in (20.0, 40.0, 60.0):
        ok(console, "POST", "/api/filter/sample", {"round_id": round_id, "flow": flow, "drop": 0.42 * flow})
    ok(console, "POST", "/api/filter/close", {"round_id": round_id})
    response = call(console, "POST", "/api/filter/sample", {"round_id": round_id, "flow": 80.0, "drop": 33.6})
    assert response.status == 409
