"""Key layout of the persisted control state."""

PLANT_STATE = "plant/state"
PLANT_STARTED_AT = "plant/started_at"
PLANT_STOPPED_AT = "plant/stopped_at"
PLANT_MAINTAINED_AT = "plant/maintained_at"

AUDIT_SEQUENCE = "audit/sequence"
AUDIT_EVENT_PREFIX = "audit/event/"
AUDIT_LAST_PREFIX = "audit/last/"

FILTER_BASELINE_CURRENT = "filter/baseline/current"
FILTER_BASELINE_PREFIX = "filter/baseline/"
FILTER_ROUND_SEQUENCE = "filter/round/sequence"
FILTER_ROUND_PREFIX = "filter/round/"
FILTER_VERDICT_PREFIX = "filter/verdict/"


def audit_event_key(sequence: int) -> str:
    """Key holding the event written at ``sequence``."""
    if sequence < 1:
        raise ValueError("audit sequence starts at one")
    return f"{AUDIT_EVENT_PREFIX}{sequence:06d}"


def audit_last_key(kind: str) -> str:
    """Key holding the sequence of the latest event of ``kind``."""
    return f"{AUDIT_LAST_PREFIX}{kind}"


def filter_baseline_key(version: int) -> str:
    """Key holding the calibration baseline frozen at ``version``."""
    if version < 1:
        raise ValueError("baseline version starts at one")
    return f"{FILTER_BASELINE_PREFIX}{version:06d}"


def filter_round_key(round_id: int) -> str:
    """Key holding the diagnostic round opened at ``round_id``."""
    if round_id < 1:
        raise ValueError("round id starts at one")
    return f"{FILTER_ROUND_PREFIX}{round_id:06d}"


def filter_verdict_key(round_id: int) -> str:
    """Key holding the immutable verdict issued for ``round_id``."""
    if round_id < 1:
        raise ValueError("round id starts at one")
    return f"{FILTER_VERDICT_PREFIX}{round_id:06d}"
