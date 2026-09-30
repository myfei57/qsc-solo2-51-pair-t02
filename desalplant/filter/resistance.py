"""Bed resistance reduced from differential-pressure samples at different flows.

The media bed follows a linear head-loss law ``drop = k * flow`` (matching
:class:`~desalplant.filter.media.MediaFilter`). The bed's own resistance ``k``
is flow-normalised: samples taken at different flows collapse onto one line
whose slope is ``k``. That slope is fit through the origin and gated on sample
diversity, so a handful of same-flow readings can never fake a conclusion.
"""

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BedSample:
    """One (flow, differential pressure) reading of the media bed.

    ``restored`` marks a reading taken while the bed was freshly backwashed:
    a clean-baseline measurement rather than a dirty-run one.
    """

    flow: float
    drop: float
    restored: bool = False


def fit_resistance(samples: Sequence[BedSample]) -> float:
    """Least-squares slope of ``drop = k * flow`` through the origin.

    Returns the bed's own resistance ``k``. Raises when the flow denominator
    is zero (no usable flow) so a degenerate sample set can never yield a
    number by accident.
    """
    denominator = sum(sample.flow * sample.flow for sample in samples)
    if denominator <= 0:
        raise ValueError("cannot fit resistance without positive flow")
    numerator = sum(sample.flow * sample.drop for sample in samples)
    return numerator / denominator


def distinct_flows(samples: Sequence[BedSample]) -> tuple[int, float, float]:
    """Number of distinct positive flow readings together with their span."""
    flows = [sample.flow for sample in samples if sample.flow > 0]
    if not flows:
        return 0, 0.0, 0.0
    unique = sorted(set(flows))
    return len(unique), unique[0], unique[-1]


def insufficiency_reason(
    samples: Sequence[BedSample],
    min_samples: int,
    min_flow_points: int,
    min_flow_ratio: float,
) -> str | None:
    """Why a sample set cannot support a verdict, or ``None`` when it can.

    The bed resistance is only identifiable when enough samples exist, those
    samples span several distinct flow points, and the flow range is wide
    enough to pin the slope. Failing any gate means the diagnoser must
    abstain rather than guess.
    """
    if len(samples) < min_samples:
        return (
            f"insufficient samples: have {len(samples)}, need at least "
            f"{min_samples}"
        )
    points, low, high = distinct_flows(samples)
    if points < min_flow_points:
        return (
            f"insufficient flow points: have {points} distinct flows, need at "
            f"least {min_flow_points}"
        )
    if low <= 0 or high / low < min_flow_ratio:
        ratio = high / low if low > 0 else 0.0
        return (
            "flow range too narrow: need a max/min flow ratio of at least "
            f"{min_flow_ratio:g}, got {ratio:g}"
        )
    return None
