"""Resource Explorer (RE) preflight.

Purpose: make Terraform-heavy customers' discovery COVERAGE GAP visible up front
rather than buried in the report. If Resource Explorer is not active, resources
created outside CloudFormation (Terraform, Pulumi, console) will not be
discovered by the DevOps Agent topology.

assess_coverage is a pure verdict function. check_resource_explorer_enabled
makes a real, read-only resource-explorer-2 get_index call via the default AWS
credential chain (or AWS_PROFILE), and degrades to "undetermined" (None) on
permission/credential errors rather than reporting a false result.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional, TypedDict

# Severity labels for the coverage verdict.
HIGH = "HIGH"
INFO = "INFO"
OK = "OK"


# Optional named profile. When unset, boto3's default credential chain is used.
# No profile is hardcoded so the skill works with any user's standard creds.
AWS_PROFILE = os.environ.get("AWS_PROFILE")


class ReStatus(TypedDict):
    enabled: Optional[bool]  # True/False, or None when it could not be determined
    aggregator_region: Optional[str]
    checked_region: str


class CoverageVerdict(TypedDict):
    severity: str  # HIGH / INFO / OK
    message: str


def check_resource_explorer_enabled(region: str) -> ReStatus:
    """Check whether AWS Resource Explorer has an index in a region.

    Uses boto3 resource-explorer-2 (default credential chain, or AWS_PROFILE
    env var if set) and calls
    get_index() to determine whether an index exists and locate the aggregator
    region.
    AWS calls use the default credential chain, or the AWS_PROFILE env var if set.

    Return shape:
        {"enabled": bool|None, "aggregator_region": str|None,
         "checked_region": region}

    Handling:
      - index exists                 -> enabled=True (aggregator_region set when
                                        the index type is AGGREGATOR)
      - no index / ResourceNotFound  -> enabled=False
      - auth or permission error     -> enabled=None, plus an "error" key so the
                                        caller can surface "could not determine"
                                        rather than a false HIGH.
    """
    result: dict = {"enabled": None, "aggregator_region": None, "checked_region": region}
    try:
        import boto3  # imported lazily so offline unit tests need no boto3
        from botocore.exceptions import ClientError

        session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
        client = session.client("resource-explorer-2", region_name=region)
        try:
            resp = client.get_index()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "NotFoundException"):
                result["enabled"] = False
                return result  # type: ignore[return-value]
            if code in ("AccessDeniedException", "UnauthorizedException", "AccessDenied"):
                result["error"] = f"permission: {code}"
                return result  # type: ignore[return-value]
            result["error"] = code or str(exc)
            return result  # type: ignore[return-value]

        # An index exists in this region.
        result["enabled"] = True
        if (resp.get("Type") or "").upper() == "AGGREGATOR":
            result["aggregator_region"] = region
        return result  # type: ignore[return-value]
    except Exception as exc:  # missing creds, expired token, network, etc.
        result["error"] = str(exc)
        return result  # type: ignore[return-value]


def assess_coverage(discovery_paths: Iterable[str], re_status: ReStatus) -> CoverageVerdict:
    """Assess discovery coverage from active paths + RE status. Pure/implemented.

    - HIGH: resource-explorer NOT among active discovery paths AND RE disabled.
            Non-CloudFormation resources will not be discovered.
    - INFO: RE enabled but no RE resources surfaced (path not active).
    - OK:   resource-explorer path is active.
    """
    paths = set(discovery_paths or [])
    re_active = "resource-explorer" in paths
    enabled = bool(re_status.get("enabled")) if re_status else False

    if re_active:
        return CoverageVerdict(
            severity=OK,
            message="Resource Explorer discovery path is active, coverage includes non-CloudFormation resources.",
        )
    if not enabled:
        return CoverageVerdict(
            severity=HIGH,
            message=(
                "Non-CloudFormation resources (e.g. Terraform, Pulumi, "
                "console-created) will NOT be discovered. Enable AWS Resource "
                "Explorer for full coverage."
            ),
        )
    return CoverageVerdict(
        severity=INFO,
        message=(
            "Resource Explorer is enabled but its discovery path is not active in "
            "this topology; no RE-only resources were surfaced."
        ),
    )
