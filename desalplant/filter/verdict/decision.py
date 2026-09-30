"""Pure decision rules mapping a fitted curve onto a verdict.

Kept free of storage and clock dependencies so the audit path can recompute the
same verdict from stored samples using this very module and compare.

Semantics
---------
* A *service* round watches the bed between washes. High own-resistance there
  means accumulated solids: wash. A media verdict is never issued mid service,
  because a single curve cannot tell solids apart from worn media.
* A *post-wash* round samples the bed right after a backwash. If resistance
  fails to return under ``replace_ratio`` of the clean-bed baseline, the wash
  did not recover the media. One failure says wash again / inspect; the
  configured number of consecutive failures under the same baseline says
  replace.
* Anything without a usable curve or without an active baseline abstains.
"""

from dataclasses import dataclass

from .baseline import BaselineVersion
from .models import (
    DecisionThresholds,
    FitOutcome,
    Reason,
    ResistanceCurve,
    RoundKind,
    Verdict,
)


@dataclass(frozen=True)
class Decision:
    """Outcome of applying the rules to one round."""

    verdict: Verdict
    ratio: float | None
    reason_code: str
    reason_detail: str


def decide(
    kind: RoundKind,
    outcome: FitOutcome,
    baseline: BaselineVersion | None,
    failed_recoveries: int,
    thresholds: DecisionThresholds | None = None,
) -> Decision:
    """Apply the wash / replace / abstain rules to a fitted round."""
    thresholds = thresholds if thresholds is not None else DecisionThresholds()
    if baseline is None:
        return Decision(
            Verdict.ABSTAIN,
            None,
            Reason.NO_BASELINE.value,
            "no active clean-bed baseline to compare against",
        )
    if not outcome.ok or outcome.curve is None:
        rejection = outcome.rejection
        code = rejection.reason.value if rejection is not None else Reason.TOO_FEW_SAMPLES.value
        detail = rejection.detail if rejection is not None else "no usable fit"
        return Decision(Verdict.ABSTAIN, None, code, detail)

    curve: ResistanceCurve = outcome.curve
    ratio = curve.reference_drop / baseline.reference_drop()

    if kind is RoundKind.POST_WASH:
        return _decide_post_wash(ratio, curve, baseline, failed_recoveries, thresholds)
    return _decide_service(ratio, thresholds)


def _decide_service(
    ratio: float, thresholds: DecisionThresholds
) -> Decision:
    """In service, elevated own-resistance means fouling: wash."""
    if ratio >= thresholds.wash_ratio:
        return Decision(
            Verdict.WASH,
            ratio,
            "resistance_high",
            f"own resistance {ratio:.2f}x clean-bed baseline; "
            "fouling during service, backwash required",
        )
    return Decision(
        Verdict.NORMAL,
        ratio,
        "within_band",
        f"own resistance {ratio:.2f}x clean-bed baseline; no action",
    )


def _decide_post_wash(
    ratio: float,
    curve: ResistanceCurve,
    baseline: BaselineVersion,
    failed_recoveries: int,
    thresholds: DecisionThresholds,
) -> Decision:
    """After a wash, unrecoverable resistance across cycles means worn media."""
    if ratio < thresholds.replace_ratio:
        return Decision(
            Verdict.NORMAL,
            ratio,
            "recovered",
            f"post-wash resistance {ratio:.2f}x baseline; bed recovered",
        )
    if failed_recoveries >= thresholds.required_failed_recoveries:
        return Decision(
            Verdict.REPLACE,
            ratio,
            "media_exhausted",
            f"post-wash resistance {ratio:.2f}x baseline after "
            f"{failed_recoveries} consecutive failed recoveries under "
            f"baseline {baseline.version}; media cannot be restored by washing",
        )
    return Decision(
        Verdict.WASH,
        ratio,
        "recovery_incomplete",
        f"post-wash resistance {ratio:.2f}x baseline; "
        f"failure {failed_recoveries} of "
        f"{thresholds.required_failed_recoveries} before media is declared worn",
    )
