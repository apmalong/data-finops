// data-finops dashboard overview (spec 06; design rules in DESIGN.md).
// Reads only the JSON API. Amounts arrive as exact decimal strings and are shown as sent;
// numbers are used only to size bars, never to display or add money.
"use strict";

const SUMMARY = "/v1/costs/summary";
const ROLLUPS = "/v1/rollups";

const state = { summaries: [], scope: 0, basis: "BilledCost" };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

async function getJson(path, params) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(query ? `${path}?${query}` : path);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail || `${path} answered ${response.status}`);
  }
  return response.json();
}

const words = (value) => String(value).replaceAll("_", " ");
const money = (amount, currency) => `${amount} ${currency}`;

function scopeParams(scope) {
  return {
    billing_account_id: scope.billing_account_id,
    billing_period: scope.billing_period_start.slice(0, 10),
    source: scope.source,
  };
}

// The bars take this share of their track, leaving room for the direct labels at their ends
// (both ends when a chart has negative amounts).
const BAR_REACH = 0.75;
const MIXED_REACH = 0.5;

// One chart: rows on a common zero baseline, sorted by size, each labelled directly.
function barChart(rows, currency, billed) {
  const sized = rows.map((row) => ({ ...row, value: Number(row.amount) }));
  sized.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const most = Math.max(...sized.map((r) => Math.max(r.value, 0)), 0);
  const least = Math.min(...sized.map((r) => Math.min(r.value, 0)), 0);
  const reach = least < 0 ? MIXED_REACH : BAR_REACH;
  const span = (most - least || 1) / reach;
  const zero = least < 0 ? (1 - reach) * 50 + (-least / span) * 100 : 0;
  const list = el("ul", { class: "bars" });
  for (const row of sized) {
    const width = (Math.abs(row.value) / span) * 100;
    const negative = row.value < 0;
    const bar = el("span", {
      class: `bar${negative ? " negative" : ""}${row.attention ? " attention" : ""}`,
      style: `left:${negative ? zero - width : zero}%;width:${width}%`,
    });
    const value = el("span", {
      class: "bar-value",
      text: money(row.amount, currency),
      style: negative ? `right:${100 - (zero - width)}%` : `left:${zero + width}%`,
    });
    const track = el("span", { class: "bar-track" }, el("span", { class: "zero", style: `left:${zero}%` }), bar, value);
    const label = el("span", { class: "bar-label" }, el("strong", { class: row.mono ? "mono" : "", text: row.name }));
    if (row.tag) label.append(el("span", { class: "tag", text: ` · ${row.tag}` }));
    const item = el("li", { class: "bar-row", tabindex: "0" }, label, track);
    const share = billed ? ` · ${((row.value / Number(billed)) * 100).toFixed(1)}% of billed` : "";
    const describe = `${row.name}${row.tag ? ` (${row.tag})` : ""}: ${money(row.amount, currency)}${share}`;
    item.setAttribute("aria-label", describe);
    attachTooltip(item, describe);
    list.append(item);
  }
  return list;
}

function attachTooltip(node, text) {
  const tip = $("tooltip");
  const show = (x, y) => {
    tip.textContent = text;
    tip.hidden = false;
    tip.style.left = `${Math.min(x + 12, window.innerWidth - 330)}px`;
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

function table(caption, head, rows) {
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr", { class: row.kind || "" });
    row.cells.forEach((cell, i) => tr.append(el("td", { class: head[i].amount ? "amount" : "", text: cell })));
    body.append(tr);
  }
  const thead = el("tr");
  for (const h of head) thead.append(el("th", { class: h.amount ? "amount" : "", scope: "col", text: h.name }));
  return el("table", {}, el("caption", { text: caption }), el("thead", {}, thead), body);
}

// The Cost Summary of one currency: headline, where it went, exact amounts.
function currencyPanel(summary) {
  const c = summary.currency;
  const rows = [
    ...summary.allocated.map((a) => ({
      name: a.category === "idle" ? "Idle" : words(a.category),
      tag: a.confidence ? words(a.confidence) : "",
      amount: a.amount,
    })),
    ...summary.unallocated.map((u) => ({ name: "Unallocated", tag: words(u.reason), amount: u.amount, attention: true })),
  ];
  if (Number(summary.adjustments) !== 0) {
    rows.push({ name: "Adjustments", tag: "credits, refunds, discounts", amount: summary.adjustments, attention: true });
  }
  const delta = Number(summary.reconciliation_delta);
  const deltaText = delta === 0
    ? `Reconciles: delta ${money(summary.reconciliation_delta, c)}`
    : `Reconciliation Delta ${money(summary.reconciliation_delta, c)}: Allocations do not match the bill`;
  const panel = el("article", { class: "panel" },
    el("div", { class: "headline" },
      el("div", {}, el("h3", { text: `Billed (${state.basis})` }),
        el("div", { class: "hero" }, document.createTextNode(summary.billed), el("small", { text: c }))),
      el("p", { class: `delta${delta === 0 ? "" : " attention"}`, text: deltaText })),
    barChart(rows, c, summary.billed),
    table(`Exact amounts, ${c}`, [{ name: "Where" }, { name: "Detail" }, { name: "Amount", amount: true }], [
      ...rows.map((r) => ({ cells: [r.name, r.tag, r.amount] })),
      { kind: "total", cells: ["Billed", "", summary.billed] },
      { kind: "group", cells: ["Reconciliation Delta", "billed − every Allocation", summary.reconciliation_delta] },
    ]));
  return panel;
}

function modelName(key) {
  try {
    const [invocation, node] = JSON.parse(key);
    return { name: node, tag: `invocation ${invocation}` };
  } catch {
    return { name: key, tag: "" };
  }
}

function rollupPanel(title, page, describe) {
  const panel = el("article", { class: "panel" }, el("h3", { text: title }));
  const byCurrency = new Map();
  for (const item of page.items) {
    for (const t of item.totals) {
      if (t.cost_basis !== state.basis) continue;
      if (!byCurrency.has(t.currency)) byCurrency.set(t.currency, []);
      byCurrency.get(t.currency).push({ ...describe(item.key), amount: t.amount });
    }
  }
  if (byCurrency.size === 0) {
    panel.append(el("p", { class: "meta", text: "Nothing linked rolls up here." }));
    return panel;
  }
  for (const [currency, rows] of byCurrency) {
    panel.append(barChart(rows, currency, null));
    panel.append(table(`Exact amounts, ${currency}`, [{ name: "Key" }, { name: "Detail" }, { name: "Amount", amount: true }],
      rows.map((r) => ({ cells: [r.name, r.tag, r.amount] }))));
  }
  if (page.total > page.items.length) {
    panel.append(el("p", { class: "meta", text: `Showing ${page.items.length} of ${page.total}.` }));
  }
  return panel;
}

async function render() {
  const notice = $("notice");
  notice.hidden = true;
  const result = await getJson(SUMMARY, { cost_basis: state.basis });
  state.summaries = result.summaries;
  if (result.uncalculated_scopes.length) {
    notice.hidden = false;
    notice.textContent = `${result.uncalculated_scopes.length} Snapshot Scope(s) have an Export Snapshot but no Calculation Run yet; their bill is not shown. Run data-finops calculate.`;
  }
  const select = $("scope");
  select.replaceChildren(...state.summaries.map((s, i) => el("option", {
    value: String(i),
    text: `${s.scope.billing_account_id} · ${s.scope.billing_period_start.slice(0, 7)} · ${s.scope.source}`,
  })));
  if (state.summaries.length === 0) {
    $("currencies").replaceChildren(el("p", { class: "meta", text: "No Allocation Snapshot yet: import a FOCUS export and run data-finops calculate." }));
    return;
  }
  state.scope = Math.min(state.scope, state.summaries.length - 1);
  select.value = String(state.scope);
  const summary = state.summaries[state.scope];
  const run = summary.snapshot;
  $("provenance").textContent =
    `Calculation Run ${run.calculation_run_id} · ${run.created_at.replace("T", " ").replace("Z", " UTC")} · policy ${run.policy_version}${run.is_current ? "" : " · superseded"}`;
  $("currencies").replaceChildren(...summary.currencies.map(currencyPanel));
  const params = { ...scopeParams(summary.scope), limit: 50 };
  const [models, runs] = await Promise.all([
    getJson(ROLLUPS, { ...params, level: "model_execution" }),
    getJson(ROLLUPS, { ...params, level: "workload_run" }),
  ]);
  $("rollups-model").replaceChildren(rollupPanel("Model Executions", models, modelName));
  $("rollups-run").replaceChildren(rollupPanel("Workload Runs", runs, (key) => ({ name: key, tag: "", mono: true })));
}

function fail(error) {
  const notice = $("notice");
  notice.hidden = false;
  notice.textContent = `Could not load: ${error.message}`;
}

$("basis").addEventListener("change", (e) => { state.basis = e.target.value; render().catch(fail); });
$("scope").addEventListener("change", (e) => { state.scope = Number(e.target.value); render().catch(fail); });
render().catch(fail);
