"""Entry point: wire source -> ingest -> checks -> report.

Usage:
    python src/main.py [--topology <path|live>]

Default is a LIVE pull from the AWS DevOps Agent MCP endpoint. Pass a local
.json export path (via --topology or as a bare positional arg) for testing or
offline use.
"""
from __future__ import annotations

import argparse
import sys

import hipaa_check
import hipaa_source
import preflight
import region_lookup
import report
import topology_ingest
import topology_source as topology_source_module


def _re_status_safe(region: str) -> dict:
    """Get Resource Explorer status, tolerant of AWS call failures.

    check_resource_explorer_enabled already handles ResourceNotFound (enabled
    False) and permission errors (enabled None with an "error" key). This wrapper
    is a final safety net: any unexpected exception is treated as undetermined
    (enabled None) so the pipeline still runs and we avoid a false HIGH.
    """
    try:
        return preflight.check_resource_explorer_enabled(region)
    except Exception as exc:
        return {"enabled": None, "aggregator_region": None, "checked_region": region, "error": str(exc)}


def build_rows(result: "topology_ingest.TopologyIngestResult", snapshot: dict) -> list[dict]:
    """Combine per-resource HIPAA + EU checks into report rows."""
    index = hipaa_check.build_index(snapshot)
    rows: list[dict] = []
    for res in result.resources:
        verdict = hipaa_check.check_service(res.service, index)
        rows.append(
            {
                "resource_id": res.resource_id,
                "service": res.service,
                "hipaa_eligible": verdict["verdict"],
                "caveat": verdict["caveat"],
                "region": res.region,
                "eu": "yes" if region_lookup.is_eu_region(res.region) else "no",
            }
        )
    return rows


def run(topology_source: str | None = None) -> str:
    """Run the full pipeline and return the markdown report.

    topology_source.load_topology() (live pull by default, or a local .json
    export path for testing/offline) -> topology_ingest.normalize() ->
    hipaa_source.load_snapshot() -> preflight.assess_coverage() -> per-resource
    HIPAA + EU checks -> report.render_report(...). Both source resolution and
    the HIPAA snapshot are fail-closed.
    """
    raw_topology = topology_source_module.load_topology(topology_source)
    source_line = _describe_source(topology_source)

    snapshot = hipaa_source.load_snapshot()  # live + fail-closed (or dev override)

    ingest_result = topology_ingest.normalize(raw_topology)

    # RE preflight. Probe region is best-effort (first resource region, else a
    # neutral default). RE status can be True, False, or None (undetermined,
    # e.g. permissions). When undetermined and the RE path is not already active,
    # surface an INFO note rather than a false HIGH.
    probe_region = next((r.region for r in ingest_result.resources if r.region), "us-east-1")
    re_status = _re_status_safe(probe_region)
    re_active = "resource-explorer" in set(ingest_result.discovery_paths or [])
    if re_status.get("enabled") is None and not re_active:
        detail = re_status.get("error")
        suffix = f" ({detail})" if detail else ""
        coverage_verdict = {
            "severity": preflight.INFO,
            "message": (
                "Resource Explorer status could not be determined "
                f"(permissions){suffix}. Coverage of non-CloudFormation resources "
                "is unverified."
            ),
        }
    else:
        coverage_verdict = preflight.assess_coverage(ingest_result.discovery_paths, re_status)

    rows = build_rows(ingest_result, snapshot)
    return report.render_report(
        rows, ingest_result.discovery_paths, coverage_verdict, snapshot, source_line
    )


def _describe_source(topology_source: str | None) -> str:
    """One-line human description of which topology source run() used.

    Mirrors the resolver's own log line so the report footer states whether the
    topology came from a local file or the live DevOps Agent MCP pull.
    """
    if topology_source and topology_source.strip().lower() != topology_source_module.LIVE:
        return f"local file {topology_source}"
    return "live (DevOps Agent MCP)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HCLS stack readiness check (factual, not compliance).")
    parser.add_argument(
        "--topology",
        dest="topology",
        default=None,
        help=(
            "Topology source: a path to a local DevOps Agent topology .json "
            "export (for testing/offline), or 'live' to pull from the DevOps "
            "Agent MCP endpoint. Defaults to live."
        ),
    )
    parser.add_argument(
        "topology_positional",
        nargs="?",
        default=None,
        help="Backward-compat: a bare path is treated as a local .json export.",
    )
    args = parser.parse_args(argv)
    # --topology wins; otherwise a bare positional path is used; otherwise live.
    source = args.topology if args.topology is not None else args.topology_positional
    try:
        print(run(source))
    except topology_source_module.TopologySourceError as exc:
        # Fail-closed is a valid, correct outcome (e.g. no Agent Space, or the
        # SDK lacks the devops-agent client). Print the actionable message
        # cleanly (no traceback) and exit nonzero.
        print(f"topology source error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
