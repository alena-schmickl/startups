---
name: hcls-stack-check
description: >-
  Reads an AWS DevOps Agent topology (live pull or local export) and reports two
  facts per resource: whether its service is on AWS's published HIPAA Eligible
  Services Reference list, and whether it sits in an EU region / whether the
  service offers an EU region. Factual membership + region check only — NOT a
  compliance assessment and NOT legal advice. Use when someone asks about HIPAA
  eligibility, EU data residency, HCLS readiness, ghost instances, or "what is
  running in my account and where."
license: Apache-2.0
compatibility: "Kiro CLI, Claude Code, Quick Desktop"
trigger: "HIPAA eligible, HIPAA eligibility, EU data residency, EU region check, HCLS readiness, stack readiness, check my stack, ghost instances, what is running in my account, is my stack in the EU"
tools: [run_python]
metadata:
  author: alschmic
  version: "1.0.0"
  last_validated: 2026-09-28
  risk_tier: L2
  audience: startup
---

# Skill: hcls-stack-check

## Overview

Produce a factual HCLS readiness snapshot of an AWS account's inventory. For
each resource discovered in the AWS DevOps Agent topology, report:

1. **HIPAA eligibility** — is the resource's service on AWS's published HIPAA
   Eligible Services Reference list (yes / yes-with-caveat / not-on-list)?
2. **EU residency** — is the resource in an EU region, and does the service even
   offer an EU region (yes / no / unknown)?

This is a **factual membership + region check only. It is NOT a compliance
assessment and NOT legal advice** — it reports list membership and region facts,
never a "HIPAA-compliant" or "GDPR-ready" verdict.

**Why one skill, not two:** the HIPAA and EU checks share the same trigger
vocabulary ("HCLS readiness", "check my stack"), the same input (one topology),
and the same output (one table). They are one readiness question asked two ways,
so they are consolidated per the >70%-shared-trigger rule rather than split.

### When to use

Use when a startup SA references a DevOps Agent topology, HIPAA eligibility, EU
data residency, or healthcare/life-sciences readiness — e.g. "which resources
run on non-HIPAA-eligible services?", "is everything in my account in the EU?",
"any ghost instances not deployed via IaC?".

### When NOT to use

- Do **not** produce a compliance verdict ("HIPAA-compliant", "GDPR-ready"),
  because this skill only checks list membership and region facts.
- Do **not** give legal advice.

## Responsible AI disclosure

This is an **assessment-support** skill. It surfaces published facts (list
membership, region) to inform a human's judgment; it does not make eligibility,
compliance, or legal determinations. A qualified human must interpret the
report. Every generated report carries the verbatim AWS HIPAA disclaimer and the
not-a-compliance boundary statement, and both MUST be shown to the user.

## External references (inventory)

The skill reads these external sources at runtime. All access is read-only.

| Reference | Purpose | Access |
|---|---|---|
| AWS HIPAA Eligible Services Reference (`https://aws.amazon.com/compliance/hipaa-eligible-services-reference/`) | Source of truth for eligibility | HTTPS GET, host-validated, live every run |
| AWS DevOps Agent (`aidevops`: ListAgentSpaces, ListAssociations) | Locate the associated account + assumable role | Read-only API |
| AWS Resource Explorer (`resource-explorer-2`: Search, GetIndex) | Enumerate resources (CFN + out-of-IaC) | Read-only API |
| AWS SSM global-infrastructure public parameters (`ssm:GetParametersByPath`) | Which regions a service offers | Read-only, public params |
| AWS STS (`sts:AssumeRole`) | Assume the DevOps Agent account's role | Read-only downstream use only |

**Risk tier L2 rationale (OWASP AST04):** the skill performs a cross-account
`sts:AssumeRole` into the DevOps-Agent-associated account. Everything downstream
is strictly read-only (no writes, no mutations, never triggers an
investigation), and no credentials are hardcoded (default chain or `AWS_PROFILE`)
— but because it assumes an externally-provided role, it is L2, not L1.

## The rules this skill enforces

These invariants are implemented in the deterministic core (`src/`) and stated
here so the behavior is auditable. Follow them; do not re-implement them by hand.

- **Rule 1 — Fail closed on the topology source.** Live pull from the DevOps
  Agent by default, or a local `.json` export. If the source is unreachable or
  misconfigured, stop with an actionable error — never emit a partial inventory
  that could read as "all clear."
- **Rule 2 — HIPAA list is live-only and gated.** Fetch the reference page live
  every run (no cache, no bundled fallback). Trust the parse only if ≥ 50
  services parsed AND sentinels S3/EC2/RDS present; otherwise fail loudly.
- **Rule 3 — Membership matching is exact and caveat-aware.** Normalized,
  case-insensitive name match (strip `Amazon `/`AWS `; index parenthesized short
  codes). Verdicts: `yes` / `yes-with-caveat` / `not-on-list`.
- **Rule 4 — Region facts; EU availability may be unknown.** Pure EU-region
  classification; live SSM lookup for whether a service offers an EU region.
  Report `unknown` rather than a false yes/no.
- **Rule 5 — Make coverage gaps visible.** Resource Explorer preflight →
  OK / HIGH / INFO. If RE status can't be determined, report coverage as
  unverified, never a false all-clear.

## Workflow

Run the deterministic core; it wires source → ingest → HIPAA snapshot →
preflight → per-resource checks → report.

### 1. Resolve and load the topology

- **Mode:** code
- **Tool:** `run_python` — `python src/main.py [--topology <live|path>]` (default: live)
- **Input:** optional `--topology` (a local `.json` export path, or `live`)
- **Output:** normalized resources `{resource_id, service, region}` + active discovery paths
- **Validate:** at least the source resolved without error; discovery paths recorded
- **On failure:** the run stops and prints an actionable message, e.g. "Can't
  reach the DevOps Agent live topology (no Agent Space configured, or the SDK
  lacks the devops-agent client). Run against a local export with
  `--topology <path>`, or upgrade boto3." Do not proceed with a partial stack.

### 2. Fetch + validate the HIPAA list

- **Mode:** code
- **Tool:** `run_python` (same run)
- **Input:** none (live fetch); optional dev override via `HCLS_SNAPSHOT_FILE`
- **Output:** validated list of `{name, caveat}` services
- **Validate:** ≥ 50 services AND S3/EC2/RDS present (the gate)
- **On failure:** stop and report "Could not retrieve/validate the live HIPAA
  Eligible Services list; not reporting against a partial list." Never fall back
  to stale or empty data.

### 3. Check each resource and render the report

- **Mode:** code
- **Tool:** `run_python` (same run)
- **Input:** normalized resources + validated HIPAA list + RE preflight status
- **Output:** the markdown report (see Output)
- **Validate:** every resource has a HIPAA verdict and an EU value; coverage
  badge present; disclaimer + boundary present
- **On failure:** if no resources resolved, still render the report with a
  "(no resources with a resolvable service)" row and the coverage note, so the
  empty state is a reported state, not a crash.

## Output

A single markdown report, in this order:

1. Title
2. **AWS HIPAA disclaimer (verbatim)** — required
3. **Not-a-compliance boundary statement** — required
4. HIPAA list source line (live, fetch timestamp, service count)
5. Discovery-coverage badge — 🟢 OK / 🟡 INFO / 🔴 HIGH (color paired with the
   text label, so it is never color-only)
6. Results table: `resource_id | service | hipaa_eligible | region | eu`
   (`hipaa_eligible` is `yes`, `yes* (<caveat>)`, or `not on list`)
7. Footer: topology source (live vs local) + active discovery paths

**Display the report verbatim.** The orchestrator MUST surface the report text
as produced and MUST NOT summarize, re-rank, or re-interpret it, because
paraphrasing a factual eligibility/region report can reintroduce exactly the
compliance-verdict framing this skill forbids.

## Lessons Learned

### Do
- Keep deterministic work (parsing, matching, region/threshold logic) in code;
  the LLM is for framing and answering follow-up questions about the report.
- Fail closed and say what the user does next, in plain language.
- Preserve the three-state outcomes (`yes-with-caveat`, `unknown`, coverage
  `INFO`) — they carry real information a binary would destroy.

### Don't
- Don't turn the report into a compliance verdict or legal advice.
- Don't cache or hardcode the HIPAA list; it must be live + gated every run.
- Don't report "all clear" when coverage is unverified (RE status unknown).

### Common failures
- **`devops-agent` client missing** from an older boto3 → live pull blocked.
  Fix: upgrade boto3, or use `--topology <path>`.
- **No Resource Explorer aggregator index** in the target account → HIGH
  coverage warning (out-of-IaC resources invisible). Fix: enable RE.
- **HIPAA page layout change** → validation gate fails and the run stops (by
  design) rather than under-reporting.

### When to ask the user
- When neither a live pull nor a local export path is available, ask which the
  user wants rather than guessing.
- When the account has multiple DevOps Agent associations, confirm scope if the
  enumerated inventory looks unexpectedly large or empty.

## Runtime notes

- AWS calls use the default boto3 credential chain, or `AWS_PROFILE` if set.
  Nothing is hardcoded. All downstream use of the assumed role is read-only.
- For offline/dev runs, an opt-in local HIPAA snapshot via `HCLS_SNAPSHOT_FILE`
  is validated through the same gate. No data ships in the repo.

## Evaluation cases

1. **Happy path (local export):** `--topology tests/fixtures/sample_topology.json`
   → report renders with a HIPAA verdict + EU value for every resolvable
   resource, disclaimer and boundary present. Expected: exit 0.
2. **Caveat match:** a resource on a service listed with a caveat → row shows
   `yes* (<caveat>)`, not a bare `yes`.
3. **Fail-closed source:** live pull with no Agent Space / no `devops-agent`
   client → `TopologySourceError`, actionable message, non-zero exit, no partial
   report emitted.
4. **Coverage gap (edge):** topology with only CloudFormation discovery and RE
   disabled → report includes the 🔴 HIGH coverage warning.
5. **HIPAA gate failure (edge):** a too-small/garbled list (see
   `tests/fixtures/hipaa_page_too_small.html`) → run stops with a validation
   error rather than reporting against a partial list.
