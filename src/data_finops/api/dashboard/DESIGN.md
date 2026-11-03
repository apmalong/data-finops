# Dashboard design note

Each rule of spec 06's Dashboard section, quoted as written (after Edward Tufte, *The Visual
Display of Quantitative Information*, and Stephen Few, *Information Dashboard Design*), then
how this dashboard meets it. `tests/integration/test_dashboard.py` fails if a rule is
missing, reworded or unticked. Review a change against this list and the screenshots in
`docs/implementation/screenshots/`.

- [x] One screen answers the first question at a glance: where did this period's bill go. The Cost Summary is the overview; details come on demand through the Cost Explanation.
  The page leads with each currency's total and one chart of where it went; the Snapshot Scope and Cost Basis are the only controls. The Cost Explanation for any amount arrives with #118.
- [x] Maximise data-ink: no gauges, dials, pie or donut charts, 3-D, gradients, shadows, decorative icons or heavy gridlines. Every mark encodes data.
  No shadows anywhere, tooltip included; the only rule in a chart is its zero baseline; the one glyph, ⚠ beside a non-zero Reconciliation Delta, is a status cue, not decoration.
- [x] Tables where exact amounts matter: right-aligned tabular figures at the line's own scale, the currency stated once per column, light rules only between groups.
  Every chart has a table beneath it; amounts are the API's exact decimal strings; the currency is stated once, in the amount column's heading ("Amount, USD"), and once in each panel's heading.
- [x] Horizontal bars on a common zero baseline, sorted by amount, labelled directly instead of through a legend, for comparing parts of the bill (allocated by Confidence, Unallocated by reason, Adjustments, Roll-ups).
  One baseline per chart; positive amounts largest first, then negative ones (Adjustments, left of zero); each bar carries its exact amount and its label; no legend.
- [x] Colour is muted by default and used only to draw attention: one highlight for amounts that need it (Unallocated, a non-zero Reconciliation Delta, failed runs), never as decoration and never as the only cue.
  Allocated amounts take one series hue; Unallocated amounts take the one highlight, and so do Adjustments, which are Unallocated amounts shown apart (OQ-18); a non-zero Reconciliation Delta is said in words beside its ⚠. Both hues are the dataviz skill's validated steps (light `#2a78d6`/`#eb6834`, dark `#3987e5`/`#d95926`; every check passes in both modes). Failed runs appear with lineage (#118).
- [x] Estimates look different from billed facts: Metered Shares are labelled with their Confidence and Coverage beside the amount (acceptance.md), so the reader never mistakes an estimate for an exact cost.
  Each allocated amount's detail names its Confidence and Coverage, for example "Direct · metered estimate, Coverage 1" (a range when lines differ).
- [x] Consistent placement and terms: glossary terms only (Unallocated, never "unknown"); the policy version, Calculation Run and Reconciliation Delta are always visible, quietly.
  The Calculation Run, its time and policy version sit under the title; each panel states its Reconciliation Delta beside its total.
- [x] Small multiples when the same chart repeats across currencies or Cost Bases, with shared scales within a currency, never across currencies.
  Each currency is its own panel with its own scale. Cost Bases are chosen one at a time, so each view shows one; Roll-ups are charted per currency for that Cost Basis.

Also: light and dark each have their own validated steps; the page follows the system setting, and `data-theme` on `<html>` forces one. `forced-colors` keeps bars and the baseline visible. Each bar row is keyboard-focusable with an accessible name that leads with its amount.

Known limit: with a bearer token configured (ADR 0024) the dashboard, like every route, needs the `Authorization` header, which a browser does not send on its own; the token-free loopback demo is where it is used in M5.
