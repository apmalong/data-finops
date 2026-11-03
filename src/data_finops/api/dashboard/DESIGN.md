# Dashboard design note

The rules from spec 06 (after Edward Tufte, *The Visual Display of Quantitative Information*,
and Stephen Few, *Information Dashboard Design*), each checked against this dashboard. Review
a change against this list and its screenshot in the PR.

- [x] **One screen answers the first question at a glance: where did this period's bill go.** The overview leads with each currency's billed total, then one chart of where it went; the scope and Cost Basis are the only controls. Details come on demand through the Cost Explanation (#118).
- [x] **Maximise data-ink.** No gauges, dials, pie or donut charts, 3-D, gradients, shadows on marks, decorative icons or heavy gridlines. Every mark is an amount; the only rule in a chart is its zero baseline.
- [x] **Tables where exact amounts matter.** Every chart has a table beneath it: right-aligned tabular figures, amounts exactly as the API sends them (the line's own scale), the currency stated once per table caption, light rules only between groups.
- [x] **Horizontal bars on a common zero baseline, sorted by amount, labelled directly.** One baseline per chart (negative Adjustments extend left of it), rows sorted by size, each labelled with its name and exact amount; no legend is needed because each chart is one series.
- [x] **Colour is muted and used only to draw attention.** Allocated amounts take one series hue; Unallocated amounts, Adjustments and a non-zero Reconciliation Delta take the one highlight, always beside the words that say why (never colour alone). Both hues are the dataviz skill's validated reference steps (light `#2a78d6`/`#eb6834`, dark `#3987e5`/`#d95926`: every check passes in both modes, CVD ΔE ≥ 24).
- [x] **Estimates look different from billed facts.** Each allocated amount is labelled with its Allocation Category and Confidence (for example "direct · metered estimate"); Coverage, per Allocation, is shown in the Cost Explanation (#118).
- [x] **Consistent placement and glossary terms only.** Unallocated (never "unknown"), Adjustments, Reconciliation Delta, Roll-up, Calculation Run; the Calculation Run, its time and policy version sit quietly under the title on every view.
- [x] **Small multiples across currencies, scales shared within a currency, never across.** Each currency is its own panel with its own scale; Roll-ups are charted per currency for the chosen Cost Basis.

Also: light and dark modes are each selected (not an automatic flip); `forced-colors` keeps the bars visible; every bar row is keyboard-focusable with an accessible name and a tooltip giving its share of the bill.

Known limit: with a bearer token configured (ADR 0024) the dashboard, like every route, needs the `Authorization` header, which a browser does not send on its own; the token-free loopback demo is where it is used in M5.
