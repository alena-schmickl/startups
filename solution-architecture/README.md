# Solution Architecture

Startup-specific plugins and tools from the AWS Startups Solution Architecture team.

## Plugins

- **[`aws-startups-solution-architecture`](plugins/aws-startups-solution-architecture/)**. Technical AWS solutions for the problems startups get stuck on: multi-tenant SaaS isolation enforced in IAM rather than application code, and running a judgment agent such as an LLM reviewer inside a CI path. Draws all general-purpose AWS service depth from [Agent Toolkit for AWS](https://github.com/aws/agent-toolkit-for-aws) as an upstream dependency rather than restating it.

  Currently two exemplar skills, deliberately. The plugin is scaffolding for Startup SAs to contribute the technical patterns they solve repeatedly; see [what to contribute](plugins/aws-startups-solution-architecture/README.md#what-to-contribute) for the verified gap list.

- **[`hcls-stack-readiness-check`](plugins/hcls-stack-readiness-check/)**. A factual HCLS readiness check for a startup's AWS stack. Reads an AWS DevOps Agent topology (live pull or local export) and reports, per resource, whether its service is on AWS's published HIPAA Eligible Services Reference list and whether it sits in an EU region. A factual membership + region check only, not a compliance assessment and not legal advice. Addresses the "compliance groundwork (HIPAA), scoped to technical controls" gap called out below; it is a `SKILL.md` plus a small deterministic Python core that keeps the correctness-critical steps (live list parsing behind a validation gate, exact matching, fail-closed sourcing) out of the model.

## Where aws-dev-toolkit went

`aws-dev-toolkit` was removed. Its skills and agents were overwhelmingly general-purpose AWS engineering guidance, which Agent Toolkit for AWS now owns, and that overlap is why it was deprecated. Nothing was ported.

- **Startup-specific guidance:** install AWS Startup Advisor with `/plugin install aws-startup-advisor@claude-plugins-official`, or see [`advisor/`](../advisor/).
- **General-purpose AWS guidance:** use Agent Toolkit for AWS with `aws configure agent-toolkit` (requires AWS CLI 2.35+).

Existing `aws-dev-toolkit` installs continue to function but receive no updates.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the content gate that applies to this folder, and the [root CONTRIBUTING guide](../CONTRIBUTING.md) for the RFC process, code of conduct, and licensing.

Contributions here must pass all three criteria: startup-specific rather than general-purpose AWS guidance, no overlap with Agent Toolkit for AWS, and no recommendation of a deprecated or sunset service. The mechanical gate checks frontmatter, naming, and pointers, and reports sunset-service mentions as notes; all three criteria themselves are decided by human and agent review.

## License

Apache-2.0
