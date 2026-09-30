"""Fit the bed's own resistance curve from multi-flow pressure samples.

The single-point reading the operators had before cannot separate a dirty bed
from a bed full of worn media, nor from a change in flow. Sampling the same bed
at several flows and fitting the Ergun form

    drop_norm = a * flow + b * flow ** 2

(with each drop normalised to the reference temperature) yields a flow-shaped
signature of the bed itself. The fit is deliberately conservative: any sample
set that fails a gate makes the round abstain instead of returning a curve.
"""

from .models import (
    BedSample,
    FitOutcome,
    FitRejection,
    FitThresholds,
    Reason,
    ResistanceCurve,
)
from .viscosity import temperature_factor


def _normalised(sample: BedSample) -> tuple[float, float]:
    """Return ``(flow, drop)`` with the drop reduced to reference viscosity."""
    return sample.flow, sample.drop * temperature_factor(sample.temperature_c)


def evaluate(
    samples: tuple[BedSample, ...] | list[BedSample],
    reference_flow: float,
    thresholds: FitThresholds | None = None,
) -> FitOutcome:
    """Gate the samples and, when they pass, fit the bed signature."""
    thresholds = thresholds if thresholds is not None else FitThresholds()
    samples = tuple(samples)

    rejection = _first_rejection(samples, reference_flow, thresholds)
    if rejection is not None:
        return FitOutcome(curve=None, rejection=rejection, samples=samples)

    points = [_normalised(sample) for sample in samples]
    a, b = _fit_origin_linear(points)
    r_squared = _r_squared_origin(points, a, b)
    if a <= 0.0 or b <= 0.0:
        return FitOutcome(
            curve=None,
            rejection=FitRejection(
                Reason.NON_PHYSICAL_FIT,
                f"fitted coefficients must be positive, got a={a:.6g}, b={b:.6g}",
            ),
            samples=samples,
        )
    if r_squared < thresholds.min_r_squared:
        return FitOutcome(
            curve=None,
            rejection=FitRejection(
                Reason.POOR_FIT,
                f"r_squared {r_squared:.4f} below {thresholds.min_r_squared}",
            ),
            samples=samples,
        )
    curve = ResistanceCurve(
        a=a,
        b=b,
        r_squared=r_squared,
        reference_drop=a * reference_flow + b * reference_flow**2,
        samples=len(samples),
    )
    return FitOutcome(curve=curve, rejection=None, samples=samples)


def _first_rejection(
    samples: tuple[BedSample, ...],
    reference_flow: float,
    thresholds: FitThresholds,
) -> FitRejection | None:
    """Check the data-quality gates in a fixed order and stop at the first."""
    if len(samples) < thresholds.min_samples:
        return FitRejection(
            Reason.TOO_FEW_SAMPLES,
            f"need at least {thresholds.min_samples} samples, have {len(samples)}",
        )

    flows = sorted({sample.flow for sample in samples})
    if len(flows) < thresholds.min_distinct_flows:
        return FitRejection(
            Reason.TOO_FEW_FLOWS,
            f"need at least {thresholds.min_distinct_flows} distinct flows, "
            f"have {len(flows)}",
        )

    span_ratio = flows[-1] / flows[0]
    if span_ratio < thresholds.min_flow_ratio:
        return FitRejection(
            Reason.FLOW_SPAN_TOO_NARROW,
            f"max/min flow ratio {span_ratio:.3f} below "
            f"{thresholds.min_flow_ratio}",
        )

    if not flows[0] <= reference_flow <= flows[-1]:
        return FitRejection(
            Reason.REFERENCE_NOT_BRACKETED,
            f"reference flow {reference_flow:g} not bracketed by sampled "
            f"range [{flows[0]:g}, {flows[-1]:g}]",
        )

    temperatures = [sample.temperature_c for sample in samples]
    spread = max(temperatures) - min(temperatures)
    if spread > thresholds.max_temperature_spread_c:
        return FitRejection(
            Reason.TEMPERATURE_SPREAD_TOO_WIDE,
            f"temperature spread {spread:.2f} °C exceeds "
            f"{thresholds.max_temperature_spread_c:g} °C",
        )
    return None


def _fit_origin_linear(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares fit of ``y = a*x + b*x**2`` forced through the origin."""
    s_xx = 0.0
    s_x2x2 = 0.0
    s_xx2 = 0.0
    s_xy = 0.0
    s_x2y = 0.0
    for x, y in points:
        x2 = x * x
        s_xx += x * x
        s_x2x2 += x2 * x2
        s_xx2 += x * x2
        s_xy += x * y
        s_x2y += x2 * y
    determinant = s_xx * s_x2x2 - s_xx2 * s_xx2
    a = (s_xy * s_x2x2 - s_x2y * s_xx2) / determinant
    b = (s_x2y * s_xx - s_xy * s_xx2) / determinant
    return a, b


def _r_squared_origin(points: list[tuple[float, float]], a: float, b: float) -> float:
    """R² against the no-intercept null model (mean forced to zero)."""
    total = 0.0
    residual = 0.0
    for x, y in points:
        fitted = a * x + b * x * x
        total += y * y
        residual += (y - fitted) ** 2
    if total <= 0.0:
        return 1.0 if residual == 0.0 else 0.0
    return max(0.0, 1.0 - residual / total)
