"""Key layout of the persisted control state."""

PLANT_STATE = "plant/state"
PLANT_STARTED_AT = "plant/started_at"
PLANT_STOPPED_AT = "plant/stopped_at"
PLANT_MAINTAINED_AT = "plant/maintained_at"

AUDIT_SEQUENCE = "audit/sequence"
AUDIT_EVENT_PREFIX = "audit/event/"
AUDIT_LAST_PREFIX = "audit/last/"

FILTER_ROOT = "filter/bed/"
DEFAULT_BED = "main"


def audit_event_key(sequence: int) -> str:
    """Key holding the event written at ``sequence``."""
    if sequence < 1:
        raise ValueError("audit sequence starts at one")
    return f"{AUDIT_EVENT_PREFIX}{sequence:06d}"


def audit_last_key(kind: str) -> str:
    """Key holding the sequence of the latest event of ``kind``."""
    return f"{AUDIT_LAST_PREFIX}{kind}"


def _bed(bed: str) -> str:
    """Normalise a bed identifier for use inside a key."""
    if not bed or "/" in bed:
        raise ValueError("bed id must be a non-empty, slash-free label")
    return bed


def filter_key(bed: str, suffix: str) -> str:
    """Namespaced key for a piece of per-bed filter verdict state."""
    return f"{FILTER_ROOT}{_bed(bed)}/{suffix}"


def filter_registry_key(bed: str = DEFAULT_BED) -> str:
    """Holding the baseline registry document (versions, active, pending)."""
    return filter_key(bed, "baselines")


def filter_round_key(bed: str) -> str:
    """Holding the round currently open on the bed, if any."""
    return filter_key(bed, "round/open")


def filter_sequence_key(bed: str) -> str:
    """Counter handing out round numbers on the bed."""
    return filter_key(bed, "round/sequence")


def filter_verdict_key(bed: str, round_no: int) -> str:
    """Immutably holding the verdict of a closed round."""
    if round_no < 1:
        raise ValueError("round numbers start at one")
    return filter_key(bed, f"verdict/{round_no:06d}")


def filter_verdict_latest_key(bed: str) -> str:
    """Pointer to the newest stored verdict for the bed."""
    return filter_key(bed, "verdict/latest")


def filter_verdict_prefix(bed: str) -> str:
    """Prefix covering every stored verdict for the bed."""
    return filter_key(bed, "verdict/")


def filter_failure_count_key(bed: str, version: str) -> str:
    """Running count of failed post-wash recoveries under one baseline."""
    if not version:
        raise ValueError("baseline version must not be empty")
    return filter_key(bed, f"postwash_failures/{version}")

