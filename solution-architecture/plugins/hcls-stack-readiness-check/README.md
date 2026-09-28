# hcls-stack-check

A factual HCLS (healthcare / life-sciences) readiness checker for a startup's
AWS stack. It reads an AWS DevOps Agent topology (pulled live by default, or
from a local export) and produces a readiness report answering two questions:

1. **HIPAA eligibility** — which resources run on services that are on AWS's
   published [HIPAA Eligible Services Reference][hipaa-ref] list.
2. **EU data residency** — which resources sit in an EU region, and whether each
   service even offers an EU region.

> **This is a factual membership + region check only. It is NOT a compliance
> assessment and NOT legal advice.** It reports list membership and region
> facts; it does not decide whether a workload is "HIPAA-compliant" or
> "GDPR-ready."

## Design: playbook + deterministic core

This is a skill (`SKILL.md`) plus a small deterministic Python core (`src/`).
The split is deliberate:

- **`SKILL.md` owns the contract** — when to run, the fail-closed policy, the
  HIPAA-list validation gate, the matching and region rules, and the required
  output boundary and disclaimer.
- **`src/` owns enforcement** — the correctness-critical steps where "described
  correctly" is not the same as "done correctly": live HTML parsing of the
  HIPAA list behind a validation gate, exact caveat-aware name matching across
  every resource, live region lookups, and fail-closed sourcing. A prose rule is
  a hope of compliance; the code's `raise` is a guarantee.

See [`SKILL.md`](./SKILL.md) for the full rule set (Rules 1–5) and workflow.

## Key guarantees

- **Fails closed.** If the topology source is unreachable or the live HIPAA list
  can't be parsed/validated, the run **errors out** rather than returning a
  partial or empty result that could read as "all clear."
- **Live HIPAA list, no cache.** The list is fetched and parsed on every run,
  behind a validation gate (≥ 50 services parsed and sentinels S3/EC2/RDS
  present).
- **Full-coverage discovery.** Topology discovers resources via both
  CloudFormation stacks and AWS Resource Explorer, capturing out-of-IaC
  ("ghost") resources; a preflight surfaces coverage gaps instead of implying
  completeness.

## Requirements

- Python 3.11+
- `boto3` (only needed for the live runtime paths: SSM region lookups and the
  Resource Explorer preflight)
- AWS credentials via the standard default chain. To target a named profile,
  set `AWS_PROFILE`. Nothing is hardcoded.

## Usage

```bash
# Default: LIVE pull of the current topology from the AWS DevOps Agent.
python src/main.py

# Explicitly request the live pull.
python src/main.py --topology live

# Point at a local topology export for testing or offline use.
python src/main.py --topology tests/fixtures/sample_topology.json

# Backward-compat: a bare positional path is treated as a local export.
python src/main.py tests/fixtures/sample_topology.json
```

For offline/dev runs without network access, an opt-in local HIPAA snapshot can
be supplied via the `HCLS_SNAPSHOT_FILE` env var; the same validation gate still
applies. No data ships in the repo.

## Tests

```bash
pytest
```

Tests are offline: the live HIPAA fetch and AWS SDK calls are exercised against
fixtures in `tests/fixtures/`, so no network or credentials are required.

## Structure

```
hcls-stack-check/
├── SKILL.md                     # the skill contract: rules, workflow, boundary, disclaimer
├── README.md
├── pytest.ini
├── src/
│   ├── main.py                  # entry point: source -> ingest -> checks -> report
│   ├── topology_source.py       # WHERE topology comes from (live pull, fail-closed; or local export)
│   ├── topology_ingest.py       # normalize topology -> {resource_id, service, region}
│   ├── hipaa_source.py          # live fetch + parse of the HIPAA list, behind a validation gate
│   ├── hipaa_check.py           # normalized, caveat-aware membership check
│   ├── region_lookup.py         # EU classification + live SSM EU-availability lookup
│   ├── preflight.py             # Resource Explorer coverage assessment
│   └── report.py                # markdown report + verbatim AWS HIPAA disclaimer
└── tests/                       # offline tests + fixtures
```

## License

Apache-2.0.

[hipaa-ref]: https://aws.amazon.com/compliance/hipaa-eligible-services-reference/
