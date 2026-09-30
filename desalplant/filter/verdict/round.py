"""One sampling round: samples collected between two round boundaries."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import BedSample, RoundKind


@dataclass
class Round:
    """Mutable accumulation state for the round currently open.

    The round is *pinned* to the baseline version active when it opened. That
    label never changes for the life of the round, which is what lets a
    baseline switch wait for the boundary instead of mutating running work.
    """

    round_no: int
    kind: RoundKind
    baseline_version: str
    opened_tick: int
    samples: list[BedSample] = field(default_factory=list)

    def add(self, sample: BedSample) -> None:
        """Collect one steady reading."""
        self.samples.append(sample)

    def as_tuple(self) -> tuple[BedSample, ...]:
        """Samples collected so far, as an immutable tuple."""
        return tuple(self.samples)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection."""
        return {
            "round_no": self.round_no,
            "kind": self.kind.value,
            "baseline_version": self.baseline_version,
            "opened_tick": self.opened_tick,
            "samples": [sample.to_dict() for sample in self.samples],
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Round":
        """Rebuild an open round from a persisted row."""
        return cls(
            round_no=int(row["round_no"]),
            kind=RoundKind(str(row["kind"])),
            baseline_version=str(row["baseline_version"]),
            opened_tick=int(row.get("opened_tick", 0)),
            samples=[BedSample.from_dict(item) for item in row.get("samples", ())],
        )
