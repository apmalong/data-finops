// data-finops dashboard: how labels, amounts and tabs are written (#313). No DOM, so Node
// runs the same code in tests (tests/integration/test_dashboard_format.py).
"use strict";

(function (root) {
  const TABS = ["summary", "models", "runs", "dimensions"];

  /** An ID as the chart shows it: its first eight characters (a UUID's first group). */
  function shortId(id) {
    return id.length > 8 ? id.slice(0, 8) : id;
  }

  /** The API's exact decimal strings with one number of decimals: no digit is dropped,
   *  zeros are added, so the amounts of one chart line up. */
  function alignAmounts(amounts) {
    const places = Math.max(0, ...amounts.map((a) => (a.split(".")[1] || "").length));
    return amounts.map((amount) => {
      const [whole, fraction = ""] = amount.split(".");
      return places ? `${whole}.${fraction.padEnd(places, "0")}` : whole;
    });
  }

  /** An exact amount with its currency's symbol, or after it its code when the currency
   *  has no symbol of its own. The digits are the amount's, never rounded. */
  function money(amount, currency) {
    const symbol = new Intl.NumberFormat("en", { style: "currency", currency })
      .formatToParts(0).find((part) => part.type === "currency").value;
    if (symbol === currency) return `${amount} ${currency}`;
    return amount.startsWith("-") ? `-${symbol}${amount.slice(1)}` : `${symbol}${amount}`;
  }

  /** A Model Execution's label: its node, then its invocation by a short ID. The title
   *  keeps the full ID. */
  function modelLabel(key) {
    const [invocation, node] = JSON.parse(key); // the API's Model Execution key (spec 02)
    return {
      name: node,
      detail: `invocation ${shortId(invocation)}`,
      title: `${node} · invocation ${invocation}`,
    };
  }

  /** A Workload Run's label: its job and start, then its short ID; a run with no event of
   *  its own (only another run's parent or root) has its ID alone. */
  function runLabel(runId, run) {
    if (!run) return { name: `run ${shortId(runId)}`, detail: "no event of its own", title: `run ${runId}` };
    const started = run.started_at ? `started ${run.started_at.slice(0, 16).replace("T", " ")} UTC · ` : "";
    return {
      name: run.job_name,
      detail: `${started}run ${shortId(runId)}`,
      title: `${run.job_name} · run ${runId}`,
    };
  }

  /** The tab an address names: `#tab=<name>`, the Runs tab for `#run=`, the first tab when
   *  none is named, and null for `#explain=`, which keeps the open tab. */
  function tabFromHash(hash) {
    const [kind, value] = hash.replace(/^#/, "").split(/=(.*)/s);
    if (kind === "explain") return null;
    if (kind === "run") return "runs";
    return kind === "tab" && TABS.includes(value) ? value : TABS[0];
  }

  const exported = { TABS, shortId, alignAmounts, money, modelLabel, runLabel, tabFromHash };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  root.DataFinopsFormat = exported;
})(typeof window !== "undefined" ? window : globalThis);
