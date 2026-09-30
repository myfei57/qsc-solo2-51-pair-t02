"""Audit-side consistency check for bed-resistance verdicts.

The audit path never trusts the stored verdict blindly and never repairs it:
it re-derives every expectation from the raw samples stored inside the record
and the *baseline version named on that record* (a historical version, never
today's active one), then compares. A discrepancy is surfaced for
investigation and never mutates history.
"""

from dataclasses import dataclass

from ...audit.recorder import Recorder
from ...store import keys
from ...store.store import Store
from .baseline import BaselineRegistry
from .decision import decide
from .fit import evaluate
from .models import (
    DecisionThresholds,
    FitThresholds,
    RoundKind,
)
from .verdict import VerdictRecord, hash_payload, verify_chain

CURVE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class AuditFinding:
    """One discrepancy, if any."""

    round_no: int
    code: str
    detail: str

    def to_dict(self) -> dict:
        """JSON-friendly projection."""
        return {"round_no": self.round_no, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class AuditReport:
    """Result of checking every stored verdict."""

    ok: bool
    checked: int
    findings: tuple[AuditFinding, ...]
    audit_events_consistent: bool

    def to_dict(self) -> dict:
        """JSON-friendly projection."""
        return {
            "ok": self.ok,
            "checked": self.checked,
            "findings": [finding.to_dict() for finding in self.findings],
            "audit_events_consistent": self.audit_events_consistent,
        }


def audit_verdicts(
    store: Store,
    recorder: Recorder | None = None,
    bed: str = keys.DEFAULT_BED,
    fit_thresholds: FitThresholds | None = None,
    decision_thresholds: DecisionThresholds | None = None,
) -> AuditReport:
    """Re-check hashes, refit samples, re-derive ratio and verdict per record."""
    fit_thresholds = fit_thresholds if fit_thresholds is not None else FitThresholds()
    decision_thresholds = (
        decision_thresholds if decision_thresholds is not None else DecisionThresholds()
    )
    records = _load_records(store, bed)
    findings: list[AuditFinding] = []

    if not verify_chain(records):
        findings.append(AuditFinding(0, "chain_broken", "verdict hash chain is broken"))

    registry = BaselineRegistry(store, bed)
    failures: dict[str, int] = {}
    for record in records:
        findings.extend(
            _check_record(
                registry, record, failures, fit_thresholds, decision_thresholds
            )
        )

    events_consistent = _events_consistent(recorder, records)
    if recorder is not None and not events_consistent:
        findings.append(
            AuditFinding(0, "audit_event_mismatch", "audit log does not match stored verdicts")
        )

    return AuditReport(
        ok=not findings,
        checked=len(records),
        findings=tuple(findings),
        audit_events_consistent=events_consistent,
    )


def _check_record(
    registry: BaselineRegistry,
    record: VerdictRecord,
    failures: dict[str, int],
    fit_thresholds: FitThresholds,
    decision_thresholds: DecisionThresholds,
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if record.record_hash != hash_payload(record):
        findings.append(AuditFinding(record.round_no, "hash_mismatch", "record digest changed"))

    baseline = None
    if record.baseline_version:
        try:
            baseline = registry.get(record.baseline_version)
        except Exception:  # noqa: BLE001 - lookup failure is itself a finding
            findings.append(
                AuditFinding(
                    record.round_no,
                    "missing_baseline",
                    f"baseline {record.baseline_version} referenced by verdict is unavailable",
                )
            )

    # Independent refit of the raw samples the record froze.
    outcome = evaluate(
        record.samples,
        baseline.reference_flow if baseline is not None else decision_thresholds.reference_flow,
        fit_thresholds,
    )
    if outcome.ok != (record.curve is not None):
        findings.append(
            AuditFinding(
                record.round_no,
                "fit_status_mismatch",
                f"stored curve present={record.curve is not None} but refit ok={outcome.ok}",
            )
        )
    if outcome.ok and record.curve is not None:
        refit = outcome.curve
        for name, stored, fresh in (
            ("a", record.curve.a, refit.a),
            ("b", record.curve.b, refit.b),
            ("reference_drop", record.curve.reference_drop, refit.reference_drop),
        ):
            if not _close(stored, fresh):
                findings.append(
                    AuditFinding(
                        record.round_no,
                        "curve_mismatch",
                        f"stored coefficient {name}={stored:.6g} disagrees with refit {fresh:.6g}",
                    )
                )

    # Replay the per-baseline post-wash failure counter and re-derive the
    # decision using the same pure function the service used at the time.
    if baseline is not None:
        count = failures.get(baseline.version, 0)
        if record.kind is RoundKind.POST_WASH and outcome.ok:
            ratio = outcome.curve.reference_drop / baseline.reference_drop()
            count = count + 1 if ratio >= decision_thresholds.replace_ratio else 0
            failures[baseline.version] = count
        expected = decide(
            record.kind, outcome, baseline, count, decision_thresholds
        )
        if record.ratio is not None and expected.ratio is not None and not _close(
            record.ratio, expected.ratio
        ):
            findings.append(
                AuditFinding(record.round_no, "ratio_mismatch", "stored ratio disagrees with refit")
            )
        if record.verdict is not expected.verdict:
            findings.append(
                AuditFinding(
                    record.round_no,
                    "verdict_mismatch",
                    f"record says {record.verdict.value} but baseline "
                    f"{baseline.version} rules re-derive {expected.verdict.value}",
                )
            )
        if record.reason_code != expected.reason_code:
            findings.append(
                AuditFinding(
                    record.round_no,
                    "reason_mismatch",
                    f"stored reason '{record.reason_code}' disagrees with "
                    f"re-derived '{expected.reason_code}'",
                )
            )
    return findings


def _close(left: float, right: float) -> bool:
    """Absolute/relative float closeness used across the re-derivation."""
    return abs(left - right) <= CURVE_TOLERANCE + CURVE_TOLERANCE * max(
        abs(left), abs(right)
    )


def _events_consistent(recorder: Recorder | None, records: tuple[VerdictRecord, ...]) -> bool:
    """Whether one filter_verdict audit event exists per committed verdict."""
    if recorder is None:
        return True
    verdict_events = [event for event in recorder.events() if event.kind == "filter_verdict"]
    return len(verdict_events) == len(records)


def _load_records(store: Store, bed: str) -> tuple[VerdictRecord, ...]:
    prefix = keys.filter_verdict_prefix(bed)
    records = [
        VerdictRecord.from_dict(store.require(key))
        for key in store.keys(prefix)
        if not key.endswith("latest")
    ]
    return tuple(sorted(records, key=lambda record: record.round_no))
