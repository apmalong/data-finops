# ADR 0015: FOCUS billing lines and export-snapshot revisions

Status: Accepted (design, 2026-11-01). Refines ADR 0003 and ADR 0014; FOCUS 1.2 pinned (`billing.aws_data_exports.FOCUS_VERSION`, #12).

## Context
ADR 0014 adopts FOCUS for canonical billing, but the original `BillingLine` contract used its own field names, a single amount with a `cost_basis` selector, and credits as fields on a line. FOCUS instead carries each cost column (for example BilledCost and EffectiveCost) on the same row, represents credits and adjustments as separate rows by charge category, and defines no stable row identifier (confirmed for FOCUS 1.2: a line is identified by its Export Snapshot and position). Provider exports restate a billing period by republishing it, so row-level "source version" deduplication has nothing reliable to key on.

## Decision
1. The internal billing record is a FOCUS row for one pinned FOCUS release. Keep FOCUS column names and every cost column; keep provider extension columns under their FOCUS `x_` names; never move them into an untyped metadata object.
2. Credits, refunds, discounts and other non-usage charges are their own Billing Lines (Adjustments), identified by charge category.
3. The revision unit is the Export Snapshot: all lines for one (billing account, Billing Period, source) as published at one time. A restatement replaces the whole snapshot; prior snapshots are retained. (Row-level deduplication was originally allowed within a snapshot; removed by ADR 0019. Which snapshot is current: ADR 0020.)
4. The first source is AWS's native FOCUS export (Data Exports), pinned to the FOCUS release AWS emits. CUR 2.0 conversion through `focus_converters` is deferred.
5. Each cost column reconciles independently. The default Ledger view is BilledCost (matches the invoice, per ADR 0003); EffectiveCost is a selectable view.

## Consequences
The data contract and `billing-line.schema.json` are replaced by the pinned FOCUS schema plus documented extensions at M1. Correction rows that a provider places in a later period are ordinary lines in that period, not edits to earlier ones. Ingestion must know snapshot boundaries, which the export layout provides (confirmed for AWS Data Exports: one execution folder per Delivery, #12).

## Alternatives rejected
- Own billing schema with a FOCUS mapping: duplicates a standard ADR 0014 already adopted.
- Row-level versioning by provider line-item ID: depends on identifiers that are not documented as stable across restatements.
- CUR 2.0 first: needs a conversion layer before any value is delivered.

## Revisit triggers
AWS stops emitting a FOCUS release we support; a FOCUS release adds a stable row identifier; a customer needs a non-FOCUS source first.

## Note (drift review #207, 2026-11-07)
The consequence "replaced by the pinned FOCUS schema plus documented extensions" was met code-first instead (OQ-15): `billing-line.schema.json` is generated from the `FocusBillingLine` model of FOCUS 1.2's columns plus `data_finops_raw_values`. Full FOCUS 1.2 conformance is still pending (`docs/research/standards-conformance-matrix.md`).
