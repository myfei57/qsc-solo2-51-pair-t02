"""HTTP surface for filter-bed resistance verdicts."""

from support import call, ok, refused

FLOWS = (60.0, 80.0, 100.0)


def register_baseline(console, version="v1", a=0.18, b=0.001):
    return ok(
        console,
        "POST",
        "/api/filter/baselines",
        {"a": a, "b": b, "version": version},
    )


def feed(console, factor, temperatures=None):
    for index, flow in enumerate(FLOWS):
        payload = {"flow": flow, "drop": factor * (0.18 * flow + 0.001 * flow * flow)}
        if temperatures is not None:
            payload["temperature_c"] = temperatures[index]
        ok(console, "POST", "/api/filter/sample", payload)


def test_filter_routes_are_registered(console):
    registered = set(console.routes())
    for method, path in (
        ("GET", "/api/filter/verdict"),
        ("GET", "/api/filter/verdicts"),
        ("POST", "/api/filter/round"),
        ("POST", "/api/filter/sample"),
        ("POST", "/api/filter/close"),
        ("POST", "/api/filter/backwash"),
        ("POST", "/api/filter/baselines"),
        ("POST", "/api/filter/baselines/activate"),
        ("POST", "/api/filter/audit"),
    ):
        assert (method, path) in registered


def test_no_verdict_before_any_round(console):
    assert ok(console, "GET", "/api/filter/verdict") is None
    listing = ok(console, "GET", "/api/filter/verdicts")
    assert listing["count"] == 0
    assert listing["open_round"]["open"] is False


def test_dirty_service_round_is_wash_and_both_channels_agree(console):
    register_baseline(console)
    ok(console, "POST", "/api/filter/round", {"kind": "service"})
    feed(console, 2.0)
    closed = ok(console, "POST", "/api/filter/close")

    assert closed["verdict"] == "wash"
    assert closed["baseline_version"] == "v1"
    assert closed["ratio"] > 1.5
    assert closed["record_hash"]
    # External data feed reads the exact same stored row as the console.
    assert ok(console, "GET", "/api/filter/verdict") == closed


def test_insufficient_samples_abstain(console):
    register_baseline(console)
    ok(console, "POST", "/api/filter/round", {})
    ok(console, "POST", "/api/filter/sample", {"flow": 80, "drop": 50.0})
    body = ok(console, "POST", "/api/filter/close")
    assert body["verdict"] == "abstain"
    assert body["reason_code"] == "too_few_samples"


def test_two_failed_recoveries_replace_the_media(console):
    register_baseline(console)

    ok(console, "POST", "/api/filter/round", {}); feed(console, 2.0)
    ok(console, "POST", "/api/filter/close")
    ok(console, "POST", "/api/filter/backwash", {}); feed(console, 1.4)
    first = ok(console, "POST", "/api/filter/close")
    assert first["kind"] == "post_wash" and first["verdict"] == "wash"

    ok(console, "POST", "/api/filter/round", {}); feed(console, 2.0)
    ok(console, "POST", "/api/filter/close")
    ok(console, "POST", "/api/filter/backwash", {}); feed(console, 1.4)
    second = ok(console, "POST", "/api/filter/close")
    assert second["verdict"] == "replace"


def test_open_round_twice_is_a_conflict(console):
    register_baseline(console)
    ok(console, "POST", "/api/filter/round", {})
    response = refused(console, "POST", "/api/filter/round", {})
    assert response.status == 409


def test_sampling_without_a_round_is_a_conflict(console):
    register_baseline(console)
    response = refused(console, "POST", "/api/filter/sample", {"flow": 80, "drop": 10})
    assert response.status == 409


def test_bad_sample_payload_is_malformed(console):
    register_baseline(console)
    ok(console, "POST", "/api/filter/round", {})
    response = refused(
        console, "POST", "/api/filter/sample", {"flow": 0, "drop": 10}
    )
    assert response.status == 400


def test_baselines_list_register_and_activate(console):
    register_baseline(console, "v1")
    register_baseline(console, "v2", a=0.3, b=0.002)
    body = ok(console, "GET", "/api/filter/baselines")
    assert body["active"] == "v1"  # second registration does not auto-activate
    assert [v["version"] for v in body["versions"]] == ["v1", "v2"]

    activated = ok(
        console, "POST", "/api/filter/baselines/activate", {"version": "v2"}
    )
    assert activated == {"version": "v2", "state": "activated"}


def test_version_change_mid_round_is_queued_and_applied_at_boundary(console):
    register_baseline(console, "v1")
    register_baseline(console, "v2", a=0.3, b=0.002)

    ok(console, "POST", "/api/filter/round", {})
    feed(console, 1.0)
    queued = ok(console, "POST", "/api/filter/baselines/activate", {"version": "v2"})
    assert queued["state"] == "queued"

    listing = ok(console, "GET", "/api/filter/verdicts")
    assert listing["open_round"]["pending_baseline"] == "v2"

    closed = ok(console, "POST", "/api/filter/close")
    assert closed["baseline_version"] == "v1"  # running round finished under v1
    assert ok(console, "GET", "/api/filter/baselines")["active"] == "v2"

    next_round = ok(console, "POST", "/api/filter/round", {})
    assert next_round["baseline_version"] == "v2"


def test_backwash_is_the_round_boundary_for_a_queued_change(console):
    register_baseline(console, "v1")
    register_baseline(console, "v2", a=0.3, b=0.002)
    ok(console, "POST", "/api/filter/round", {}); feed(console, 2.0)
    ok(console, "POST", "/api/filter/baselines/activate", {"version": "v2"})

    body = ok(console, "POST", "/api/filter/backwash", {})
    # The just-closed service verdict stays on the old version...
    assert body["service_verdict"]["baseline_version"] == "v1"
    # ...and the post-wash verification opens already on the new version.
    assert body["post_wash_round"]["baseline_version"] == "v2"


def test_historical_verdict_is_not_rewritten_by_new_samples(console):
    register_baseline(console, "v1")
    ok(console, "POST", "/api/filter/round", {}); feed(console, 2.0)
    old = ok(console, "POST", "/api/filter/close")

    # A looser v2 would judge the same bed normal, yet history is frozen.
    ok(
        console,
        "POST",
        "/api/filter/baselines",
        {"a": 0.36, "b": 0.002, "version": "v2", "activate": True},
    )
    again = ok(console, "GET", "/api/filter/verdict")
    assert again["round_no"] == old["round_no"]
    assert again["verdict"] == "wash"
    assert again["baseline_version"] == "v1"
    assert again["record_hash"] == old["record_hash"]


def test_audit_endpoint_recomputes_and_matches(console):
    register_baseline(console)
    ok(console, "POST", "/api/filter/round", {}); feed(console, 2.0)
    ok(console, "POST", "/api/filter/close")
    ok(console, "POST", "/api/filter/backwash", {}); feed(console, 1.0)
    ok(console, "POST", "/api/filter/close")

    body = ok(console, "POST", "/api/filter/audit", {})
    assert body["ok"] is True
    assert body["checked"] == 2
    assert body["audit_events_consistent"] is True
    assert body["findings"] == []


def test_unknown_baseline_activation_is_conflict(console):
    register_baseline(console)
    response = refused(
        console, "POST", "/api/filter/baselines/activate", {"version": "nope"}
    )
    assert response.status == 409
