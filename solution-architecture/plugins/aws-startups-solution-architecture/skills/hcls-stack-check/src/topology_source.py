"""Topology SOURCE layer: resolve WHERE the topology graph comes from.

This module sits in front of topology_ingest.normalize(). It answers "where do
I get the raw topology dict from" and returns that dict unchanged for
normalize() to consume. Two sources are supported:

  Mode A, LOCAL FILE (dev/testing/offline): a path to an existing .json file is
  read and json.load()'d. No env-var hack is needed, just pass the path.

  Mode B, LIVE PULL (default): fetch the current topology from the AWS DevOps
  Agent MCP endpoint at runtime using the default AWS credential chain (or
  AWS_PROFILE if set, the same pattern used by region_lookup.py and
  preflight.py). This assembles/receives the topology graph and hands the
  resulting dict to normalize().

Fail-closed philosophy: if the live pull cannot be completed (endpoint/space
not configured, auth failure, call error), we raise TopologySourceError with an
actionable message rather than returning empty, partial, or fabricated data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Optional named profile. When unset, boto3's default credential chain is used
# (env vars, shared config, SSO, instance role, etc.). No profile is hardcoded,
# same pattern as region_lookup.py / preflight.py.
AWS_PROFILE = os.environ.get("AWS_PROFILE")

# Sentinel meaning "live pull" when passed as the source string.
LIVE = "live"

# DevOps Agent MCP access docs (endpoint + auth wiring reference).
DEVOPS_AGENT_MCP_DOCS = (
    "https://docs.aws.amazon.com/devopsagent/latest/userguide/"
    "working-with-devops-agent-accessing-devops-agent-index.html"
)


class TopologySourceError(RuntimeError):
    """Raised when the topology cannot be sourced (fail-closed).

    Carries an actionable message so the skill fails loudly rather than
    silently under-reporting or fabricating a topology.
    """


def _looks_like_local_file(source: Optional[str]) -> bool:
    """True when `source` points at an existing .json file on disk."""
    if not source:
        return False
    if source.strip().lower() == LIVE:
        return False
    p = Path(source)
    return p.suffix.lower() == ".json" and p.is_file()


def _make_session():
    """Build a boto3 Session honoring AWS_PROFILE, else the default chain."""
    import boto3  # imported lazily so offline unit tests need no boto3

    return boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()


# Resource Explorer query that matches every resource the view can see. RE
# requires a non-empty query string; "*" matches all indexed resources.
_RE_MATCH_ALL_QUERY = "*"


def _aws_account_associations(associations: list[dict]) -> list[dict]:
    """Extract AWS-account associations (accountId + assumable role) from the
    DevOps Agent ListAssociations output.

    VERIFIED shape (installed SDK service model):
        associations[].configuration.sourceAws | .aws -> {accountId,
        assumableRoleArn, externalId?, ...}

    Returns a list of {"account_id", "assumable_role_arn", "external_id"} dicts
    for every association that carries an AWS-account block with both an account
    id and an assumable role. Associations for GitHub/Slack/Datadog/etc. (which
    carry no AWS account block) are ignored.
    """
    out: list[dict] = []
    for assoc in associations:
        if not isinstance(assoc, dict):
            continue
        config = assoc.get("configuration") or {}
        # Either key can carry the AWS account block depending on how the
        # association was created; prefer sourceAws, fall back to aws.
        aws_block = config.get("sourceAws") or config.get("aws") or {}
        account_id = (aws_block.get("accountId") or "").strip()
        role_arn = (aws_block.get("assumableRoleArn") or "").strip()
        if account_id and role_arn:
            out.append(
                {
                    "account_id": account_id,
                    "assumable_role_arn": role_arn,
                    "external_id": (aws_block.get("externalId") or "").strip() or None,
                }
            )
    return out


def _assume_role_session(session, role_arn: str, external_id: Optional[str], region: Optional[str]):
    """Assume `role_arn` via STS and return a boto3 Session for the target
    account. Read-only downstream use only. Fail-closed on any STS error."""
    from botocore.exceptions import BotoCoreError, ClientError

    import boto3

    sts = session.client("sts")
    kwargs: dict[str, Any] = {
        "RoleArn": role_arn,
        "RoleSessionName": "hcls-stack-readiness-check",
    }
    if external_id:
        kwargs["ExternalId"] = external_id
    try:
        creds = sts.assume_role(**kwargs)["Credentials"]
    except (BotoCoreError, ClientError) as exc:
        raise TopologySourceError(
            "Could not assume the DevOps Agent account role for a read-only "
            "inventory pull. Check that your current principal is allowed to "
            "sts:AssumeRole into it (and the ExternalId if required), or run "
            f"against a local export with --topology <path>. (details: role "
            f"{role_arn}: {exc})"
        ) from exc

    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def _resource_explorer_resources(target_session, region: Optional[str]) -> list[dict]:
    """Enumerate resources in the target account via AWS Resource Explorer.

    VERIFIED shape (installed SDK): resource-explorer-2 Search returns
        Resources[] -> {Arn, OwningAccountId, Region, ResourceType, Service,
                        CfnResourceType, ...}

    Resource Explorer aggregates across regions when an aggregator index exists,
    so a single Search enumerates the account's resources with their real
    per-resource Region and Service. Returns a list of normalize()-ready dicts:
        {"resource_id", "service", "region", "resource_type", "cfn_managed"}
    Fail-closed on any RE error (including no index configured).
    """
    from botocore.exceptions import BotoCoreError, ClientError

    re_client = target_session.client("resource-explorer-2", region_name=region)
    resources: list[dict] = []
    try:
        paginator = re_client.get_paginator("search")
        for page in paginator.paginate(QueryString=_RE_MATCH_ALL_QUERY):
            for item in page.get("Resources", []) or []:
                arn = (item.get("Arn") or "").strip()
                if not arn:
                    continue
                # Prefer the RE "Service" short code (e.g. "s3"); ingest maps it
                # to a HIPAA display name. Fall back to the CFN type namespace.
                service_code = (item.get("Service") or "").strip()
                cfn_type = (item.get("CfnResourceType") or "").strip()
                resources.append(
                    {
                        "resource_id": arn,
                        # ingest resolves "service"/"resource_type" to a display
                        # name; we hand it both the RE service code and the CFN
                        # type so its existing resolver logic applies unchanged.
                        "service": service_code,
                        "resource_type": cfn_type,
                        "region": (item.get("Region") or "").strip(),
                        # A resource with a CfnResourceType is CloudFormation
                        # managed; otherwise it is an out-of-IaC / RE-only find.
                        "cfn_managed": bool(cfn_type),
                    }
                )
    except (BotoCoreError, ClientError) as exc:
        raise TopologySourceError(
            "Could not enumerate resources via AWS Resource Explorer in the "
            f"DevOps Agent account: {exc}. Ensure Resource Explorer is enabled "
            "(an aggregator index) in the target account, or run against a "
            "local export with --topology <path>."
        ) from exc

    return resources


def fetch_live_topology(region: Optional[str] = None) -> dict[str, Any]:
    """Derive the current, resource-level topology live from the AWS DevOps
    Agent account, fail-closed.

    Verified reality (from the installed SDK service model, not assumption): the
    AWS DevOps Agent has NO single GetTopology API and its ListAssociations
    output describes SERVICE CONNECTIONS to an Agent Space, not a resource
    inventory. The AWS-account association carries an `accountId` and an
    `assumableRoleArn`, a pointer to the account. The resource-level topology is
    therefore DERIVED: we assume that role and enumerate the account's resources
    via AWS Resource Explorer (which covers both CloudFormation-managed and
    out-of-IaC resources, with each resource's real Service and Region).

    Pipeline (all read-only; never triggers an investigation or mutates state):

      1. Build the `devops-agent` client using the default AWS credential chain
         (or AWS_PROFILE). If the installed SDK does not ship that service
         (UnknownServiceError), fail closed: the client cannot be created, so
         the live pull is impossible and we do not fabricate a topology.
      2. ListAgentSpaces -> require at least one configured Agent Space.
      3. ListAssociations per space -> find AWS-account association(s) and their
         accountId + assumableRoleArn.
      4. sts:AssumeRole into the target account.
      5. Resource Explorer Search in the assumed account -> map each resource's
         real Arn, Service, and Region into the dict shape normalize() consumes:
             {"resources": [{"resource_id", "service", "resource_type",
                             "region"}],
              "discovery_paths": [...]}

    Args:
        region: AWS region for the clients. Defaults to the session region.

    Returns:
        The raw topology dict for topology_ingest.normalize().

    Raises:
        TopologySourceError (fail-closed) when: boto3/the devops-agent client is
        unavailable, no Agent Space is configured, no AWS-account association
        exists, the role cannot be assumed, Resource Explorer is unavailable, or
        any call errors. Never returns empty, partial, or fabricated data.
    """
    # 1. Session + devops-agent client via the default credential chain.
    try:
        from botocore.exceptions import (  # lazy import, offline tests need no boto3
            BotoCoreError,
            ClientError,
            UnknownServiceError,
        )

        session = _make_session()
        target_region = region or session.region_name
    except Exception as exc:  # boto3 missing, bad profile, etc.
        raise TopologySourceError(
            "Live topology pull requires a usable AWS session (boto3 with the "
            "default credential chain, or AWS_PROFILE). Could not build one: "
            f"{exc}. Run against a local export with --topology <path> for "
            "testing or offline use."
        ) from exc

    try:
        client = session.client("devops-agent", region_name=target_region)
    except UnknownServiceError as exc:
        # VERIFIED failure mode: an older boto3 does not ship the DevOps Agent
        # (aidevops) client, so there is no operation to call. This is the
        # primary "client not installed" case: it BLOCKS the live pull. Fail
        # closed with a message naming BOTH remedies (install/upgrade the
        # client, OR use a local export). Do not fabricate, do not fall back to
        # the bundled sample.
        detail = str(exc).split(". Valid service names")[0]
        raise TopologySourceError(
            "The AWS DevOps Agent client ('devops-agent', IAM prefix aidevops) "
            "is NOT installed in the current AWS SDK, so the live topology pull "
            "is blocked. To enable the live pull (the preferred path), install "
            "or upgrade the SDK so it ships the devops-agent service, e.g. "
            "`pip install --upgrade 'boto3>=1.43' 'botocore>=1.43'`. "
            "Alternatively, run against a local DevOps Agent topology export "
            "with `--topology <path>`. The bundled sample is for tests only and "
            f"is never used at runtime. Reference: {DEVOPS_AGENT_MCP_DOCS}. "
            f"(underlying error: {detail})"
        ) from exc
    except (BotoCoreError, ClientError) as exc:
        raise TopologySourceError(
            f"Could not create the AWS DevOps Agent client: {exc}. Check your "
            "AWS credentials/region, or run against a local export with "
            "--topology <path>."
        ) from exc

    # 2. List configured Agent Space(s). Read-only, paginated defensively.
    try:
        spaces = _paginate(client, "list_agent_spaces", "agentSpaces")
    except (BotoCoreError, ClientError) as exc:
        raise TopologySourceError(
            f"AWS DevOps Agent ListAgentSpaces failed: {exc}. Check permissions "
            "(aidevops) and that an Agent Space is configured in region "
            f"{target_region}."
        ) from exc

    if not spaces:
        raise TopologySourceError(
            "No AWS DevOps Agent Space is configured in this account/region "
            f"({target_region}). Create and configure an Agent Space first, or "
            "run against a local export with --topology <path>."
        )

    # 3. For each space, list associations and collect AWS-account associations.
    aws_assocs: list[dict] = []
    for space in spaces:
        space_id = (space.get("agentSpaceId") or "").strip()
        if not space_id:
            continue
        try:
            associations = _paginate(
                client, "list_associations", "associations", agentSpaceId=space_id
            )
        except (BotoCoreError, ClientError) as exc:
            raise TopologySourceError(
                f"AWS DevOps Agent ListAssociations failed for space {space_id}: "
                f"{exc}. Check aidevops permissions, or run against a local "
                "export with --topology <path>."
            ) from exc
        aws_assocs.extend(_aws_account_associations(associations))

    if not aws_assocs:
        raise TopologySourceError(
            "An AWS DevOps Agent Space was found, but it has no AWS-account "
            "association (accountId + assumableRoleArn) to derive a resource "
            "topology from. Associate an AWS account with the Agent Space, or "
            "run against a local export with --topology <path>."
        )

    # 4 + 5. Assume the account role and enumerate resources via Resource
    # Explorer. De-duplicate resources by ARN across associations/accounts.
    by_arn: dict[str, dict] = {}
    any_cfn = False
    for assoc in aws_assocs:
        target_session = _assume_role_session(
            session, assoc["assumable_role_arn"], assoc.get("external_id"), target_region
        )
        for res in _resource_explorer_resources(target_session, target_region):
            if res.get("cfn_managed"):
                any_cfn = True
            by_arn[res["resource_id"]] = res

    resources = list(by_arn.values())
    if not resources:
        raise TopologySourceError(
            "The DevOps Agent account(s) were reachable but AWS Resource "
            "Explorer returned no resources. Ensure Resource Explorer has an "
            "aggregator index with resources indexed, or run against a local "
            "export with --topology <path>."
        )

    # Resource Explorer always contributes the resource-explorer discovery path
    # (it is how we enumerated). CloudFormation coverage is asserted only when
    # at least one resource actually carried a CfnResourceType.
    discovery_paths = ["resource-explorer"]
    if any_cfn:
        discovery_paths.insert(0, "cloudformation")

    return {"resources": resources, "discovery_paths": discovery_paths}


def _paginate(client, method_name: str, list_key: str, **kwargs) -> list[dict]:
    """Call a boto3 list_* operation, following nextToken, and return the
    accumulated `list_key` items. Uses the client's paginator when available,
    else a manual nextToken loop. Read-only."""
    items: list[dict] = []
    if client.can_paginate(method_name):
        paginator = client.get_paginator(method_name)
        for page in paginator.paginate(**kwargs):
            items.extend(page.get(list_key, []) or [])
        return items

    # Manual fallback (should not normally be needed).
    next_token: Optional[str] = None
    method = getattr(client, method_name)
    while True:
        call_kwargs = dict(kwargs)
        if next_token:
            call_kwargs["nextToken"] = next_token
        resp = method(**call_kwargs)
        items.extend(resp.get(list_key, []) or [])
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return items


def load_topology(source: Optional[str] = None) -> dict[str, Any]:
    """Resolve the topology source and return the raw topology dict.

    Modes:
      - LOCAL FILE: `source` is a path to an existing .json file. It is read and
        json.load()'d. This is the dev/testing/offline path.
      - LIVE PULL (default): `source` is None or the string "live". The topology
        is fetched from the DevOps Agent MCP endpoint via fetch_live_topology().

    The resolver is explicit about which mode ran: it prints a one-line
    "topology source: ..." note. The returned dict is unchanged, ready for
    topology_ingest.normalize().

    Raises:
        TopologySourceError: on a live-pull failure, or when a non-live source
        string is given that is not an existing .json file (fail-closed).
    """
    if _looks_like_local_file(source):
        path = Path(source)  # type: ignore[arg-type]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                topology = json.load(fh)
        except (OSError, ValueError) as exc:
            raise TopologySourceError(
                f"Could not read local topology file {path}: {exc}"
            ) from exc
        print(f"topology source: local file {path}")
        return topology

    # A non-live, non-file string is a mistake, fail closed rather than
    # silently falling through to a live pull.
    if source and source.strip().lower() != LIVE:
        raise TopologySourceError(
            f"Topology source {source!r} is neither an existing .json file nor "
            f"the literal 'live'. Pass a path to a local export, or 'live' (the "
            f"default) to pull from the DevOps Agent MCP endpoint."
        )

    print("topology source: live (DevOps Agent MCP)")
    return fetch_live_topology()
