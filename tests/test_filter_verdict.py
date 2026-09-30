"""Bed-resistance verdicts: fitting, wash/replace rules, abstains, migration."""

import pytest

from desalplant.audit.recorder import Recorder
from desalplant.clock import LogicalClock
from desalplant.errors import BaselineError, RoundError
from desalplant.filter.verdict import (
    BaselineRegistry,
    BedSample,
    DecisionThresholds,
    FitThresholds,
    Reason,
    ResistanceService,
    RoundKind,
    Verdict,
    evaluate,
)
from desalplant.filter.verdict.audit import audit_verdicts
from desalplant.filter.verdict.viscosity import temperature_factor, viscosity_mPa_s
from desalplant.store import keys
from desalplant.store.store import Store

FLOWS = (60.0, 80.0, 100.0)
A0, B0 = 0.18, 0.001


def make_sample(flow, factor=1.0, temperature_c=20.0):
    """Synthetic reading scaled off the clean-bed curve."""
    return BedSample(
        flow=flow,
        drop=factor * (A0 * flow + B0 * flow * flow),
        temperature_c=temperature_c,
    )


def feed(service, factor=1.0, temperatures=None):
    for index, flow in enumerate(FLOWS):
        temperature = 20.0 if temperatures is None else temperatures[index]
        service.add_sample(make_sample(flow, factor, temperature))


def fresh_service():
    store = Store()
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    service = ResistanceService(store, clock, recorder)
    service.register_baseline(A0, B0, version="v1")
    return service, store, clock, recorder


# ---------------------------------------------------------------- viscosity


def test_viscosity_table_endpoints_and_interpolation():
    assert viscosity_mPa_s(0.0) == 1.792
    assert viscosity_mPa_s(100.0) == 0.653  # clamped at the top
    mid = viscosity_mPa_s(22.5)
    assert 0.890 < mid < 1.002
    assert temperature_factor(20.0) == pytest.approx(1.0)
    # Cold water is more viscous: normalising down to 20 °C shrinks the drop.
    assert 0.0 < temperature_factor(10.0) < 1.0
    assert temperature_factor(30.0) > 1.0


def test_temperature_normalisation_keeps_a_cold_bed_from_looking_dirty():
    # Same physical bed; the cold reading carries the higher viscous drop.
    # Reduce it by the physical viscosity ratio before feeding both sets.
    from desalplant.filter.verdict.viscosity import viscosity_mPa_s

    def physical_sample(flow, temperature_c):
        # Reference-temperature bed loss, inflated by the real viscosity ratio.
        base = A0 * flow + B0 * flow * flow
        return BedSample(
            flow=flow,
            drop=base * viscosity_mPa_s(temperature_c) / viscosity_mPa_s(20.0),
            temperature_c=temperature_c,
        )

    cold = tuple(physical_sample(f, 5.0) for f in FLOWS)
    warm = tuple(make_sample(f) for f in FLOWS)
    cold_drop = evaluate(cold, 80.0).curve.reference_drop
    warm_drop = evaluate(warm, 80.0).curve.reference_drop
    assert cold_drop == pytest.approx(warm_drop, rel=1e-6)


# ---------------------------------------------------------------- fitting


def test_fit_recovers_clean_bed_coefficients():
    outcome = evaluate(tuple(make_sample(f) for f in FLOWS), 80.0)
    assert outcome.ok
    assert outcome.curve.a == pytest.approx(A0, abs=1e-9)
    assert outcome.curve.b == pytest.approx(B0, abs=1e-12)
    assert outcome.curve.r_squared > 0.999


@pytest.mark.parametrize(
    "samples,reason",
    [
        ((), Reason.TOO_FEW_SAMPLES),
        (tuple(make_sample(f) for f in (80.0, 90.0)), Reason.TOO_FEW_SAMPLES),
        (
            tuple(make_sample(f) for f in (80.0, 81.0, 82.0)),
            Reason.FLOW_SPAN_TOO_NARROW,
        ),
        (
            (
                make_sample(30.0),
                make_sample(50.0),
                make_sample(70.0),
            ),
            Reason.REFERENCE_NOT_BRACKETED,
        ),
        (
            tuple(make_sample(f, 1.0, t) for f, t in zip(FLOWS, (20.0, 20.0, 35.0))),
            Reason.TEMPERATURE_SPREAD_TOO_WIDE,
        ),
    ],
)
def test_fit_abstains_on_insufficient_or_unusable_samples(samples, reason):
    outcome = evaluate(samples, 80.0)
    assert not outcome.ok
    assert outcome.reason is reason


def test_fit_rejects_non_physical_and_noisy_curves():
    from desalplant.filter.verdict.fit import _fit_origin_linear

    # A sign-flipped middle point pushes the origin fit to a negative slope.
    points = [(60.0, 11.4), (80.0, -20.8), (100.0, 18.999)]
    a, b = _fit_origin_linear(points)
    assert a <= 0.0 or b <= 0.0

    # Gate-passing but wildly non-Ergun data fails the R² gate.
    noisy = (
        BedSample(60.0, 30.0),
        BedSample(80.0, 5.0),
        BedSample(100.0, 40.0),
    )
    assert evaluate(noisy, 80.0).reason is Reason.POOR_FIT


def test_bed_sample_rejects_bad_readings():
    with pytest.raises(ValueError):
        BedSample(flow=0.0, drop=1.0)
    with pytest.raises(ValueError):
        BedSample(flow=80.0, drop=-1.0)


# -------------------------------------------------------------- decisions


def test_service_round_dirty_bed_says_wash():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE)
    feed(service, factor=2.0)
    record = service.close_round()
    assert record.verdict is Verdict.WASH
    assert record.baseline_version == "v1"
    assert record.ratio > 1.5
    assert record.reason_code == "resistance_high"


def test_service_round_clean_bed_says_normal():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE)
    feed(service, factor=1.0)
    record = service.close_round()
    assert record.verdict is Verdict.NORMAL
    assert record.ratio == pytest.approx(1.0, abs=1e-9)


def test_service_round_never_goes_straight_to_replace():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE)
    feed(service, factor=5.0)
    assert service.close_round().verdict is Verdict.WASH


def test_first_failed_post_wash_recovery_says_wash_not_replace():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE)
    feed(service, 2.0)
    service.close_round()
    service.backwash()
    feed(service, factor=1.4)  # above replace_ratio 1.3, first failure
    record = service.close_round()
    assert record.kind is RoundKind.POST_WASH
    assert record.verdict is Verdict.WASH
    assert record.reason_code == "recovery_incomplete"


def test_consecutive_failed_recoveries_say_replace():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.4); first = service.close_round()
    assert first.verdict is Verdict.WASH

    service.open_round(RoundKind.SERVICE); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.4); second = service.close_round()
    assert second.verdict is Verdict.REPLACE
    assert second.reason_code == "media_exhausted"


def test_successful_recovery_resets_the_failure_counter():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.4); service.close_round()  # failure 1

    service.open_round(RoundKind.SERVICE); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.0); service.close_round()  # recovered

    service.open_round(RoundKind.SERVICE); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.4); again = service.close_round()
    assert again.verdict is Verdict.WASH  # counter restarted, not REPLACE


def test_insufficient_samples_abstain_even_on_a_dirty_looking_bed():
    service, *_ = fresh_service()
    service.open_round()
    service.add_sample(make_sample(80.0, factor=5.0))
    record = service.close_round()
    assert record.verdict is Verdict.ABSTAIN
    assert record.reason_code == Reason.TOO_FEW_SAMPLES.value
    assert record.curve is None
    assert record.ratio is None


def test_no_baseline_means_abstain():
    service = ResistanceService(Store(), LogicalClock())
    service.open_round()
    feed(service, 2.0)
    record = service.close_round()
    assert record.verdict is Verdict.ABSTAIN
    assert record.reason_code == Reason.NO_BASELINE.value


# ------------------------------------------------------- round lifecycle


def test_rounds_must_be_closed_before_another_opens():
    service, *_ = fresh_service()
    service.open_round()
    with pytest.raises(RoundError):
        service.open_round()
    assert service.require_round().round_no == 1


def test_samples_rejected_without_an_open_round():
    service, *_ = fresh_service()
    with pytest.raises(RoundError):
        service.add_sample(make_sample(80.0))


def test_backwash_closes_service_round_and_opens_post_wash_verification():
    service, *_ = fresh_service()
    service.open_round(RoundKind.SERVICE)
    feed(service, 2.0)
    service_record, verification = service.backwash()
    assert service_record.verdict is Verdict.WASH
    assert verification.kind is RoundKind.POST_WASH
    assert service.current_round().round_no == verification.round_no
    with pytest.raises(RoundError):
        service.backwash()  # already in a post-wash round


def test_round_numbers_resume_across_service_restarts():
    store = Store()
    clock = LogicalClock()
    service = ResistanceService(store, clock, Recorder(store, clock))
    service.register_baseline(A0, B0, version="v1")
    service.open_round()
    feed(service, 1.0)
    service.close_round()

    resumed = ResistanceService(store, clock, Recorder(store, clock))
    opened = resumed.open_round()
    assert opened.round_no == 2


# ------------------------------------------------- migration / versioning


def test_baseline_versions_are_immutable_and_unique():
    registry = BaselineRegistry(Store(), "main")
    registry.register(A0, B0, 80.0, version="v1")
    with pytest.raises(BaselineError):
        registry.register(A0, B0, 80.0, version="v1")
    with pytest.raises(BaselineError):
        registry.register(-0.1, B0, 80.0, version="v2")
    with pytest.raises(BaselineError):
        registry.get("missing")


def test_version_switch_during_a_round_is_queued_and_old_round_finishes_old():
    service, store, clock, recorder = fresh_service()
    opened = service.open_round(RoundKind.SERVICE)
    assert opened.baseline_version == "v1"
    service.register_baseline(0.30, 0.002, version="v2")

    assert service.activate_baseline("v2") == "queued"
    # Running round is still pinned to v1 and active baseline stays v1.
    assert service.current_round().baseline_version == "v1"
    assert service.registry.active().version == "v1"

    feed(service, 1.0)
    record = service.close_round()  # judged and frozen under v1
    assert record.baseline_version == "v1"
    assert service.registry.active().version == "v2"

    # Historical record is byte-for-byte unchanged; next round uses v2.
    stored = service.get_verdict(record.round_no)
    assert stored.baseline_version == "v1"
    assert stored.record_hash == record.record_hash
    new_round = service.open_round()
    assert new_round.baseline_version == "v2"


def test_version_switch_without_open_round_activates_immediately():
    service, *_ = fresh_service()
    service.register_baseline(0.30, 0.002, version="v2")
    assert service.activate_baseline("v2") == "activated"
    assert service.registry.active().version == "v2"


def test_old_verdict_is_explained_under_its_own_version_after_migration():
    service, store, clock, recorder = fresh_service()
    # Dirty bed judged WASH under v1.
    service.open_round(); feed(service, 2.0)
    old = service.close_round()
    assert old.verdict is Verdict.WASH and old.baseline_version == "v1"

    # v2 makes the exact same resistance look normal (looser baseline).
    service.register_baseline(A0 * 2, B0 * 2, version="v2")
    service.activate_baseline("v2")
    service.open_round(); feed(service, 2.0)
    new = service.close_round()
    assert new.verdict is Verdict.NORMAL and new.baseline_version == "v2"

    # The old row keeps its old verdict; audit reconciles per version.
    assert service.get_verdict(old.round_no).verdict is Verdict.WASH
    report = audit_verdicts(store, recorder)
    assert report.ok is True
    assert report.checked == 2


def test_only_one_pending_activation_is_allowed():
    service, *_ = fresh_service()
    service.open_round()
    service.register_baseline(0.3, 0.002, version="v2")
    service.register_baseline(0.4, 0.003, version="v3")
    assert service.activate_baseline("v2") == "queued"
    with pytest.raises(BaselineError):
        service.activate_baseline("v3")
    # Re-requesting the same queued version is idempotent.
    assert service.activate_baseline("v2") == "queued"


# ----------------------------------------------------------------- audit


def test_records_are_hash_chained_and_recompute_cleanly():
    service, store, clock, recorder = fresh_service()
    service.open_round(); feed(service, 2.0); service.close_round()
    service.backwash(); feed(service, 1.4); service.close_round()
    report = audit_verdicts(store, recorder)
    assert report.ok is True
    assert report.checked == 2
    assert report.audit_events_consistent is True
    assert service.verify_verdicts() is True


def test_audit_detects_a_rewritten_verdict():
    service, store, clock, recorder = fresh_service()
    service.open_round(); feed(service, 2.0)
    record = service.close_round()
    key = keys.filter_verdict_key("main", record.round_no)
    tampered = dict(store.require(key))
    tampered["verdict"] = "replace"
    store.put(key, tampered)

    report = audit_verdicts(store, recorder)
    assert report.ok is False
    codes = {finding.code for finding in report.findings}
    assert {"hash_mismatch", "verdict_mismatch"} <= codes


def test_audit_flags_missing_verdict_event():
    service, store, clock, recorder = fresh_service()
    service.open_round(); feed(service, 1.0); service.close_round()
    # A second recorder on the same store has no events in memory history?
    # Instead verify event-count mismatch by wiping the event pointer rows.
    store.put(keys.AUDIT_SEQUENCE, 0)
    fresh_recorder = Recorder(store, clock)
    report = audit_verdicts(store, fresh_recorder)
    assert report.audit_events_consistent is False


def test_audit_recomputes_with_stored_raw_samples():
    service, store, clock, recorder = fresh_service()
    service.open_round(); feed(service, 2.0)
    record = service.close_round()
    # Corrupting only the stored curve must be caught by refitting samples.
    key = keys.filter_verdict_key("main", record.round_no)
    tampered = dict(store.require(key))
    curve = dict(tampered["curve"])
    curve["a"] = curve["a"] * 3
    tampered["curve"] = curve
    tampered["record_hash"] = record.record_hash  # stale hash on purpose
    store.put(key, tampered)
    report = audit_verdicts(store, recorder)
    codes = {finding.code for finding in report.findings}
    assert "curve_mismatch" in codes
