# ADR 0029: The public demo is a static snapshot of the read model behind CloudFront

Status: Accepted (design, 2026-11-07). Decides M7's hosted site (Q53, Q60–Q63). Amends ADR 0026 point 3's "the dashboard is used locally" for the demo only, and applies ADR 0028 to a public audience.

## Context
The owner wants anyone to reach the dashboard from the repository or the docs site and see it load with recent data. ADR 0026 put the API behind an `AWS_IAM` Function URL and kept the dashboard local, because a browser cannot sign SigV4. A public live API would put compute on the open internet, with throttling, WAF and cost risk to manage. It would also expose the owner's real bill: the Deployment collects the whole account's FOCUS export.

## Decision
1. **A static snapshot.** After each Collection Run writes the database object, a publisher Lambda renders the read model's answers as JSON files, in exactly the shapes the HTTP API returns. It writes them, with the dashboard's own files, to a private demo bucket served by CloudFront through origin access control. Nothing public computes anything.
2. **The existing dashboard.** The M5 dashboard gains a static mode that reads the snapshot's files instead of calling the API. There is no second front end and no build step. The local and deployed dashboards stay the same code.
3. **Only the demo is public.** The publisher includes only the Athena usage lines whose ResourceId is the demo workgroup, with the Allocations, Roll-ups, lineage and Cost Explanations that come from them. It replaces every account ID with the example account, `123456789012`, and uses the minimal Cost Explanation (ADR 0028). The rest of the owner's bill is never published.
4. **One stack.** The demo is an optional component of the Deployment's Pulumi stack (`publicDemo`, off by default), under the same policy pack. Its bucket keeps all four public access blocks: CloudFront reads it through origin access control.

## Consequences
- **Freshness:** the public site is as fresh as the last Collection Run, and the bill itself trails usage by about a day.
- **Snapshot size:** every answer the dashboard can ask for must be pre-rendered. Filters, pages and per-allocation explanations multiply the files, so the snapshot covers a bounded window: the current and previous Billing Periods.
- **Credits:** the demo's credit lines carry no ResourceId, so they are not published. The page says the amounts shown are before credits.
- **Not a template for customers:** a customer's own Deployment stays private, as ADR 0026 decided.

## Alternatives rejected
- **A live public API through CloudFront:** compute and the real bill on the open internet.
- **A Cognito-gated dashboard:** not public.
- **A React rebuild:** a second front end and a Node toolchain, for the same views.
- **Committing snapshots to the docs site's repository:** a bot commit a day, and the bill's data in git history.

## Revisit triggers
- The snapshot grows past what a static site serves comfortably.
- Readers need filters the pre-rendered files don't cover.
- A customer asks for a hosted dashboard of their own: that would need authentication, not this ADR.
