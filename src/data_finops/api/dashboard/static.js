// data-finops dashboard: the static data source of the public demo (#228; spec 09 §The site).
// It answers the dashboard's reads from a Demo Snapshot (`public_demo.publisher.KEYS`) as the
// API would: the same shapes, pages, filters and Snapshot Scope selection. A read that the
// snapshot does not cover is "not in the public demo". The dashboard uses it only when its
// page says `<meta name="data-finops-source" content="static">`; locally it reads the API.
"use strict";

(function (root) {
  const PAGE = 1000; // the publisher's page size (`MAX_PAGE_SIZE`)
  const ALLOCATION_FILTERS = ["target_id", "category", "billing_line_ref", "cost_basis"];
  const BANNER = "Simulated workload; real Athena charges, reconciled to the bill; data trails by about a day; amounts before credits.";

  class NotInDemo extends Error {
    constructor(what) {
      super(`${what} is not in the public demo`);
      this.notInDemo = true;
    }
  }

  /** A path segment taken from a read; a separator or a dot segment is never followed. */
  function segment(value, what) {
    const text = String(value);
    if (!text || /[/\\]/.test(text) || text === "." || text === "..") throw new NotInDemo(what);
    return text;
  }

  function scopeMatches(scope, params) {
    return (params.billing_account_id == null || scope.billing_account_id === params.billing_account_id)
      && (params.billing_period == null || scope.billing_period_start.slice(0, 10) === params.billing_period)
      && (params.source == null || scope.source === params.source);
  }

  /** One page of `items` for a read's `limit` and `offset`, in the shape of `first`. */
  function page(first, items, params) {
    const limit = Number(params.limit ?? 100);
    const offset = Number(params.offset ?? 0);
    return { ...first, total: items.length, limit, offset, items: items.slice(offset, offset + limit) };
  }

  /** The static source over `fetchJson(path)`, which gives a file of the site as JSON, or
   *  null when there is no such file. */
  function createStaticSource(fetchJson) {
    let prefix = null;
    const files = new Map();

    async function file(path, what) {
      prefix ??= fetchJson("current.json").then((pointer) => {
        if (!pointer) throw new NotInDemo("A Demo Snapshot");
        return pointer.prefix;
      });
      const key = (await prefix) + path;
      if (!files.has(key)) files.set(key, fetchJson(key));
      const document = await files.get(key);
      if (document == null) throw new NotInDemo(what);
      return document;
    }

    /** Every item of a paged file set, `<base>/page-<n>.json`, and its first page. */
    async function everything(base, what) {
      const first = await file(`${base}/page-0.json`, what);
      const items = [...first.items];
      for (let n = 1; n * PAGE < first.total; n++) items.push(...(await file(`${base}/page-${n}.json`, what)).items);
      return { first, items };
    }

    async function runOf(params) {
      if (params.calculation_run_id) return segment(params.calculation_run_id, "That Calculation Run");
      const summary = await file("v1/costs/summary/BilledCost.json", "The Cost Summary");
      const matching = summary.summaries.filter((s) => scopeMatches(s.scope, params));
      if (matching.length !== 1) throw new NotInDemo("That Snapshot Scope");
      return segment(matching[0].snapshot.calculation_run_id, "That Calculation Run");
    }

    async function get(path, params = {}) {
      let match;
      if (path === "/v1/costs/summary") {
        const basis = segment(params.cost_basis || "BilledCost", "That Cost Basis");
        return file(`v1/costs/summary/${basis}.json`, `The Cost Summary in ${basis}`);
      }
      if (path === "/v1/allocations") {
        const run = await runOf(params);
        const { first, items } = await everything(`v1/allocations/${run}`, "Those Allocations");
        const kept = items.filter((entry) => ALLOCATION_FILTERS.every(
          (name) => params[name] == null || String(entry.allocation[name]) === String(params[name])));
        return page(first, kept, params);
      }
      if ((match = path.match(/^\/v1\/allocations\/([^/]+)\/explanation$/))) {
        const ref = decodeURIComponent(match[1]);
        const [run, position] = ref.split("#");
        const what = `The Cost Explanation of ${ref}`;
        return file(`v1/allocations/${segment(run, what)}/${segment(position, what)}/explanation.json`, what);
      }
      if (path === "/v1/rollups") {
        const run = await runOf(params);
        const level = segment(params.level, "That Roll-up level");
        const { first, items } = await everything(`v1/rollups/${level}/${run}`, "Those Roll-ups");
        return page(first, items, params);
      }
      if ((match = path.match(/^\/v1\/rollups\/dimensions\/([^/]+)$/))) {
        const name = segment(decodeURIComponent(match[1]), "That Cost Dimension");
        const run = await runOf(params);
        const what = `The Cost Dimension ${name}`;
        const { first, items } = await everything(`v1/rollups/dimensions/${name}/${run}`, what);
        return page(first, items, params);
      }
      if (path === "/v1/lineage") {
        if (params.run_id != null) {
          const what = `The lineage of Workload Run ${params.run_id}`;
          return file(`v1/lineage/runs/${segment(params.run_id, what)}.json`, what);
        }
        const { first, items } = await everything("v1/lineage", "The Workload Runs");
        return page(first, items, params);
      }
      throw new NotInDemo(`The read ${path}`);
    }

    /** The banner: what the demo is, then when its snapshot was published and the
     *  Calculation Run of the Billing Period shown. */
    async function banner(runId) {
      const snapshot = await file("snapshot.json", "The Demo Snapshot");
      const when = snapshot.published_at.replace("T", " ").replace(/:\d\dZ$/, " UTC");
      return `${BANNER} Published ${when}${runId ? ` · Calculation Run ${runId}` : ""}.`;
    }

    return { get, banner };
  }

  const exported = { createStaticSource, NotInDemo, BANNER };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else root.DataFinopsStatic = exported;
})(typeof window !== "undefined" ? window : globalThis);
