// data-finops dashboard: the Cost Summary and Roll-ups (spec 06; design rules in DESIGN.md).
// Reads only the JSON API. Amounts arrive as exact decimal strings and are shown exactly as
// sent; numbers are used only to size bars, never to display, add or divide money.
"use strict";

const SUMMARY = "/v1/costs/summary";
const ROLLUPS = "/v1/rollups";

// The bars take this share of their track, leaving room for the direct label at each end
// (both ends when a chart has negative amounts).
const BAR_REACH = 0.75;
const MIXED_REACH = 0.5;

const state = { scope: 0, basis: "BilledCost", request: 0 };
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
      name: capitalised(asWords(group.category)),
      detail: [group.confidence ? asWords(group.confidence) : "", coverageText(group)].filter(Boolean).join(", "),
      amount: group.amount,
    })),
    ...summary.unallocated.map((u) => ({ name: "Unallocated", detail: asWords(u.reason), amount: u.amount, attention: true })),
    // Adjustments are Unallocated amounts shown apart (OQ-18): always listed, highlighted.
    { name: "Adjustments", detail: "credits, refunds, discounts", amount: summary.adjustments, attention: true },
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
  byId("rollups-run").replaceChildren(rollupPanel("Workload Runs", runs, (key) => ({ name: key, detail: "", mono: true })));
}

const fail = (error) => showNotice(`Could not load: ${error.message}`);
byId("basis").addEventListener("change", (e) => { state.basis = e.target.value; render().catch(fail); });
byId("scope").addEventListener("change", (e) => { state.scope = Number(e.target.value); render().catch(fail); });
render().catch(fail);
