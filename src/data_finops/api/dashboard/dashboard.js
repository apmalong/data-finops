// data-finops dashboard: the Cost Summary and Roll-ups, with details on demand (spec 06;
// design rules in DESIGN.md). Reads only the JSON API. Amounts arrive as exact decimal
// strings and are shown exactly as sent; numbers are used only to size bars, never to
// display, add or divide money. API strings reach the page only as text, never as HTML.
"use strict";

const SUMMARY = "/v1/costs/summary";
const ROLLUPS = "/v1/rollups";
const ALLOCATIONS = "/v1/allocations";
const EXPLANATION = "/v1/allocations/{ref}/explanation";
const LINEAGE = "/v1/lineage";
const PAGE_SIZE = 1000;
// Run statuses that need a reader: failed or aborted work, and two terminal events that
// disagree (`conflicting`), which is as unresolved as a failure.
const ATTENTION_STATUSES = new Set(["failed", "aborted", "conflicting"]);

// The bars take this share of their track, leaving room for the direct label at each end
// (both ends when a chart has negative amounts).
const BAR_REACH = 0.75;
const MIXED_REACH = 0.5;

const state = { scope: 0, basis: "BilledCost", summaries: [], request: 0, detailRequest: 0 };
const byId = (id) => document.getElementById(id);

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

async function getJson(path, params = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail || `${path} answered ${response.status}`);
  }
  return response.json();
}

/** A glossary value as a label: `metered_estimate` → "metered estimate". */
const asWords = (value) => String(value).replaceAll("_", " ");
const capitalised = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const categoryName = (category) => capitalised(asWords(category));

function coverageText(minimum, maximum = minimum) {
  if (minimum == null) return "";
  return minimum === maximum ? `Coverage ${minimum}` : `Coverage ${minimum}–${maximum}`;
}

/** What makes an amount an estimate or not: its Confidence and Coverage, as words. */
function estimateText(confidence, coverage) {
  return [confidence ? asWords(confidence) : "", coverage].filter(Boolean).join(", ");
}

function scopeParams(scope) {
  return {
    billing_account_id: scope.billing_account_id,
    billing_period: scope.billing_period_start.slice(0, 10),
    source: scope.source,
  };
}

function selectedScopeParams() {
  const summary = state.summaries[state.scope];
  return summary ? scopeParams(summary.scope) : {};
}

const needsAttention = (run) => Boolean(run) && ATTENTION_STATUSES.has(run.status);
const runStatus = (run) => (run ? run.status : "not reported (known only as another run's parent or root)");
const jobName = (run) => (run ? `${run.job_namespace} / ${run.job_name}` : null);
const executionStatus = (model) => (model.execution ? model.execution.status : "not in the run results");

/** A real button that opens a detail; the row it sits in keeps its list or table role. */
function opener(text, open, mono = false) {
  const button = element("button", { type: "button", class: `opener${mono ? " mono" : ""}`, text });
  button.addEventListener("click", () => open().catch(fail));
  return button;
}

// ---- The Cost Summary and Roll-ups --------------------------------------------------------

/** Positive amounts largest first, then negative ones, largest magnitude first. */
function bySize(a, b) {
  if ((a.size < 0) !== (b.size < 0)) return a.size < 0 ? 1 : -1;
  return Math.abs(b.size) - Math.abs(a.size);
}

// One chart: rows on a common zero baseline, sorted by size, each labelled directly. The
// panel states the currency once; a row's label carries its exact amount and its detail.
function barChart(rows) {
  const sized = rows.map((row) => ({ ...row, size: Number(row.amount) })).sort(bySize);
  const highest = Math.max(0, ...sized.map((r) => r.size));
  const lowest = Math.min(0, ...sized.map((r) => r.size));
  const reach = lowest < 0 ? MIXED_REACH : BAR_REACH;
  const range = (highest - lowest || 1) / reach;
  const zero = lowest < 0 ? (1 - reach) * 50 + (-lowest / range) * 100 : 0;
  const list = element("ul", { class: "bars" });
  for (const row of sized) list.append(barRow(row, zero, range));
  return list;
}

function barRow(row, zero, range) {
  const width = (Math.abs(row.size) / range) * 100;
  const negative = row.size < 0;
  const start = negative ? zero - width : zero;
  const bar = element("span", {
    class: `bar${negative ? " negative" : ""}${row.attention ? " attention" : ""}`,
    style: `left:${start}%;width:${width}%`,
  });
  const value = element("span", {
    class: "bar-value",
    text: row.amount,
    style: negative ? `right:${100 - start}%` : `left:${zero + width}%`,
  });
  const track = element("span", { class: "bar-track", "aria-hidden": "true" },
    element("span", { class: "zero", style: `left:${zero}%` }), bar, value);
  const description = `${row.amount}: ${row.name}${row.detail ? `, ${row.detail}` : ""}`;
  const name = row.open
    ? opener(row.name, row.open, row.mono)
    : element("strong", { class: row.mono ? "mono" : "", text: row.name });
  name.setAttribute("aria-label", description);
  const label = element("span", { class: "bar-label" }, name);
  if (row.detail) label.append(element("span", { class: "detail", text: ` · ${row.detail}` }));
  const item = element("li", { class: "bar-row" }, label, track);
  if (!row.open) {
    item.setAttribute("tabindex", "0");
    item.setAttribute("aria-label", description);
  }
  attachTooltip(item, description, row.open ? name : item);
  return item;
}

// Values lead in the tooltip; it repeats the accessible name, so it is hidden from
// assistive technology, which reads that name on focus instead.
function attachTooltip(node, text, focusable) {
  const tip = byId("tooltip");
  const show = (x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.max(0, Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${y + 12}px`;
  };
  node.addEventListener("mousemove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  focusable.addEventListener("focus", () => {
    const box = node.getBoundingClientRect();
    show(box.left, box.bottom);
  });
  focusable.addEventListener("blur", () => (tip.hidden = true));
}

function amountTable(caption, rows, extraRows = []) {
  const body = element("tbody");
  for (const row of [...rows.map((r) => ({ cells: [r.name, r.detail, r.amount] })), ...extraRows]) {
    const tr = element("tr", { class: row.kind || "" });
    row.cells.forEach((cell, i) => tr.append(element("td", { class: i === 2 ? "amount" : "", text: cell || "" })));
    body.append(tr);
  }
  const head = element("tr");
  for (const [name, isAmount] of [["Where", false], ["Detail", false], [caption, true]]) {
    head.append(element("th", { class: isAmount ? "amount" : "", scope: "col", text: name }));
  }
  return element("table", {}, element("thead", {}, head), body);
}

function summaryRows(summary) {
  const currency = summary.currency;
  return [
    ...summary.allocated.map((group) => ({
      name: categoryName(group.category),
      detail: estimateText(group.confidence, coverageText(group.coverage_min, group.coverage_max)),
      amount: group.amount,
      open: () => openAllocations(currency, { category: group.category },
        (a) => (a.confidence ?? null) === (group.confidence ?? null), categoryName(group.category)),
    })),
    ...summary.unallocated.map((u) => ({
      name: "Unallocated", detail: asWords(u.reason), amount: u.amount, attention: true,
      open: () => openAllocations(currency, { category: "unallocated" }, (a) => a.method === u.reason,
        `Unallocated · ${asWords(u.reason)}`),
    })),
    // Adjustments are Unallocated amounts shown apart (OQ-18): always listed, highlighted.
    {
      name: "Adjustments", detail: "credits, refunds, discounts", amount: summary.adjustments, attention: true,
      open: () => openAllocations(currency, { category: "unallocated" }, (a) => a.method === "adjustment", "Adjustments"),
    },
  ];
}

// The Cost Summary of one currency: its total, where it went, its exact amounts.
function currencyPanel(summary) {
  const currency = summary.currency;
  const rows = summaryRows(summary);
  const balanced = Number(summary.reconciliation_delta) === 0;
  const deltaText = balanced
    ? `Reconciliation Delta ${summary.reconciliation_delta}`
    : `Reconciliation Delta ${summary.reconciliation_delta}: the Allocations do not match the bill`;
  const charted = rows.filter((r) => Number(r.amount) !== 0);
  return element("article", { class: "panel" },
    element("div", { class: "headline" },
      element("div", {}, element("h3", { text: `${state.basis} total, ${currency}` }),
        element("p", { class: "hero", text: summary.billed })),
      element("p", { class: `delta${balanced ? "" : " attention"}`, text: deltaText })),
    charted.length ? barChart(charted) : element("p", { class: "meta", text: "Nothing billed." }),
    amountTable(`Amount, ${currency}`, rows, [
      { kind: "total", cells: [`${state.basis} total`, "", summary.billed] },
      { kind: "group", cells: ["Reconciliation Delta", "total − every Allocation", summary.reconciliation_delta] },
    ]));
}

function modelExecutionName(key) {
  const [invocation, node] = JSON.parse(key); // the API's Model Execution key (spec 02)
  return { name: node, detail: `invocation ${invocation}` };
}

function rollupPanel(title, page, describe) {
  const panel = element("article", { class: "panel" }, element("h3", { text: title }));
  const byCurrency = new Map();
  for (const item of page.items) {
    for (const total of item.totals) {
      if (total.cost_basis !== state.basis) continue;
      if (!byCurrency.has(total.currency)) byCurrency.set(total.currency, []);
      byCurrency.get(total.currency).push({ ...describe(item.key), amount: total.amount });
    }
  }
  if (byCurrency.size === 0) {
    panel.append(element("p", { class: "meta", text: "Nothing linked rolls up here." }));
    return panel;
  }
  for (const [currency, rows] of byCurrency) {
    panel.append(element("p", { class: "meta", text: `${state.basis}, ${currency}` }),
      barChart(rows), amountTable(`Amount, ${currency}`, rows));
  }
  if (page.total > page.items.length) {
    panel.append(element("p", { class: "meta", text: `Showing ${page.items.length} of ${page.total}.` }));
  }
  return panel;
}

/** Every Workload Run at the top of its family, so a run with no cost (a failed one, say)
 *  can still be opened. */
function rootRunsPanel(page) {
  const panel = element("article", { class: "panel" }, element("h3", { text: "Workload Runs, from the top" }));
  if (!page.items.length) {
    panel.append(element("p", { class: "meta", text: "No Workload Run reported yet." }));
    return panel;
  }
  panel.append(element("ul", { class: "runs" }, ...page.items.map((r) => runItem(r.run_id, r.run))));
  if (page.total > page.items.length) {
    panel.append(element("p", { class: "meta", text: `Showing ${page.items.length} of ${page.total}.` }));
  }
  return panel;
}

function showNotice(text) {
  const notice = byId("notice");
  notice.hidden = !text;
  notice.textContent = text || "";
}

async function render() {
  const request = ++state.request; // a later change supersedes an earlier, slower request
  const current = () => request === state.request;
  const result = await getJson(SUMMARY, { cost_basis: state.basis });
  if (!current()) return;
  showNotice(result.uncalculated_scopes.length
    ? `${result.uncalculated_scopes.length} Snapshot Scope(s) have an Export Snapshot but no Calculation Run yet; their bill is not shown. Run data-finops calculate.`
    : "");
  const summaries = result.summaries;
  state.summaries = summaries;
  byId("scope").replaceChildren(...summaries.map((s, i) => element("option", {
    value: String(i),
    text: `${s.scope.billing_account_id} · ${s.scope.billing_period_start.slice(0, 7)} · ${s.scope.source}`,
  })));
  if (summaries.length === 0) {
    byId("currencies").replaceChildren(element("p", { class: "meta", text: "No Allocation Snapshot yet: import a FOCUS export and run data-finops calculate." }));
    return;
  }
  state.scope = Math.min(state.scope, summaries.length - 1);
  byId("scope").value = String(state.scope);
  const summary = summaries[state.scope];
  const run = summary.snapshot;
  byId("provenance").textContent =
    `Calculation Run ${run.calculation_run_id} · ${run.created_at.replace("T", " ").replace("Z", " UTC")} · policy ${run.policy_version}${run.is_current ? "" : " · superseded"}`;
  byId("currencies").replaceChildren(...summary.currencies.map(currencyPanel));
  const params = { ...scopeParams(summary.scope), limit: 50 };
  const [models, runs, roots] = await Promise.all([
    getJson(ROLLUPS, { ...params, level: "model_execution" }),
    getJson(ROLLUPS, { ...params, level: "workload_run" }),
    getJson(LINEAGE, { limit: 100 }),
  ]);
  if (!current()) return;
  byId("rollups-model").replaceChildren(rollupPanel("Model Executions", models, modelExecutionName));
  byId("rollups-run").replaceChildren(rollupPanel("Workload Runs", runs,
    (key) => ({ name: key, detail: "", mono: true, open: () => openLineage(key) })));
  byId("runs").replaceChildren(rootRunsPanel(roots));
}

// ---- Details on demand (#118): the Allocations behind an amount, one Allocation's Cost
// Explanation, a Workload Run's lineage. `#explain=<ref>` and `#run=<id>` open them from a
// link; the address follows what is shown. A later opener supersedes an earlier one.

function beginDetail() {
  const request = ++state.detailRequest;
  return () => request === state.detailRequest;
}

function showDetail(title, hash, ...content) {
  history.replaceState(null, "", hash || location.pathname + location.search);
  const section = byId("detail");
  section.hidden = false;
  section.replaceChildren(element("h2", { text: title }), ...content);
  section.scrollIntoView({ block: "start" });
}

function closeDetail() {
  state.detailRequest++;
  byId("detail").hidden = true;
  history.replaceState(null, "", location.pathname + location.search);
}

async function allPages(path, params) {
  const items = [];
  let total = Infinity;
  let first = null;
  while (items.length < total) {
    const page = await getJson(path, { ...params, limit: PAGE_SIZE, offset: items.length });
    first ??= page;
    total = page.total;
    items.push(...page.items);
    if (page.items.length === 0) break;
  }
  return { ...first, items };
}

async function openAllocations(currency, filters, keep, title) {
  const current = beginDetail();
  const page = await allPages(ALLOCATIONS, { ...selectedScopeParams(), ...filters, cost_basis: state.basis });
  if (!current()) return;
  const entries = page.items.filter((entry) => entry.allocation.currency === currency && keep(entry.allocation));
  if (entries.length === 1) {
    await openExplanation(entries[0].ref);
    return;
  }
  const body = element("tbody");
  for (const { ref, allocation } of entries) {
    body.append(element("tr", {},
      element("td", {}, opener(allocation.billing_line_ref, () => openExplanation(ref), true)),
      element("td", { class: "mono", text: allocation.target_id }),
      element("td", { text: asWords(allocation.method) }),
      element("td", { text: estimateText(allocation.confidence, coverageText(allocation.coverage)) }),
      element("td", { class: "amount", text: allocation.amount })));
  }
  const head = element("tr");
  for (const [name, amount] of [["Billing Line", false], ["Allocation Target", false], ["Method", false],
    ["Confidence, Coverage", false], [`Amount, ${currency}`, true]]) {
    head.append(element("th", { scope: "col", class: amount ? "amount" : "", text: name }));
  }
  showDetail(`${title}: its Allocations (${state.basis})`, "",
    element("p", { class: "meta", text: `${entries.length} Allocations in Calculation Run ${page.calculation_run_id}. Choose a Billing Line for its Cost Explanation.` }),
    element("table", {}, element("thead", {}, head), body));
}

function facts(pairs) {
  const list = element("dl", { class: "facts" });
  for (const [name, value, options = {}] of pairs) {
    if (value == null || value === "") continue;
    list.append(element("dt", { text: name }), element("dd", { class: options.mono ? "mono" : "", text: String(value) }));
  }
  return list;
}

function runItem(runId, run) {
  return element("li", { class: needsAttention(run) ? "attention" : "" },
    opener(runId, () => openLineage(runId), true),
    element("span", { class: "detail", text: ` · ${[jobName(run), runStatus(run)].filter(Boolean).join(" · ")}` }));
}

function billingLineSection(explanation) {
  const line = explanation.billing_line;
  const basis = explanation.allocation.cost_basis;
  return [
    element("h3", { text: "1 · Billing Line" }),
    facts([["Reference", explanation.billing_line_ref, { mono: true }], ["Service", line.ServiceName],
      ["Charge", line.ChargeDescription], ["Charge period", `${line.ChargePeriodStart} – ${line.ChargePeriodEnd}`],
      [basis, `${line[basis]} ${line.BillingCurrency}`],
      ["Usage", line.ConsumedQuantity != null ? `${line.ConsumedQuantity} ${line.ConsumedUnit || ""}` : null],
      ["Account, region", [line.SubAccountId, line.RegionId].filter(Boolean).join(", ")]]),
    element("details", {}, element("summary", { text: "Every column as exported" }),
      element("pre", { class: "mono", text: JSON.stringify(line, null, 2) })),
  ];
}

function allocationSection(explanation) {
  const allocation = explanation.allocation;
  const estimate = estimateText(allocation.confidence, coverageText(allocation.coverage));
  return [
    element("h3", { text: "2 · Allocation" }),
    facts([["Amount", `${allocation.amount} ${allocation.currency} (${allocation.cost_basis})${estimate ? ` · ${estimate}` : ""}`],
      ["Allocation Category", categoryName(allocation.category)], ["Method", asWords(allocation.method)],
      ["Unallocated because", explanation.unallocated_reason ? asWords(explanation.unallocated_reason) : null],
      ["Policy version", allocation.policy_version], ["Calculation Run", allocation.calculation_run_id, { mono: true }]]),
  ];
}

function evidenceSection(explanation) {
  const section = [element("h3", { text: "3 · Evidence" })];
  const query = explanation.query_execution;
  const model = explanation.model_execution;
  if (!query && !model && explanation.workload_runs.length === 0) {
    section.push(element("p", { class: "meta", text: "None: nothing explicit names a Query Execution for this amount (ADR 0006)." }));
  }
  if (query) {
    section.push(element("h4", { text: "Query Execution" }), facts([["ID", query.query_execution_id, { mono: true }],
      ["Account, region", `${query.account_id}, ${query.region}`], ["Workgroup", query.workgroup], ["State", query.state],
      ["Scanned bytes", query.scanned_bytes], ["Billable bytes", query.billable_bytes]]));
  }
  if (model) {
    section.push(element("h4", { text: "Model Execution" }), facts([["dbt Node", model.node_id, { mono: true }],
      ["Invocation", model.invocation_id, { mono: true }], ["Status", executionStatus(model)],
      ["Materialized", model.dbt_node ? model.dbt_node.materialized : null]]));
  }
  if (explanation.workload_runs.length) {
    section.push(element("h4", { text: "Workload Runs, nearest first" }),
      element("ul", { class: "runs" }, ...explanation.workload_runs.map((r) => runItem(r.run_id, r.run))));
  }
  return section;
}

function rollupSection(explanation) {
  return [
    element("h3", { text: "4 · Roll-ups it contributes to" }),
    explanation.rollups.length
      ? facts(explanation.rollups.map((r) => [
        r.level === "model_execution" ? `Model Execution ${modelExecutionName(r.key).name}` : `Workload Run ${r.key}`,
        `${r.amount} ${r.currency} (${r.cost_basis})`]))
      : element("p", { class: "meta", text: "None: no Identity Link reaches a Model Execution or Workload Run." }),
  ];
}

async function openExplanation(ref) {
  const current = beginDetail();
  const explanation = await getJson(EXPLANATION.replace("{ref}", encodeURIComponent(ref)));
  if (!current()) return;
  showDetail(`Cost Explanation of ${ref}`, `#explain=${encodeURIComponent(ref)}`,
    ...billingLineSection(explanation), ...allocationSection(explanation),
    ...evidenceSection(explanation), ...rollupSection(explanation));
}

function claimsList(claims) {
  if (!claims.length) return element("p", { class: "meta", text: "No query claims it." });
  return element("ul", { class: "claims" }, ...claims.map((claim) => element("li", {},
    element("span", { class: "mono", text: claim.query_execution.query_execution_id }),
    element("span", { class: "detail", text: ` · Link Outcome ${asWords(claim.outcome)}` }))));
}

function identityLinkSection(lineage) {
  const section = [element("h3", { text: "Identity Links" })];
  if (lineage.invocation) {
    section.push(facts([["dbt invocation", lineage.invocation.invocation_id, { mono: true }],
      ["Link Outcome", asWords(lineage.invocation.outcome)], ["Linked Workload Run", lineage.invocation.linked_run_id, { mono: true }]]));
  }
  if (lineage.model_run_link) {
    section.push(facts([["Identity Link to its Model Execution", `Link Outcome ${asWords(lineage.model_run_link.outcome)}`]]));
  }
  for (const model of lineage.model_executions) {
    section.push(element("h4", { text: model.node_id }), facts([["Status", executionStatus(model)]]), claimsList(model.queries));
  }
  if (lineage.unmatched_queries.length) {
    section.push(element("h4", { text: "Unmatched: queries naming a node this invocation never reported" }),
      claimsList(lineage.unmatched_queries));
  }
  return section;
}

function dataAssetSection(lineage) {
  const section = [element("h3", { text: "Data Assets" })];
  if (!lineage.data_assets.length) return [...section, element("p", { class: "meta", text: "None reported." })];
  const directions = new Map();
  for (const edge of lineage.lineage_edges) {
    const key = `${edge.asset.namespace} / ${edge.asset.name}`;
    directions.set(key, new Set([...(directions.get(key) || []), asWords(edge.direction)]));
  }
  return [...section, facts(lineage.data_assets.map((asset) => {
    const key = `${asset.namespace} / ${asset.name}`;
    return [key, [...(directions.get(key) || [])].join(" and ") || "no Lineage Edge"];
  }))];
}

async function openLineage(runId) {
  const current = beginDetail();
  const lineage = await getJson(LINEAGE, { run_id: runId });
  if (!current()) return;
  const run = lineage.run;
  showDetail(`Lineage of Workload Run ${runId}`, `#run=${encodeURIComponent(runId)}`,
    element("p", { class: `run-status${needsAttention(run) ? " attention" : ""}`, text: `Status: ${runStatus(run)}` }),
    facts([["Job", jobName(run)], ["Started", run ? run.started_at : null], ["Ended", run ? run.ended_at : null]]),
    element("h3", { text: "Above it, nearest first" }),
    lineage.ancestors.length ? element("ul", { class: "runs" }, ...lineage.ancestors.map((a) => runItem(a.run_id, a.run)))
      : element("p", { class: "meta", text: "None: it is at the top of its family." }),
    element("h3", { text: "Below it" }),
    lineage.children.length ? element("ul", { class: "runs" }, ...lineage.children.map((c) => runItem(c.run_id, c)))
      : element("p", { class: "meta", text: "None." }),
    ...identityLinkSection(lineage), ...dataAssetSection(lineage));
}

/** Open what the address names; a malformed one is reported, not thrown away silently. */
async function openFromHash() {
  let fragment;
  try {
    fragment = decodeURIComponent(location.hash.slice(1));
  } catch {
    throw new Error("the link's #explain= or #run= part is not valid");
  }
  const [kind, value] = fragment.split(/=(.*)/s);
  if (kind === "explain" && value) await openExplanation(value);
  else if (kind === "run" && value) await openLineage(value);
}

const fail = (error) => showNotice(`Could not load: ${error.message}`);
function change(update) {
  return (e) => {
    update(e.target.value);
    closeDetail(); // a detail belongs to the scope and Cost Basis it was opened under
    render().catch(fail);
  };
}
byId("basis").addEventListener("change", change((value) => (state.basis = value)));
byId("scope").addEventListener("change", change((value) => (state.scope = Number(value))));
window.addEventListener("hashchange", () => openFromHash().catch(fail));
render().then(openFromHash).catch(fail);
