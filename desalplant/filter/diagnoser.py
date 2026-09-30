"""Round-based media-bed diagnosis with versioned baselines and audit parity.

The diagnoser turns differential-pressure samples at different flows into a
bed resistance and a wash/replace/abstain verdict. Three rules keep the
conclusions trustworthy:

* A round binds the calibration version in force when it opens. Recalibrating
  mid-round never rebinds an open round: the running round finishes under the
  old baseline, and the next round uses the new one.
* Verdicts are immutable. Closing a round writes one verdict that is never
  overwritten; new samples open new rounds instead of rewriting history.
* Every issued verdict is cross-checked against the append-only audit record,
  so the console view and the external data feed reconcile with the audit
  caliber.
"""

import json
from typing import Any

from ..audit.recorder import Recorder
from ..clock import LogicalClock
from ..errors import RoundNotOpenError, StoreError, UnknownRoundError
from ..store import keys
from ..store.store import Store
from .baseline import DEFAULT_BASELINE, Baseline, recalibrate
from .resistance import BedSample, fit_resistance, insufficiency_reason
from .round import DiagnosticRound
from .verdict import Decision, Verdict


class FilterDiagnoser:
    """Issues immutable bed-resistance verdicts over versioned baselines."""

    def __init__(self, store: Store, clock: LogicalClock, recorder: Recorder) -> None:
        self._store = store
        self._clock = clock
        self._recorder = recorder

    # ------------------------------------------------------------ baselines

    def current_baseline(self) -> Baseline:
        """The calibration baseline in force, minting version 1 on first use."""
        row = self._store.get(keys.FILTER_BASELINE_CURRENT)
        if row is None:
            baseline = DEFAULT_BASELINE
            self._store.put(keys.FILTER_BASELINE_CURRENT, baseline.to_dict())
            self._store.put(
                keys.filter_baseline_key(baseline.version), baseline.to_dict()
            )
            return baseline
        return Baseline.from_dict(row)

    def baseline(self, version: int) -> Baseline | None:
        """The baseline at a given version, or ``None`` if it never existed."""
        row = self._store.get(keys.filter_baseline_key(version))
        return None if row is None else Baseline.from_dict(row)

    def recalibrate(self, **overrides: Any) -> Baseline:
        """Mint a new calibration version and record the migration.

        Recalibration never touches an open round: rounds already bound to an
        older version keep it, and only rounds opened afterwards use the new
        criteria.
        """
        previous = self.current_baseline()
        baseline = recalibrate(previous, **overrides)
        self._store.put(keys.FILTER_BASELINE_CURRENT, baseline.to_dict())
        self._store.put(
            keys.filter_baseline_key(baseline.version), baseline.to_dict()
        )
        self._recorder.log(
            "filter_baseline_version",
            json.dumps(
                {
                    "from": previous.version,
                    "to": baseline.version,
                    "wash_limit": baseline.wash_limit,
                    "replace_limit": baseline.replace_limit,
                },
                sort_keys=True,
            ),
        )
        return baseline

    # --------------------------------------------------------------- rounds

    def begin_round(self) -> DiagnosticRound:
        """Open a new round bound to the baseline version in force right now."""
        baseline = self.current_baseline()
        sequence = int(self._store.get(keys.FILTER_ROUND_SEQUENCE, 0) or 0) + 1
        round_ = DiagnosticRound(
            round_id=sequence,
            baseline_version=baseline.version,
            status="open",
            opened_tick=self._clock.tick,
        )
        self._store.put(keys.FILTER_ROUND_SEQUENCE, sequence)
        self._store.put(keys.filter_round_key(round_.round_id), round_.to_dict())
        self._recorder.log(
            "filter_round_open",
            json.dumps(
                {"round_id": round_.round_id, "baseline_version": baseline.version},
                sort_keys=True,
            ),
        )
        return round_

    def record_sample(
        self, round_id: int, flow: float, drop: float, restored: bool = False
    ) -> DiagnosticRound:
        """Append one (flow, drop) reading to an open round."""
        round_ = self._require_round(round_id)
        if not round_.open():
            raise RoundNotOpenError(
                f"round {round_id} is closed; open a new round for new samples"
            )
        if flow <= 0:
            raise ValueError("sample flow must be positive")
        round_.samples.append(
            BedSample(flow=float(flow), drop=float(drop), restored=restored)
        )
        self._store.put(keys.filter_round_key(round_id), round_.to_dict())
        return round_

    def close_round(self, round_id: int) -> Verdict:
        """Close a round and issue its immutable verdict.

        Closing is idempotent: a round that already closed returns the verdict
        it was first issued without recomputing or re-logging it, so history
        can never be rewritten by a later recalibration.
        """
        round_ = self._require_round(round_id)
        existing = self._store.get(keys.filter_verdict_key(round_id))
        if existing is not None:
            return Verdict.from_dict(existing)
        baseline = self.baseline(round_.baseline_version)
        if baseline is None:
            raise StoreError(
                f"round {round_.round_id} references missing baseline version "
                f"{round_.baseline_version}"
            )
        verdict = decide(round_, baseline, self._clock.tick)
        round_.status = "closed"
        round_.closed_tick = self._clock.tick
        self._store.put(keys.filter_round_key(round_id), round_.to_dict())
        self._store.put(keys.filter_verdict_key(round_id), verdict.to_dict())
        self._recorder.log(
            "filter_verdict",
            json.dumps(
                {
                    "round_id": verdict.round_id,
                    "baseline_version": verdict.baseline_version,
                    "decision": verdict.decision,
                    "resistance": verdict.resistance,
                },
                sort_keys=True,
            ),
        )
        return verdict

    def _require_round(self, round_id: int) -> DiagnosticRound:
        row = self._store.get(keys.filter_round_key(round_id))
        if row is None:
            raise UnknownRoundError(f"no diagnostic round {round_id}")
        return DiagnosticRound.from_dict(row)

    def get_round(self, round_id: int) -> DiagnosticRound | None:
        """The round at an id, or ``None`` when it was never opened."""
        row = self._store.get(keys.filter_round_key(round_id))
        return None if row is None else DiagnosticRound.from_dict(row)

    def latest_round(self) -> DiagnosticRound | None:
        """The newest round, or ``None`` when none has been opened."""
        sequence = int(self._store.get(keys.FILTER_ROUND_SEQUENCE, 0) or 0)
        return None if sequence <= 0 else self.get_round(sequence)

    def get_verdict(self, round_id: int) -> Verdict | None:
        """The verdict for a round, or ``None`` when it has not closed."""
        row = self._store.get(keys.filter_verdict_key(round_id))
        return None if row is None else Verdict.from_dict(row)

    def latest_verdict(self) -> Verdict | None:
        """The verdict of the newest round, or ``None`` when none closed."""
        round_ = self.latest_round()
        if round_ is None:
            return None
        return self.get_verdict(round_.round_id)

    def rounds(self) -> tuple[DiagnosticRound, ...]:
        """All rounds, oldest first."""
        sequence = int(self._store.get(keys.FILTER_ROUND_SEQUENCE, 0) or 0)
        collected: list[DiagnosticRound] = []
        for round_id in range(1, sequence + 1):
            round_ = self.get_round(round_id)
            if round_ is not None:
                collected.append(round_)
        return tuple(collected)

    # ----------------------------------------------------------- consistency

    def audit_consistency(self) -> dict[str, Any]:
        """Cross-check stored verdicts against the append-only audit record.

        Returns a report that is ``consistent`` only when every non-abstain
        verdict has a matching audit event, every audit event maps back to a
        stored verdict, and every stamped baseline version exists.
        """
        verdicts = [
            verdict
            for round_ in self.rounds()
            if (verdict := self.get_verdict(round_.round_id)) is not None
        ]
        events = self._recorder.events()
        verdict_events = [event for event in events if event.kind == "filter_verdict"]
        baseline_events = [
            event for event in events if event.kind == "filter_baseline_version"
        ]

        mismatches: list[str] = []
        event_rounds: dict[int, dict[str, Any]] = {}
        for event in verdict_events:
            try:
                payload = json.loads(event.detail)
            except (ValueError, TypeError):
                mismatches.append(
                    f"audit event {event.sequence} has unparsable detail"
                )
                continue
            round_id = int(payload.get("round_id", -1))
            event_rounds[round_id] = payload
            if self.get_verdict(round_id) is None:
                mismatches.append(
                    f"audit event {event.sequence} references missing round "
                    f"{round_id}"
                )

        for verdict in verdicts:
            payload = event_rounds.get(verdict.round_id)
            if payload is None:
                mismatches.append(
                    f"verdict for round {verdict.round_id} has no audit event"
                )
                continue
            if int(payload.get("baseline_version", -1)) != verdict.baseline_version:
                mismatches.append(
                    f"round {verdict.round_id} baseline version disagrees "
                    f"(verdict {verdict.baseline_version}, audit "
                    f"{payload.get('baseline_version')})"
                )
            if str(payload.get("decision")) != verdict.decision:
                mismatches.append(
                    f"round {verdict.round_id} decision disagrees "
                    f"(verdict {verdict.decision}, audit "
                    f"{payload.get('decision')})"
                )
            if self.baseline(verdict.baseline_version) is None:
                mismatches.append(
                    f"round {verdict.round_id} stamps unknown baseline version "
                    f"{verdict.baseline_version}"
                )

        for event in baseline_events:
            try:
                payload = json.loads(event.detail)
            except (ValueError, TypeError):
                mismatches.append(
                    f"baseline event {event.sequence} has unparsable detail"
                )
                continue
            version = int(payload.get("to", -1))
            if self.baseline(version) is None:
                mismatches.append(
                    f"baseline event {event.sequence} references unknown version "
                    f"{version}"
                )

        return {
            "consistent": not mismatches,
            "verdicts": len(verdicts),
            "audit_verdict_events": len(verdict_events),
            "audit_baseline_events": len(baseline_events),
            "checked": len(verdicts),
            "mismatches": mismatches,
        }


def decide(round_: DiagnosticRound, baseline: Baseline, tick: int) -> Verdict:
    """Pure verdict logic for a closed round under one baseline.

    The decision separates reversible fouling from irreversible media wear:
    a high *restored* (post-backwash) resistance means the media is spent,
    while a high *running* resistance with a sound restored baseline means
    the bed is dirty and a backwash will restore it.
    """
    samples = round_.samples
    reason = insufficiency_reason(
        samples,
        baseline.min_samples,
        baseline.min_flow_points,
        baseline.min_flow_ratio,
    )
    if reason is not None:
        return Verdict(
            round_id=round_.round_id,
            baseline_version=round_.baseline_version,
            decision=Decision.ABSTAIN,
            resistance=None,
            restored_resistance=None,
            running_resistance=None,
            samples=len(samples),
            tick=tick,
            reason=reason,
        )

    resistance = fit_resistance(samples)
    restored = [sample for sample in samples if sample.restored]
    running = [sample for sample in samples if not sample.restored]
    restored_resistance = fit_resistance(restored) if len(restored) >= 2 else None
    running_resistance = fit_resistance(running) if len(running) >= 2 else None

    if restored_resistance is not None and restored_resistance >= baseline.replace_limit:
        decision = Decision.REPLACE
    elif (
        (running_resistance is not None and running_resistance >= baseline.wash_limit)
        or (running_resistance is None and resistance >= baseline.wash_limit)
        or (
            restored_resistance is not None
            and restored_resistance >= baseline.wash_limit
        )
    ):
        decision = Decision.WASH
    else:
        decision = Decision.OK

    return Verdict(
        round_id=round_.round_id,
        baseline_version=round_.baseline_version,
        decision=decision,
        resistance=resistance,
        restored_resistance=restored_resistance,
        running_resistance=running_resistance,
        samples=len(samples),
        tick=tick,
    )
