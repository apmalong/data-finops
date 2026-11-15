# ADR 0015: FOCUS billing lines and export-snapshot revisions

Status: Accepted (design, 2026-11-01). Refines ADR 0003 and ADR 0014. FOCUS 1.2 is pinned (`billing.aws_data_exports.FOCUS_VERSION`, #12).

## Context
ADR 0014 adopts FOCUS for canonical billing. But the original `BillingLine` contract used its own field names, a single amount with a `cost_basis` selector, and credits as fields on a line. FOCUS is different:
- It has each cost column (for example BilledCost and EffectiveCost) on the same row.
- It shows credits and adjustments as separate rows, by charge category.
- It defines no stable row identifier. This is confirmed for FOCUS 1.2: a line is identified by its Export Snapshot and position.

Provider exports restate a billing period: they publish all of the period again. Thus a row-level "source version" deduplication has nothing reliable to use as a key.

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
- CUR 2.0 first: requires a conversion layer before any value is delivered.

## Revisit triggers
- AWS no longer emits a FOCUS release that we support.
- A FOCUS release adds a stable row identifier.
- A customer requires a source that is not FOCUS as the first source.

## Note (drift review #207, 2026-11-07)
The consequence "replaced by the pinned FOCUS schema plus documented extensions" was met code-first instead (OQ-15): `billing-line.schema.json` is generated from the `FocusBillingLine` model of FOCUS 1.2's columns plus `data_finops_raw_values`. Full FOCUS 1.2 conformance is not verified yet (`docs/research/standards-conformance-matrix.md`).
