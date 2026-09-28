"""Ingest an AWS DevOps Agent topology JSON export.

Discovery source is the DevOps Agent topology, NOT raw IaC parsing. Topology
discovers resources via BOTH CloudFormation stacks AND AWS Resource Explorer
(the latter captures resources created outside IaC). The export gives per
resource type, region, and relationships.

Docs: https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-what-is-a-devops-agent-topology.html
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Sample fixture shipped with the skill. TESTS ONLY: it must never be loaded on
# the runtime path (the live pull is the preferred source; for offline use pass
# an explicit --topology <path>). Tests reference this constant directly.
SAMPLE_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_topology.json"

# CloudFormation type namespace to a HIPAA-list display name. The HIPAA list is
# matched by normalized display name, so we resolve a human name here. Extend as
# needed; unknown namespaces fall through to a best-effort title-cased guess.
_CFN_NAMESPACE_TO_SERVICE = {
    "AWS::S3": "Amazon S3",
    "AWS::EC2": "Amazon EC2",
    "AWS::RDS": "Amazon RDS",
    "AWS::Lambda": "AWS Lambda",
    "AWS::SQS": "Amazon SQS",
    "AWS::SNS": "Amazon SNS",
    "AWS::DynamoDB": "Amazon DynamoDB",
    "AWS::Transcribe": "AWS Transcribe",
    "AWS::ComprehendMedical": "Amazon Comprehend Medical",
    "AWS::HealthLake": "Amazon HealthLake",
}

# service-slug (e.g. "amazon-s3") to display name, for exports that carry a
# lowercase service key instead of, or in addition to, a CFN type.
_SLUG_TO_SERVICE = {
    "amazon-s3": "Amazon S3",
    "amazon-ec2": "Amazon EC2",
    "amazon-rds": "Amazon RDS",
    "aws-lambda": "AWS Lambda",
    "amazon-sqs": "Amazon SQS",
    "amazon-sns": "Amazon SNS",
    "amazon-dynamodb": "Amazon DynamoDB",
    "aws-transcribe": "AWS Transcribe",
    "amazon-comprehend-medical": "Amazon Comprehend Medical",
    "amazon-healthlake": "Amazon HealthLake",
}

# AWS Resource Explorer short service codes (e.g. "s3", "ec2") to a HIPAA-list
# display name. RE's Search result carries `Service` as a short code, not an
# "amazon-*" slug, so the live pull needs this map. Extend as needed; unknown
# codes fall through to the CFN-type path or the best-effort title-cased guess.
_RE_SERVICE_CODE_TO_SERVICE = {
    "s3": "Amazon S3",
    "ec2": "Amazon EC2",
    "rds": "Amazon RDS",
    "lambda": "AWS Lambda",
    "sqs": "Amazon SQS",
    "sns": "Amazon SNS",
    "dynamodb": "Amazon DynamoDB",
    "transcribe": "AWS Transcribe",
    "comprehendmedical": "Amazon Comprehend Medical",
    "comprehend-medical": "Amazon Comprehend Medical",
    "healthlake": "Amazon HealthLake",
}

_KNOWN_DISCOVERY_PATHS = ("cloudformation", "resource-explorer")


@dataclass
class NormalizedResource:
    """A single resource, normalized down to what the checks need."""

    resource_id: str
    service: str  # HIPAA-list display name, e.g. "Amazon S3"
    region: str


@dataclass
class TopologyIngestResult:
    resources: list[NormalizedResource] = field(default_factory=list)
    # Which discovery paths the topology export reports as active.
    discovery_paths: list[str] = field(default_factory=list)


def load_topology(path: str | Path) -> dict[str, Any]:
    """Load a topology JSON export from disk into a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_service(resource: dict[str, Any]) -> Optional[str]:
    """Resolve a resource to a HIPAA-list display name, or None if not resolvable.

    Preference order:
      1. Explicit "amazon-*"/"aws-*" service slug (local export style).
      2. AWS Resource Explorer short service code, e.g. "s3" (live pull style).
      3. CloudFormation "resource_type" namespace, e.g. "AWS::S3::Bucket".
      4. Best-effort title-cased slug so unknown but present services are still
         checked (they will simply be NOT_ON_LIST if the live list does not
         contain them).
    """
    slug = (resource.get("service") or "").strip().lower()
    if slug in _SLUG_TO_SERVICE:
        return _SLUG_TO_SERVICE[slug]
    if slug in _RE_SERVICE_CODE_TO_SERVICE:
        return _RE_SERVICE_CODE_TO_SERVICE[slug]

    rtype = (resource.get("resource_type") or resource.get("type") or "").strip()
    if rtype:
        namespace = "::".join(rtype.split("::")[:2])  # e.g. "AWS::S3"
        if namespace in _CFN_NAMESPACE_TO_SERVICE:
            return _CFN_NAMESPACE_TO_SERVICE[namespace]

    # Best-effort fallback from a slug like "amazon-foo-bar" or an RE code.
    if slug:
        return " ".join(part.capitalize() for part in slug.split("-"))

    return None


def _resolve_discovery_paths(topology: dict[str, Any]) -> list[str]:
    """Extract active discovery paths from the export, else empty list."""
    raw = topology.get("discovery_paths") or topology.get("discoveryPaths") or []
    if not isinstance(raw, list):
        return []
    # Keep known paths in a stable order; ignore anything unexpected.
    present = {str(p).strip().lower() for p in raw}
    return [p for p in _KNOWN_DISCOVERY_PATHS if p in present]


def normalize(topology: dict[str, Any]) -> TopologyIngestResult:
    """Parse a topology export into a normalized resource list.

    Tolerant of missing fields: resources with no resolvable service are
    skipped; region is collected where present (empty string when absent).
    Also returns the active discovery paths declared by the export.
    """
    resources: list[NormalizedResource] = []
    for res in topology.get("resources", []) or []:
        if not isinstance(res, dict):
            continue
        service = _resolve_service(res)
        if not service:
            continue  # skip resources with no resolvable service
        resource_id = str(res.get("resource_id") or res.get("id") or res.get("arn") or "").strip()
        if not resource_id:
            continue
        region = str(res.get("region") or "").strip()
        resources.append(NormalizedResource(resource_id=resource_id, service=service, region=region))

    return TopologyIngestResult(
        resources=resources,
        discovery_paths=_resolve_discovery_paths(topology),
    )


def ingest(path: str | Path) -> TopologyIngestResult:
    """Convenience wrapper: load + normalize a topology export from `path`.

    `path` is REQUIRED and has no default. This is deliberate: nothing in the
    runtime path may silently fall back to the bundled sample fixture. The
    sample (SAMPLE_FIXTURE) is for tests only; callers that want it must pass it
    explicitly.
    """
    return normalize(load_topology(path))
