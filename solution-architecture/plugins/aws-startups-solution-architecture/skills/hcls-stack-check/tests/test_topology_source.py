"""Tests for the topology SOURCE layer (topology_source).

Runnable with stdlib only (pytest not installed): each test_* function raises
on failure. A __main__ block runs them all and prints PASS/FAIL. The real live
network call stays skipped: we exercise the fail-closed paths by (a)
monkeypatching fetch_live_topology to raise, and (b) monkeypatching the session
factory so the real fetch_live_topology hits an UnknownServiceError (the
verified condition where the installed SDK lacks the devops-agent client) with
no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import topology_ingest  # noqa: E402
import topology_source  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_topology.json")


def test_local_file_returns_dict_and_normalizes_to_three():
    topo = topology_source.load_topology(FIXTURE)
    assert isinstance(topo, dict), "expected a dict from the local file source"
    result = topology_ingest.normalize(topo)
    assert len(result.resources) == 3, f"expected 3 resources, got {len(result.resources)}"


def test_live_source_fail_closed_propagates():
    original = topology_source.fetch_live_topology

    def _boom(region=None):
        raise topology_source.TopologySourceError("space not configured")

    topology_source.fetch_live_topology = _boom
    try:
        raised = False
        try:
            topology_source.load_topology("live")
        except topology_source.TopologySourceError:
            raised = True
        assert raised, "expected TopologySourceError to propagate from live pull"
    finally:
        topology_source.fetch_live_topology = original


def test_default_none_is_live_and_fail_closed():
    original = topology_source.fetch_live_topology

    def _boom(region=None):
        raise topology_source.TopologySourceError("space not configured")

    topology_source.fetch_live_topology = _boom
    try:
        raised = False
        try:
            topology_source.load_topology(None)
        except topology_source.TopologySourceError:
            raised = True
        assert raised, "expected TopologySourceError when source is None (default live)"
    finally:
        topology_source.fetch_live_topology = original


def test_bogus_source_string_fails_closed():
    raised = False
    try:
        topology_source.load_topology("not-a-file-and-not-live")
    except topology_source.TopologySourceError:
        raised = True
    assert raised, "expected TopologySourceError for a non-file, non-live source"


def test_fetch_live_topology_fails_closed_when_sdk_lacks_client():
    # Exercise the REAL fetch_live_topology (no network): monkeypatch the
    # session factory so client("devops-agent") raises UnknownServiceError, the
    # verified condition in this environment. It must fail closed with a
    # TopologySourceError whose message points at the missing SDK client.
    try:
        from botocore.exceptions import UnknownServiceError
    except Exception:
        print("SKIP test_fetch_live_topology_fails_closed_when_sdk_lacks_client (no botocore)")
        return

    class _FakeSession:
        region_name = "eu-central-1"

        def client(self, name, region_name=None):
            raise UnknownServiceError(
                service_name=name, known_service_names=["s3", "ec2"]
            )

    original = topology_source._make_session
    topology_source._make_session = lambda: _FakeSession()
    try:
        raised = None
        try:
            topology_source.fetch_live_topology()
        except topology_source.TopologySourceError as exc:
            raised = exc
        assert raised is not None, "expected TopologySourceError, got none"
        msg = str(raised)
        # Message must make clear the missing client BLOCKS the live pull and
        # name both remedies (install/upgrade the client OR a local export).
        assert "NOT installed in the current AWS SDK" in msg, msg
        assert "live topology pull" in msg and "blocked" in msg, msg
        assert "--topology <path>" in msg, msg
    finally:
        topology_source._make_session = original


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
