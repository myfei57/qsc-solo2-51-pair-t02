"""Operations that change the state of the line."""

from .http import Request, Response, json_response


def start(console, request: Request) -> Response:
    """Bring the line up."""
    request.body()
    event = console.plant.start()
    return json_response({"status": "started", "event": event.to_dict()})


def shutdown(console, request: Request) -> Response:
    """Stop the line."""
    request.body()
    event = console.plant.shutdown()
    return json_response({"status": "stopped", "event": event.to_dict()})


def switch(console, request: Request) -> Response:
    """Move production to the standby tank."""
    request.body()
    event = console.plant.switch_tank()
    return json_response({"status": "switched", "event": event.to_dict()})


def maintain(console, request: Request) -> Response:
    """Run the maintenance routine."""
    request.body()
    event = console.plant.maintain()
    return json_response({"status": "maintained", "event": event.to_dict()})


def filter_begin_round(console, request: Request) -> Response:
    """Open a diagnostic round bound to the current baseline version."""
    request.body()
    round_ = console.plant.begin_filter_round()
    return json_response({"round": round_.to_dict()})


def filter_record_sample(console, request: Request) -> Response:
    """Post one (flow, drop) reading to an open diagnostic round."""
    body = request.body()
    round_ = console.plant.record_filter_sample(
        int(body["round_id"]),
        float(body["flow"]),
        float(body["drop"]),
        bool(body.get("restored", False)),
    )
    return json_response({"round": round_.to_dict()})


def filter_close_round(console, request: Request) -> Response:
    """Close a round and return its immutable verdict."""
    body = request.body()
    verdict = console.plant.close_filter_round(int(body["round_id"]))
    return json_response({"verdict": verdict.to_dict()})


def filter_calibrate(console, request: Request) -> Response:
    """Mint a new calibration baseline version."""
    body = request.body()
    gates = {
        name: body[name]
        for name in (
            "wash_limit",
            "replace_limit",
            "min_samples",
            "min_flow_points",
            "min_flow_ratio",
        )
        if name in body
    }
    baseline = console.plant.recalibrate_filter(**gates)
    return json_response({"baseline": baseline.to_dict()})
