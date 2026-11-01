# Claude Code execution boundaries

Configure permissions in Claude Code and at the cloud IAM level. These instructions alone do not enforce access control.

Allowed without additional approval: inspect repository, modify source/tests/docs, run offline unit/contract tests, format/lint/type-check, create local virtualenv.

Require explicit human approval: any cloud provisioning or destroy, AWS/GCP credential use, IAM changes, enabling network-facing endpoints, copying third-party source, installing unreviewed dependencies, git push, release/publish, deleting files outside project, or accessing production data.

Never supply production cloud credentials during M-1 through M4. Do not run `pulumi up` or `pulumi destroy` without approval. Cloud collectors are read-only; Pulumi deployment identity must be distinct from collector runtime identity.

Use Claude Code's actual permissions and deny/ask settings to enforce these rules. Review commands before approval; do not assume this document is a sandbox.
