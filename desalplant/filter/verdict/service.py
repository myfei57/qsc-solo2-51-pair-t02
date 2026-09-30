"""Application service owning bed rounds, verdicts and baseline migration."""

from dataclasses import replace

from ...audit.recorder import Recorder
from ...clock import LogicalClock
from ...errors import BaselineError, RoundError
from ...store import keys
from ...store.store import Store
from .baseline import BaselineRegistry, BaselineVersion
from .decision import decide
from .fit import evaluate
from .models import BedSample, DecisionThresholds, FitThresholds, RoundKind
from .round import Round
from .verdict import VerdictRecord, hash_payload, verify_chain


class ResistanceService:
    """State machine for filter-bed resistance verdicts.

    The version-switch rule enforced here is fixed once and for all: a round is
    pinned to the baseline active when it opens and is always judged and
    recorded under that version. A baseline change requested while a round is
    open is queued and activates only at the next round boundary — the round in
    flight is never re-cut under the new version, and committed verdicts are
    never rewritten.
    """

    def __init__(
        self,
        store: Store,
        clock: LogicalClock | None = None,
        recorder: Recorder | None = None,
        bed: str = keys.DEFAULT_BED,
        fit_thresholds: FitThresholds | None = None,
        decision_thresholds: DecisionThresholds | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._recorder = recorder
        self._bed = bed
        self.fit_thresholds = fit_thresholds if fit_thresholds is not None else FitThresholds()
        self.thresholds = (
            decision_thresholds if decision_thresholds is not None else DecisionThresholds()
        )
        self.registry = BaselineRegistry(store, bed, clock)

    # ------------------------------------------------------------ baselines

    def register_baseline(
        self,
        a: float,
        b: float,
        reference_flow: float | None = None,
        version: str | None = None,
        description: str = "",
    ) -> BaselineVersion:
        """Register a new immutable calibration (auto-active only for the first)."""
        flow = self.thresholds.reference_flow if reference_flow is None else reference_flow
        baseline = self.registry.register(a, b, flow, version, description)
        self._log("filter_baseline_registered", f"baseline {baseline.version} registered")
        return baseline

    def activate_baseline(self, version: str) -> str:
        """Request a version switch, queueing it while a round is open."""
        state = self.registry.request_activation(version, self.has_open_round())
        if state == "queued":
            self._log(
                "filter_baseline_change_queued",
                f"baseline {version} queued; running round keeps its pinned version",
            )
        else:
            self._log("filter_baseline_activated", f"baseline {version} active")
        return state

    # --------------------------------------------------------------- rounds

    def open_round(self, kind: RoundKind = RoundKind.SERVICE) -> Round:
        """Open a fresh round pinned to the currently active baseline."""
        existing = self.current_round()
        if existing is not None:
            raise RoundError(
                f"round {existing.round_no} ({existing.kind.value}) is still open"
            )
        active = self.registry.active()
        round_no = int(self._store.get(keys.filter_sequence_key(self._bed), 0) or 0) + 1
        round_obj = Round(
            round_no=round_no,
            kind=kind,
            baseline_version=active.version if active is not None else "",
            opened_tick=self._tick(),
        )
        self._save_round(round_obj)
        self._store.put(keys.filter_sequence_key(self._bed), round_no)
        self._log(
            "filter_round_opened",
            f"round {round_no} ({kind.value}) pinned to "
            f"'{round_obj.baseline_version or 'none'}'",
        )
        return round_obj

    def add_sample(self, sample: BedSample) -> Round:
        """Append a steady reading to the open round."""
        round_obj = self.require_round()
        round_obj.add(sample)
        self._save_round(round_obj)
        return round_obj

    def backwash(self) -> tuple[VerdictRecord | None, Round]:
        """Close any open service round, then open a post-wash verification.

        The wash is the round boundary: the service round (if any) is judged
        under its pinned version first; a queued baseline is promoted only
        after that, so the post-wash round opens under the new version while
        the verdict just issued stays under the old one.
        """
        current = self.current_round()
        service_record: VerdictRecord | None = None
        if current is not None:
            if current.kind is RoundKind.POST_WASH:
                raise RoundError("a post-wash verification round is already open")
            service_record = self.close_round()
        verification = self.open_round(RoundKind.POST_WASH)
        self._log("filter_backwash", f"backwash performed; round {verification.round_no} verifies recovery")
        return service_record, verification

    def close_round(self) -> VerdictRecord:
        """Judge the open round, commit its immutable verdict, promote pending."""
        round_obj = self.require_round()
        try:
            pinned = self.registry.get(round_obj.baseline_version)
        except BaselineError:
            pinned = None

        if pinned is not None:
            outcome = evaluate(
                round_obj.as_tuple(), pinned.reference_flow, self.fit_thresholds
            )
        else:
            outcome = evaluate(
                round_obj.as_tuple(), self.thresholds.reference_flow, self.fit_thresholds
            )

        failures = self._update_failures(round_obj, pinned, outcome)
        decision = decide(round_obj.kind, outcome, pinned, failures, self.thresholds)

        previous = self.latest_verdict()
        record = VerdictRecord(
            round_no=round_obj.round_no,
            kind=round_obj.kind,
            verdict=decision.verdict,
            baseline_version=round_obj.baseline_version,
            opened_tick=round_obj.opened_tick,
            closed_tick=self._tick(),
            ratio=decision.ratio,
            curve=outcome.curve,
            reason_code=decision.reason_code,
            reason_detail=decision.reason_detail,
            prev_hash=previous.record_hash if previous is not None else "",
            samples=round_obj.as_tuple(),
        )
        record = replace(record, record_hash=hash_payload(record))

        self._store.put(keys.filter_verdict_key(self._bed, record.round_no), record.to_dict())
        self._store.put(keys.filter_verdict_latest_key(self._bed), record.round_no)
        self._store.delete(keys.filter_round_key(self._bed))
        self._log(
            "filter_verdict",
            f"round {record.round_no} -> {record.verdict.value} "
            f"under {record.baseline_version or 'none'}"
            + (f" (ratio {record.ratio:.2f})" if record.ratio is not None else ""),
        )

        promoted = self.registry.promote_pending(round_open=False)
        if promoted is not None:
            self._log(
                "filter_baseline_activated",
                f"baseline {promoted.version} active from the next round",
            )
        return record

    # ------------------------------------------------------------- reading

    def current_round(self) -> Round | None:
        """The open round, if any."""
        row = self._store.get(keys.filter_round_key(self._bed))
        if not row:
            return None
        return Round.from_dict(row)

    def require_round(self) -> Round:
        """The open round, failing when none exists."""
        round_obj = self.current_round()
        if round_obj is None:
            raise RoundError("no sampling round is open")
        return round_obj

    def has_open_round(self) -> bool:
        """Whether a round is currently open."""
        return self.current_round() is not None

    def latest_verdict(self) -> VerdictRecord | None:
        """Newest committed verdict, if any."""
        round_no = self._store.get(keys.filter_verdict_latest_key(self._bed))
        if not round_no:
            return None
        return self.get_verdict(int(round_no))

    def get_verdict(self, round_no: int) -> VerdictRecord:
        """One committed verdict by round number."""
        row = self._store.require(keys.filter_verdict_key(self._bed, round_no))
        return VerdictRecord.from_dict(row)

    def list_verdicts(self) -> tuple[VerdictRecord, ...]:
        """All committed verdicts oldest-first."""
        prefix = keys.filter_verdict_prefix(self._bed)
        records = [
            VerdictRecord.from_dict(self._store.require(key))
            for key in self._store.keys(prefix)
            if not key.endswith("latest")
        ]
        return tuple(sorted(records, key=lambda record: record.round_no))

    def preview(self) -> dict:
        """Non-binding view of the open round for live console display."""
        round_obj = self.current_round()
        if round_obj is None:
            return {"open": False}
        pinned = None
        if round_obj.baseline_version:
            try:
                pinned = self.registry.get(round_obj.baseline_version)
            except BaselineError:
                pinned = None
        reference = pinned.reference_flow if pinned is not None else self.thresholds.reference_flow
        outcome = evaluate(round_obj.as_tuple(), reference, self.fit_thresholds)
        failures = self._failure_count(round_obj.baseline_version) if pinned else 0
        decision = decide(round_obj.kind, outcome, pinned, failures, self.thresholds)
        return {
            "open": True,
            "final": False,
            "round_no": round_obj.round_no,
            "kind": round_obj.kind.value,
            "baseline_version": round_obj.baseline_version,
            "samples": len(round_obj.samples),
            "verdict": decision.verdict.value,
            "ratio": decision.ratio,
            "reason_code": decision.reason_code,
            "reason_detail": decision.reason_detail,
            "pending_baseline": (
                self.registry.pending().version if self.registry.pending() else None
            ),
        }

    # ------------------------------------------------------------ internal

    def _update_failures(self, round_obj: Round, pinned: BaselineVersion | None, outcome) -> int:
        """Adjust the consecutive post-wash failure counter for the pinned version."""
        if round_obj.kind is not RoundKind.POST_WASH or pinned is None or not outcome.ok:
            return self._failure_count(round_obj.baseline_version)
        ratio = outcome.curve.reference_drop / pinned.reference_drop()
        if ratio >= self.thresholds.replace_ratio:
            failures = self._failure_count(pinned.version) + 1
        else:
            failures = 0
        self._store.put(keys.filter_failure_count_key(self._bed, pinned.version), failures)
        return failures

    def _failure_count(self, version: str) -> int:
        if not version:
            return 0
        return int(self._store.get(keys.filter_failure_count_key(self._bed, version), 0) or 0)

    def _save_round(self, round_obj: Round) -> None:
        self._store.put(keys.filter_round_key(self._bed), round_obj.to_dict())

    def _tick(self) -> int:
        return self._clock.tick if self._clock is not None else 0

    def _log(self, kind: str, detail: str) -> None:
        if self._recorder is not None:
            self._recorder.log(kind, detail)

    def verify_verdicts(self) -> bool:
        """Re-check the stored hash chain over every committed verdict."""
        return verify_chain(self.list_verdicts())
