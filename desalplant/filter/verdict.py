"""Immutable conclusion of one diagnostic round."""

from dataclasses import dataclass
from typing import Any, Mapping


class Decision:
    """Verdict codes; kept as string constants for JSON stability."""

    WASH = "wash"  # 该洗: reversible fouling, a backwash restores the bed
    REPLACE = "replace"  # 该换: irreversible, backwash no longer restores it
    OK = "ok"  # 正常: resistance in band, no action
    ABSTAIN = "abstain"  # 判不了: samples insufficient, no conclusion


# Chinese labels surfaced on the console display alongside the code.
LABELS = {
    Decision.WASH: "该洗",
    Decision.REPLACE: "该换",
    Decision.OK: "正常",
    Decision.ABSTAIN: "判不了",
}


@dataclass(frozen=True)
class Verdict:
    """One round's conclusion, stamped with the baseline version in force."""

    round_id: int
    baseline_version: int
    decision: str
    resistance: float | None
    restored_resistance: float | None
    running_resistance: float | None
    samples: int
    tick: int
    reason: str = ""

    @property
    def label(self) -> str:
        """Chinese display label for the decision."""
        return LABELS.get(self.decision, self.decision)

    def actionable(self) -> bool:
        """Whether the verdict demands maintenance (wash or replace)."""
        return self.decision in (Decision.WASH, Decision.REPLACE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "baseline_version": self.baseline_version,
            "decision": self.decision,
            "label": self.label,
            "resistance": self.resistance,
            "restored_resistance": self.restored_resistance,
            "running_resistance": self.running_resistance,
            "samples": self.samples,
            "tick": self.tick,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Verdict":
        return cls(
            round_id=int(row["round_id"]),
            baseline_version=int(row["baseline_version"]),
            decision=str(row["decision"]),
            resistance=_optional_float(row.get("resistance")),
            restored_resistance=_optional_float(row.get("restored_resistance")),
            running_resistance=_optional_float(row.get("running_resistance")),
            samples=int(row.get("samples", 0)),
            tick=int(row.get("tick", 0)),
            reason=str(row.get("reason", "")),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
