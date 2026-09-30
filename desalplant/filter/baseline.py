"""Versioned calibration baseline for the media-bed diagnosis.

A baseline fixes the thresholds that turn a fitted resistance into a verdict.
Baselines are immutable once written: recalibrating mints a new version rather
than editing the old one, so a verdict can always be interpreted under the
criteria that were in force when it was issued.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Baseline:
    """Thresholds and sample gates bound to one calibration version."""

    version: int
    wash_limit: float
    replace_limit: float
    min_samples: int = 3
    min_flow_points: int = 3
    min_flow_ratio: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "wash_limit": self.wash_limit,
            "replace_limit": self.replace_limit,
            "min_samples": self.min_samples,
            "min_flow_points": self.min_flow_points,
            "min_flow_ratio": self.min_flow_ratio,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Baseline":
        return cls(
            version=int(row["version"]),
            wash_limit=float(row["wash_limit"]),
            replace_limit=float(row["replace_limit"]),
            min_samples=int(row.get("min_samples", 3)),
            min_flow_points=int(row.get("min_flow_points", 3)),
            min_flow_ratio=float(row.get("min_flow_ratio", 1.5)),
        )


# Version 1: a clean bed sits near 0.18 and a dirty one near 0.42, so the wash
# band opens between them and the replace band opens above the dirty regime.
DEFAULT_BASELINE = Baseline(
    version=1,
    wash_limit=0.30,
    replace_limit=0.60,
    min_samples=3,
    min_flow_points=3,
    min_flow_ratio=1.5,
)


def recalibrate(previous: Baseline, **overrides: Any) -> Baseline:
    """Mint the next calibration version, carrying forward unchanged gates.

    Every numeric gate may be overridden; the version always advances by one
    and the previous version is left untouched in the store.
    """
    values = {
        field.name: getattr(previous, field.name)
        for field in dataclasses.fields(previous)
        if field.name != "version"
    }
    for name, value in overrides.items():
        if name not in values:
            raise ValueError(f"unknown calibration gate: {name}")
        cast = type(getattr(previous, name))
        values[name] = cast(value)
    return Baseline(version=previous.version + 1, **values)
