// Runs calls of the dashboard's format.js (#313), for tests/integration/test_dashboard_format.py.
// Usage: node format_harness.js <format.js>, with a JSON list of [name, ...args] on stdin;
// prints one result for each call.
"use strict";
const fs = require("fs");
const format = require(process.argv[2]);

const calls = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(calls.map(([name, ...args]) => format[name](...args))));
