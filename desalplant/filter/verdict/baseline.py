"""Calibrated clean-bed baselines and their versioned registry.

A baseline captures a freshly washed, healthy bed's resistance signature.
Replacing it is a *migration*, not an edit: each calibration gets its own
immutable version row, and verdicts are always explained against the version
that was active when the round ran. A new version can therefore never rewrite
a conclusion already issued under an older one.
"""

from dataclasses import dataclass
from typing import Any, Mapping

from ...clock import LogicalClock
from ...errors import BaselineError
from ...store import keys
from ...store.store import Store


@dataclass(frozen=True)
class BaselineVersion:
    """One immutable clean-bed calibration."""

    version: str
    a: float
    b: float
    reference_flow: float
    created_tick: int = 0
    description: str = ""

    def reference_drop(self) -> float:
        """Bed resistance at the reference flow under reference viscosity."""
        return self.a * self.reference_flow + self.b * self.reference_flow**2

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection."""
        return {
            "version": self.version,
            "a": self.a,
            "b": self.b,
            "reference_flow": self.reference_flow,
            "created_tick": self.created_tick,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "BaselineVersion":
        """Rebuild a baseline from a persisted row."""
        return cls(
            version=str(row["version"]),
            a=float(row["a"]),
            b=float(row["b"]),
            reference_flow=float(row["reference_flow"]),
            created_tick=int(row.get("created_tick", 0)),
            description=str(row.get("description", "")),
        )


def validate_coefficients(a: float, b: float, reference_flow: float) -> None:
    """Reject coefficients that cannot describe a physical bed."""
    if a <= 0.0 or b <= 0.0:
        raise BaselineError("baseline coefficients must both be positive")
    if reference_flow <= 0.0:
        raise BaselineError("reference flow must be positive")


class BaselineRegistry:
    """Persisted set of immutable baselines with one active version."""

    def __init__(self, store: Store, bed: str, clock: LogicalClock | None = None) -> None:
        self._store = store
        self._bed = bed
        self._clock = clock
        self._key = keys.filter_registry_key(bed)

    # ------------------------------------------------------------ reading

    def active(self) -> BaselineVersion | None:
        """The version currently used to judge new rounds, if any."""
        document = self._load()
        label = document.get("active")
        if not label:
            return None
        return BaselineVersion.from_dict(document["versions"][label])

    def pending(self) -> BaselineVersion | None:
        """Version queued for activation at the next round boundary, if any."""
        document = self._load()
        label = document.get("pending")
        if not label:
            return None
        return BaselineVersion.from_dict(document["versions"][label])

    def get(self, version: str) -> BaselineVersion:
        """Look up one registered version."""
        document = self._load()
        row = document.get("versions", {}).get(version)
        if row is None:
            raise BaselineError(f"unknown baseline version '{version}'")
        return BaselineVersion.from_dict(row)

    def all_versions(self) -> tuple[BaselineVersion, ...]:
        """Every registered version in registration order."""
        document = self._load()
        return tuple(
            BaselineVersion.from_dict(document["versions"][label])
            for label in document.get("order", ())
        )

    # ------------------------------------------------------------ writing

    def register(
        self,
        a: float,
        b: float,
        reference_flow: float,
        version: str | None = None,
        description: str = "",
    ) -> BaselineVersion:
        """Add a new immutable calibration without activating it.

        Version rows are write-once: a label that already exists is refused so
        an old calibration can never be silently overwritten.
        """
        validate_coefficients(a, b, reference_flow)
        document = self._load()
        versions = document.setdefault("versions", {})
        label = version or f"v{len(versions) + 1}"
        if label in versions:
            raise BaselineError(f"baseline version '{label}' already exists")
        tick = self._clock.tick if self._clock is not None else 0
        baseline = BaselineVersion(
            version=label,
            a=a,
            b=b,
            reference_flow=reference_flow,
            created_tick=tick,
            description=description,
        )
        versions[label] = baseline.to_dict()
        document.setdefault("order", []).append(label)
        if not document.get("active"):
            document["active"] = label
        self._save(document)
        return baseline

    def request_activation(self, version: str, round_open: bool) -> str:
        """Ask for ``version`` to become active.

        While a round is open the request is parked in ``pending`` and takes
        effect only at the next round boundary — the running round keeps being
        judged under its pinned (old) version. With no round open the new
        version activates immediately. Returns the resulting state.
        """
        document = self._load()
        if version not in document.get("versions", {}):
            raise BaselineError(f"unknown baseline version '{version}'")
        if document.get("active") == version and not document.get("pending"):
            raise BaselineError(f"baseline version '{version}' is already active")
        queued = document.get("pending")
        if queued and queued != version:
            raise BaselineError(f"baseline activation already queued for '{queued}'")
        if round_open:
            document["pending"] = version
            self._save(document)
            return "queued"
        document["active"] = version
        document["pending"] = None
        self._save(document)
        return "activated"

    def promote_pending(self, round_open: bool) -> BaselineVersion | None:
        """Activate a queued version once no round is open.

        Called at a round boundary. Never called while a round runs, so a
        version switch cannot change the criteria of a round in flight.
        """
        document = self._load()
        label = document.get("pending")
        if not label or round_open:
            return None
        document["active"] = label
        document["pending"] = None
        self._save(document)
        return BaselineVersion.from_dict(document["versions"][label])

    # ---------------------------------------------------------- plumbing

    def _load(self) -> dict[str, Any]:
        row = self._store.get(self._key)
        if row is None:
            return {"versions": {}, "order": [], "active": None, "pending": None}
        return dict(row)

    def _save(self, document: dict[str, Any]) -> None:
        self._store.put(self._key, document)
