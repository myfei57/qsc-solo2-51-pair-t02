"""Domain values for bed-resistance verdicts."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# Canonical verdict codes. ``NORMAL`` is the fourth operational answer given
# alongside the requested wash / replace / abstain outcomes: a healthy bed that
# needs no action must not be confused with an undecidable one.
class Verdict(str, Enum):
    """What the bed needs, according to one closed round."""

    WASH = "wash"
    REPLACE = "replace"
    ABSTAIN = "abstain"
    NORMAL = "normal"


class RoundKind(str, Enum):
    """Whether a round watches a bed in service or verifies it after a wash."""

    SERVICE = "service"
    POST_WASH = "post_wash"


# Machine-readable reasons backing an ABSTAIN verdict (and open-round status).
class Reason(str, Enum):
    TOO_FEW_SAMPLES = "too_few_samples"
    TOO_FEW_FLOWS = "too_few_distinct_flows"
    FLOW_SPAN_TOO_NARROW = "flow_span_too_narrow"
    REFERENCE_NOT_BRACKETED = "reference_flow_not_bracketed"
    TEMPERATURE_SPREAD_TOO_WIDE = "temperature_spread_too_wide"
    POOR_FIT = "poor_fit"
    NON_PHYSICAL_FIT = "non_physical_fit"
    NO_BASELINE = "no_active_baseline"
    PENDING = "round_open"


@dataclass(frozen=True)
class BedSample:
    """One steady ``(flow, differential pressure)`` reading at a temperature."""

    flow: float
    drop: float
    temperature_c: float = 20.0

    def __post_init__(self) -> None:
        if self.flow <= 0:
            raise ValueError("sample flow must be positive")
        if self.drop < 0:
            raise ValueError("pressure drop must not be negative")

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection."""
        return {
            "flow": self.flow,
            "drop": self.drop,
            "temperature_c": self.temperature_c,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "BedSample":
        """Rebuild a sample from a persisted row."""
        return cls(
            flow=float(row["flow"]),
            drop=float(row["drop"]),
            temperature_c=float(row.get("temperature_c", 20.0)),
        )


@dataclass(frozen=True)
class ResistanceCurve:
    """Fitted ``drop = mu_norm*(a*flow + b*flow**2)`` bed signature.

    ``a`` is the viscous (laminar) coefficient and ``b`` the inertial
    (turbulent) coefficient of the Ergun form, with drops reduced to the
    reference viscosity.
    """

    a: float
    b: float
    r_squared: float
    reference_drop: float
    samples: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection."""
        return {
            "a": self.a,
            "b": self.b,
            "r_squared": self.r_squared,
            "reference_drop": self.reference_drop,
            "samples": self.samples,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "ResistanceCurve":
        """Rebuild a fitted curve from a persisted row."""
        return cls(
            a=float(row["a"]),
            b=float(row["b"]),
            r_squared=float(row["r_squared"]),
            reference_drop=float(row["reference_drop"]),
            samples=int(row["samples"]),
        )


@dataclass(frozen=True)
class FitRejection:
    """Why the samples could not produce a usable curve."""

    reason: Reason
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection."""
        return {"reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True)
class FitThresholds:
    """Gates a sample set has to pass before its curve is trusted."""

    min_samples: int = 3
    min_distinct_flows: int = 3
    min_flow_ratio: float = 1.5
    max_temperature_spread_c: float = 10.0
    min_r_squared: float = 0.98


@dataclass(frozen=True)
class DecisionThresholds:
    """Resistance ratios, relative to the active clean-bed baseline."""

    wash_ratio: float = 1.5
    replace_ratio: float = 1.3
    required_failed_recoveries: int = 2
    reference_flow: float = 80.0


@dataclass(frozen=True)
class FitOutcome:
    """Result of attempting a fit: either a curve or a single rejection."""

    curve: ResistanceCurve | None = None
    rejection: FitRejection | None = None
    samples: tuple[BedSample, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Whether a usable curve was produced."""
        return self.curve is not None

    @property
    def reason(self) -> Reason | None:
        """Rejection reason, if any."""
        return None if self.rejection is None else self.rejection.reason
