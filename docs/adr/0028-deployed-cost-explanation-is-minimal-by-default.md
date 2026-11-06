# ADR 0028: A Deployment's Cost Explanation shows a minimal Billing Line by default

Status: Accepted (design, 2026-11-06). Applies ADR 0012 to the Cost Explanation in a Deployment, as #110 asked.

## Context
Locally, the Cost Explanation shows the Billing Line exactly as exported: every FOCUS column, the raw provider values, `Tags` and every `x_` provider column (owner decision, #110). In a Deployment the readers may be a wider group than billing administrators, and ADR 0012 asks for metadata minimization. Tags and free-text names often carry team, customer or project names.

## Decision
1. Two detail levels:
   - **full**: as today.
   - **minimal**: only the columns `FocusBillingLine` declares (the cost columns, accounts, periods, charge category and class, provider, service, SKU, region, `ResourceId`, `SubAccountId`, quantities and units) and `data_finops_raw_values`; `Tags` and every undeclared column (free-text names, `ChargeDescription`, `x_` extensions) are withheld.
2. The rule is the contract's declared fields, not a hand-kept list, so it moves with the contract.
3. The response states `billing_line_detail` (`minimal` or `full`), so a withheld column is never mistaken for an empty one. It is in the OpenAPI document.
4. `data-finops serve` takes `--explanation-detail {full,minimal}`, default `full`. A Deployment's `explanationDetail` setting defaults to `minimal`; `full` is an explicit opt-in.

## Consequences
A deployed explanation cannot show which tag or resource name a line had unless the operator opts in. Allocation, Reconciliation and every amount are unaffected: they use declared columns only.

## Alternatives rejected
- **Full everywhere:** leaks tags and names to every API reader in a Deployment.
- **A configurable column allowlist:** more to get wrong, and drifts from the contract.

## Revisit triggers
Per-reader authorization, so that billing administrators see full detail while others see minimal.
