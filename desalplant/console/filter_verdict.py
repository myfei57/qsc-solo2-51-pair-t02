"""Console handlers for filter-bed resistance verdicts.

The console view and the external data feed both render the *same* stored
:class:`VerdictRecord` rows — the ``verdict`` and ``verdicts`` handlers are the
single read surface, while ``audit`` performs the independent consistency
check.
"""

from ..filter.verdict import BedSample, RoundKind
from . import routes
from .http import Request, Response, json_response


def _service(console):
    return console.plant.filter_resistance


def _round_kind(body: dict) -> RoundKind:
    raw = str(body.get("kind", "service"))
    try:
        return RoundKind(raw)
    except ValueError as error:
        raise ValueError(f"unknown round kind '{raw}'") from error


def open_round(console, request: Request) -> Response:
    """Open a sampling round pinned to the active baseline."""
    body = request.body()
    round_obj = _service(console).open_round(_round_kind(body))
    return json_response(
        {
            "round_no": round_obj.round_no,
            "kind": round_obj.kind.value,
            "baseline_version": round_obj.baseline_version,
            "opened_tick": round_obj.opened_tick,
        }
    )


def add_sample(console, request: Request) -> Response:
    """Append one steady (flow, drop[, temperature]) reading."""
    body = request.body()
    sample = BedSample(
        flow=float(body["flow"]),
        drop=float(body["drop"]),
        temperature_c=float(body.get("temperature_c", 20.0)),
    )
    round_obj = _service(console).add_sample(sample)
    return json_response({"round_no": round_obj.round_no, "samples": len(round_obj.samples)})


def close_round(console, request: Request) -> Response:
    """Judge the open round and commit its immutable verdict."""
    request.body()
    record = _service(console).close_round()
    return json_response(record.to_dict())


def backwash(console, request: Request) -> Response:
    """Close the service round, perform the wash, open post-wash verification."""
    request.body()
    service_record, verification = _service(console).backwash()
    return json_response(
        {
            "service_verdict": None if service_record is None else service_record.to_dict(),
            "post_wash_round": {
                "round_no": verification.round_no,
                "baseline_version": verification.baseline_version,
            },
        }
    )


def latest_verdict(console, request: Request) -> Response:
    """Latest committed verdict — the external data-feed entry point."""
    record = _service(console).latest_verdict()
    if record is None:
        return json_response(None)
    return json_response(record.to_dict())


def list_verdicts(console, request: Request) -> Response:
    """Every committed verdict, oldest-first, with a live open-round preview."""
    service = _service(console)
    records = [record.to_dict() for record in service.list_verdicts()]
    return json_response(
        {
            "count": len(records),
            "verdicts": records,
            "open_round": service.preview(),
        }
    )


def baselines(console, request: Request) -> Response:
    """Register a baseline (POST) or list versions and pending change (GET)."""
    service = _service(console)
    if request.method == "POST":
        body = request.body()
        baseline = service.register_baseline(
            a=float(body["a"]),
            b=float(body["b"]),
            reference_flow=(
                float(body["reference_flow"]) if "reference_flow" in body else None
            ),
            version=body.get("version"),
            description=str(body.get("description", "")),
        )
        state = None
        if body.get("activate"):
            state = service.activate_baseline(baseline.version)
        return json_response({"baseline": baseline.to_dict(), "activation": state})

    registry = service.registry
    active = registry.active()
    pending = registry.pending()
    return json_response(
        {
            "active": None if active is None else active.version,
            "pending": None if pending is None else pending.version,
            "versions": [version.to_dict() for version in registry.all_versions()],
        }
    )


def activate(console, request: Request) -> Response:
    """Request a baseline switch; queued while a round is open."""
    body = request.body()
    version = str(body["version"])
    state = _service(console).activate_baseline(version)
    return json_response({"version": version, "state": state})


def audit(console, request: Request) -> Response:
    """Independent audit re-check of the stored verdicts."""
    from ..filter.verdict.audit import audit_verdicts

    report = audit_verdicts(console.plant.store, console.plant.recorder)
    return json_response(report.to_dict())


ROUTE_TABLE = (
    ("POST", routes.FILTER_ROUND, open_round),
    ("POST", routes.FILTER_SAMPLE, add_sample),
    ("POST", routes.FILTER_CLOSE, close_round),
    ("POST", routes.FILTER_BACKWASH, backwash),
    ("GET", routes.FILTER_VERDICT, latest_verdict),
    ("GET", routes.FILTER_VERDICTS, list_verdicts),
    ("POST", routes.FILTER_BASELINES, baselines),
    ("GET", routes.FILTER_BASELINES, baselines),
    ("POST", routes.FILTER_ACTIVATE, activate),
    ("POST", routes.FILTER_AUDIT, audit),
)
