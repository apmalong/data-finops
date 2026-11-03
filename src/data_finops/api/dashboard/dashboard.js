// data-finops dashboard: the Cost Summary and Roll-ups (spec 06; design rules in DESIGN.md).
// Reads only the JSON API. Amounts arrive as exact decimal strings and are shown exactly as
// sent; numbers are used only to size bars, never to display, add or divide money.
"use strict";

const SUMMARY = "/v1/costs/summary";
const ROLLUPS = "/v1/rollups";
const ALLOCATIONS = "/v1/allocations";
const EXPLANATION = "/v1/allocations/{ref}/explanation";
const LINEAGE = "/v1/lineage";
const ATTENTION_STATUSES = new Set(["failed", "aborted", "conflicting"]);

// The bars take this share of their track, leaving room for the direct label at each end
// (both ends when a chart has negative amounts).
const BAR_REACH = 0.75;
const MIXED_REACH = 0.5;

const state = { scope: 0, basis: "BilledCost", request: 0, summaries: [], deepLinked: false };
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

async function getJson(path, params) {
  const response = await fetch(`${path}?${new URLSearchParams(params)}`);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail || `${path} answered ${response.status}`);
  }
  return response.json();
}

/** A glossary value as a label: `metered_estimate` → "metered estimate". */
const asWords = (value) => String(value).replaceAll("_", " ");
const capitalised = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function coverageText(group) {
  if (group.coverage_min == null) return "";
  return group.coverage_min === group.coverage_max
    ? `Coverage ${group.coverage_min}`
    : `Coverage ${group.coverage_min}–${group.coverage_max}`;
}

function scopeParams(scope) {
  return {
    billing_account_id: scope.billing_account_id,
    billing_period: scope.billing_period_start.slice(0, 10),
    source: scope.source,
  };
}

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
  const label = element("span", { class: "bar-label" },
    element("strong", { class: row.mono ? "mono" : "", text: row.name }));
  if (row.detail) label.append(element("span", { class: "detail", text: ` · ${row.detail}` }));
  const description = `${row.amount}: ${row.name}${row.detail ? `, ${row.detail}` : ""}`;
  const item = element("li", { class: "bar-row", tabindex: "0", "aria-label": description }, label, track);
  attachTooltip(item, description);
  if (row.open) makeOpenable(item, row.open);
  return item;
}

// Values lead in the tooltip; it repeats the row's accessible name, so it is hidden from
// assistive technology, which reads that name on focus instead.
function attachTooltip(node, text) {
  const tip = byId("tooltip");
  const show = (x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.max(0, Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${y + 12}px`;
  };
  node.addEventListener("mousemove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("mouseleave", () => (tip.hidden = true));
  node.addEventListener("focus", () => {
    const box = node.getBoundingClientRect();
    show(box.left, box.bottom);
  });
  node.addEventListener("blur", () => (tip.hidden = true));
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
  return [
    ...summary.allocated.map((group) => ({
      open: () => openAllocations(summary.currency, { category: group.category },
        (a) => (a.confidence ?? null) === (group.confidence ?? null), capitalised(asWords(group.category))),
      name: capitalised(asWords(group.category)),
      detail: [group.confidence ? asWords(group.confidence) : "", coverageText(group)].filter(Boolean).join(", "),
      amount: group.amount,
    })),
    ...summary.unallocated.map((u) => ({
      open: () => openAllocations(summary.currency, { category: "unallocated" }, (a) => a.method === u.reason,
        `Unallocated · ${asWords(u.reason)}`),
      name: "Unallocated", detail: asWords(u.reason), amount: u.amount, attention: true,
    })),
    // Adjustments are Unallocated amounts shown apart (OQ-18): always listed, highlighted.
    {
      open: () => openAllocations(summary.currency, { category: "unallocated" }, (a) => a.method === "adjustment", "Adjustments"),
      name: "Adjustments", detail: "credits, refunds, discounts", amount: summary.adjustments, attention: true,
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
  const [models, runs] = await Promise.all([
    getJson(ROLLUPS, { ...params, level: "model_execution" }),
    getJson(ROLLUPS, { ...params, level: "workload_run" }),
  ]);
  if (!current()) return;
  byId("rollups-model").replaceChildren(rollupPanel("Model Executions", models, modelExecutionName));
  byId("rollups-run").replaceChildren(rollupPanel("Workload Runs", runs,
    (key) => ({ name: key, detail: "", mono: true, open: () => openLineage(key) })));
  if (!state.deepLinked) {
    state.deepLinked = true;
    openFromHash().catch(fail);
  }
}

// ---- Details on demand (#118): the Allocations behind an amount, one Allocation's Cost
// Explanation, and a Workload Run's lineage, shown under the charts. `#explain=<ref>` and
// `#run=<id>` open them directly, so a link can be shared.

function makeOpenable(node, open) {
  node.classList.add("openable");
  node.setAttribute("role", "button");
  node.addEventListener("click", () => open().catch(fail));
  node.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open().catch(fail);
    }
  });
}

function showDetail(title, ...content) {
  const section = byId("detail");
  section.hidden = false;
  section.replaceChildren(element("h2", { text: title }), ...content);
  section.scrollIntoView({ block: "start" });
}

function selectedScope() {
  const summary = state.summaries[state.scope];
  return summary ? scopeParams(summary.scope) : {};
}

async function openAllocations(currency, filters, keep, title) {
  const page = await getJson(ALLOCATIONS, { ...selectedScope(), ...filters, cost_basis: state.basis, limit: 1000 });
  const entries = page.items.filter((entry) => entry.allocation.currency === currency && keep(entry.allocation));
  const body = element("tbody");
  for (const { ref, allocation } of entries) {
    const row = element("tr", { tabindex: "0" },
      element("td", { class: "mono", text: allocation.billing_line_ref }),
      element("td", { class: "mono", text: allocation.target_id }),
      element("td", { text: [asWords(allocation.method), allocation.confidence ? asWords(allocation.confidence) : "",
        allocation.coverage != null ? `Coverage ${allocation.coverage}` : ""].filter(Boolean).join(", ") }),
      element("td", { class: "amount", text: allocation.amount }));
    makeOpenable(row, () => openExplanation(ref));
    body.append(row);
  }
  const head = element("tr");
  for (const [name, amount] of [["Billing Line", false], ["Allocation Target", false], ["How", false], [`Amount, ${currency}`, true]]) {
    head.append(element("th", { scope: "col", class: amount ? "amount" : "", text: name }));
  }
  showDetail(`${title}: its Allocations (${state.basis})`,
    element("p", { class: "meta", text: `Calculation Run ${page.calculation_run_id}. Choose an Allocation for its Cost Explanation.` }),
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

function statusText(run) {
  if (!run) return "not reported: known only as another run's parent or root";
  return run.status;
}

function runButton(runId, run) {
  const attention = run && ATTENTION_STATUSES.has(run.status);
  const item = element("li", { class: `run${attention ? " attention" : ""}`, tabindex: "0" },
    element("span", { class: "mono", text: runId }),
    element("span", { class: "detail", text: ` · ${run ? `${run.job_namespace} / ${run.job_name} · ` : ""}${statusText(run)}` }));
  makeOpenable(item, () => openLineage(runId));
  return item;
}

async function openExplanation(ref) {
  const x = await getJson(EXPLANATION.replace("{ref}", encodeURIComponent(ref)), {});
  history.replaceState(null, "", `#explain=${encodeURIComponent(ref)}`);
  const a = x.allocation;
  const line = x.billing_line;
  const sections = [
    element("h3", { text: "1 · Billing Line" }),
    facts([["Reference", x.billing_line_ref, { mono: true }], ["Service", line.ServiceName], ["Charge", line.ChargeDescription],
      ["Charge period", `${line.ChargePeriodStart} – ${line.ChargePeriodEnd}`], [a.cost_basis, `${line[a.cost_basis]} ${line.BillingCurrency}`],
      ["Usage", line.ConsumedQuantity != null ? `${line.ConsumedQuantity} ${line.ConsumedUnit || ""}` : null],
      ["Account, region", [line.SubAccountId, line.RegionId].filter(Boolean).join(", ")]]),
    element("details", {}, element("summary", { text: "Every column as exported" }),
      element("pre", { class: "mono", text: JSON.stringify(line, null, 2) })),
    element("h3", { text: "2 · Allocation" }),
    facts([["Amount", `${a.amount} ${a.currency} (${a.cost_basis})`], ["Allocation Category", asWords(a.category)],
      ["Method", asWords(a.method)], ["Confidence", a.confidence ? asWords(a.confidence) : null],
      ["Coverage", a.coverage], ["Unallocated because", x.unallocated_reason ? asWords(x.unallocated_reason) : null],
      ["Policy version", a.policy_version], ["Calculation Run", a.calculation_run_id, { mono: true }]]),
    element("h3", { text: "3 · Evidence" }),
  ];
  if (!x.query_execution && !x.model_execution && x.workload_runs.length === 0) {
    sections.push(element("p", { class: "meta", text: "None: nothing explicit names a Query Execution for this amount (ADR 0006)." }));
  }
  if (x.query_execution) {
    const q = x.query_execution;
    sections.push(element("h4", { text: "Query Execution" }), facts([["ID", q.query_execution_id, { mono: true }],
      ["Account, region", `${q.account_id}, ${q.region}`], ["Workgroup", q.workgroup], ["State", q.state],
      ["Scanned bytes", q.scanned_bytes], ["Billable bytes", q.billable_bytes]]));
  }
  if (x.model_execution) {
    const m = x.model_execution;
    sections.push(element("h4", { text: "Model Execution" }), facts([["dbt Node", m.node_id, { mono: true }],
      ["Invocation", m.invocation_id, { mono: true }], ["Status", m.execution ? m.execution.status : "not in the run results"],
      ["Materialized", m.dbt_node ? m.dbt_node.materialized : null]]));
  }
  if (x.workload_runs.length) {
    sections.push(element("h4", { text: "Workload Runs, nearest first" }),
      element("ul", { class: "runs" }, ...x.workload_runs.map((r) => runButton(r.run_id, r.run))));
  }
  sections.push(element("h3", { text: "4 · Roll-ups it contributes to" }));
  sections.push(x.rollups.length
    ? facts(x.rollups.map((r) => [r.level === "model_execution" ? `Model Execution ${modelExecutionName(r.key).name}` : `Workload Run ${r.key}`,
      `${r.amount} ${r.currency} (${r.cost_basis})`]))
    : element("p", { class: "meta", text: "None: no Identity Link reaches a Model Execution or Workload Run." }));
  showDetail(`Cost Explanation of ${ref}`, ...sections);
}

function claimsList(claims) {
  if (!claims.length) return element("p", { class: "meta", text: "No query claims it." });
  return element("ul", { class: "claims" }, ...claims.map((c) => element("li", {},
    element("span", { class: "mono", text: c.query_execution.query_execution_id }),
    element("span", { class: "detail", text: ` · ${asWords(c.outcome)}` }))));
}

async function openLineage(runId) {
  const l = await getJson(LINEAGE, { run_id: runId });
  history.replaceState(null, "", `#run=${encodeURIComponent(runId)}`);
  const run = l.run;
  const attention = run && ATTENTION_STATUSES.has(run.status);
  const sections = [
    element("p", { class: `delta${attention ? " attention" : ""}`, text: `Status: ${statusText(run)}` }),
    facts([["Job", run ? `${run.job_namespace} / ${run.job_name}` : null], ["Started", run ? run.started_at : null],
      ["Ended", run ? run.ended_at : null]]),
    element("h3", { text: "Above it, nearest first" }),
    l.ancestors.length ? element("ul", { class: "runs" }, ...l.ancestors.map((a) => runButton(a.run_id, a.run)))
      : element("p", { class: "meta", text: "None: a root run." }),
    element("h3", { text: "Below it" }),
    l.children.length ? element("ul", { class: "runs" }, ...l.children.map((c) => runButton(c.run_id, c)))
      : element("p", { class: "meta", text: "None." }),
    element("h3", { text: "Identity Links" }),
  ];
  if (l.invocation) {
    sections.push(facts([["dbt invocation", l.invocation.invocation_id, { mono: true }],
      ["Link Outcome", asWords(l.invocation.outcome)], ["Linked run", l.invocation.linked_run_id, { mono: true }]]));
  }
  if (l.model_run_link) sections.push(facts([["Model Execution link", asWords(l.model_run_link.outcome)]]));
  for (const m of l.model_executions) {
    sections.push(element("h4", { text: `${m.node_id}` }),
      facts([["Status", m.execution ? m.execution.status : "not in the run results"]]), claimsList(m.queries));
  }
  if (l.unmatched_queries.length) {
    sections.push(element("h4", { text: "Queries naming an unreported node" }), claimsList(l.unmatched_queries));
  }
  sections.push(element("h3", { text: "Data Assets" }),
    l.lineage_edges.length
      ? facts(l.lineage_edges.map((e) => [asWords(e.direction), `${e.asset.namespace} / ${e.asset.name}`]))
      : element("p", { class: "meta", text: "None reported." }));
  showDetail(`Lineage of Workload Run ${runId}`, ...sections);
}

async function openFromHash() {
  const [kind, value] = decodeURIComponent(location.hash.slice(1)).split(/=(.*)/s);
  if (kind === "explain" && value) await openExplanation(value);
  else if (kind === "run" && value) await openLineage(value);
}

const fail = (error) => showNotice(`Could not load: ${error.message}`);
byId("basis").addEventListener("change", (e) => { state.basis = e.target.value; render().catch(fail); });
byId("scope").addEventListener("change", (e) => { state.scope = Number(e.target.value); render().catch(fail); });
render().catch(fail);
