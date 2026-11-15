// Runs reads through the dashboard's static data source (#228) over a site folder, for
// tests/integration/test_dashboard_static.py. Usage: node static_harness.js <static.js>
// <site folder>, with a JSON list of calls on stdin; prints one result for each call.
"use strict";
const fs = require("fs");
const path = require("path");
const { createStaticSource } = require(process.argv[2]);

const site = process.argv[3];
const calls = JSON.parse(fs.readFileSync(0, "utf8"));
const fetchJson = async (key) => {
  try {
    return JSON.parse(fs.readFileSync(path.join(site, ...key.split("/")), "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
};

(async () => {
  const source = createStaticSource(fetchJson);
  const results = [];
  for (const call of calls) {
    try {
      const value = "banner" in call ? await source.banner(call.banner) : await source.get(call.path, call.params || {});
      results.push({ ok: value });
    } catch (error) {
      if (!error.notInDemo) throw error;
      results.push({ notInDemo: error.message });
    }
  }
  process.stdout.write(JSON.stringify(results));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
