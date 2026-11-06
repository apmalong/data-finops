# Documentation site (D)

Decided in the D design session (2026-11-05, #135). Format and structure are in ADR 0025. The site is for people who run data-finops; contributors come second.

## Pages
One page per document under `site/`, each one Diátaxis type. Home is `index.html`, with an index page for each type.

- **Tutorials:** `first-allocation` (the demo walkthrough). "Instrument an Airflow and dbt workload" waits until after M6 and #26, since it costs real AWS money.
- **How-to guides:** `install`, `ingest-focus-export`, `receive-openlineage`, `ingest-dbt-artifacts`, `ingest-athena-history`, `calculate`, `handle-restated-export`, `serve-with-token`, `read-cost-explanation`, `deploy-reference-workload` (teardown steps link to the reference workload).
- **Reference:** `cli`, `http-api`, `data-contracts` (generated); `exit-codes-and-environment` (hand-written); `glossary` (generated).
- **Explanation:** `bill-is-authoritative`, `allocation-and-coverage`, `identity-links-and-evidence`, `export-snapshots-and-restatements`, `security-model`, `decision-records` (an index of ADR titles linking to GitHub, never copies). The Athena policy text names the policy version and says it is provisional until #91 closes.
- `contributing.html` links to the specs, ADRs, plan, progress and research on GitHub.

## Layout
- `site/index.html`, `site/{tutorials,how-to,reference,explanation}/`, `site/assets/` (`site.css`, optional `site.js`, SVGs, screenshots), `site/nav.json`, `site/DESIGN.md`.
- Every page has the same `<head>`, header, nav and footer. The nav sits between `<!-- nav:start -->` and `<!-- nav:end -->` and is stamped from `site/nav.json`.
- The footer says the site describes `main`, that nothing is released yet, and that the license isn't chosen yet (OQ-8). It makes no license claim until OQ-8 is decided.
- Every link is relative. Root-absolute links (`/…`) are refused, so the same files work from `file://`, a checkout and `https://apmalong.github.io/data-finops/`.

## Design and writing (`site/DESIGN.md`)
- The dashboard's visual rules (spec 06): Tufte and Few, light and dark through `prefers-color-scheme`, a system font stack, muted colour used only to draw attention.
- `site.css` starts as a copy of the dashboard's tokens and evolves separately. Foreground and background token pairs reach at least 4.5:1 contrast in both themes.
- Accessible: `lang`, one `<h1>`, ordered headings, alt text on every image, visible focus, and the nav usable by keyboard. Pages work without JavaScript.
- No `http(s)://` in CSS or JS. Outbound links in page text are fine.
- Diagrams are committed SVGs rendered from D2 sources in `docs/architecture/` (`d2 --layout elk`). There is no Mermaid.
- Writing: glossary terms capitalised as in GLOSSARY, never their avoided synonyms. Short declarative sentences. Explanation pages cite their ADRs and specs, which stay authoritative. The only account ID is the placeholder `123456789012`. No real account IDs, bucket names, credentials or SQL literals appear.

## The site tool (`scripts/site/`)
Stdlib only, dev tooling outside the wheel, under ruff and mypy. It has two commands: `python -m scripts.site build` rewrites the generated pages and nav, and `python -m scripts.site check` reports problems and exits non-zero on any.

- **CLI reference:** walked from `data_finops.cli`'s argparse parser, covering every command, argument, default and help string.
- **HTTP API reference:** from `schemas/openapi.json`, covering every path, method, parameter, response and Problem Details.
- **Data contracts:** from the JSON schemas in `schemas/`.
- **Glossary:** from `GLOSSARY.md`, read by a parser for its fixed `**Term**:` / definition / `_Avoid_:` format (not a general Markdown parser). Each term gets an anchor.
- **Check:**
  - Internal links and `#anchors` resolve, ids are unique, and tags are balanced.
  - Each page has `lang`, a `<title>`, one `<h1>` and alt text on every image.
  - Every image used exists.
  - No root-absolute links.
  - No 12-digit number other than `123456789012`.
  - No external URL in CSS or JS.
  - Every page is in `nav.json`, and every `nav.json` entry exists.

## Runnable pages
- A `<pre><code class="run">` block holds only `data-finops …` lines, split with `shlex`, using repo-relative fixture paths. They run in order through `main(argv)` from a temporary working directory.
- A `serve` line opens FastAPI's test client over that database. A `curl` line in a `run` block is a GET through that client.
- A `<pre class="output checked">` right after a `run` block must match the output, with `…` standing for any text within a line. Other output blocks are illustrative.
- Commands with placeholders (`<your-export>`) are never marked `run`. Steps that need AWS are labelled as not run.
- `first-allocation` and every offline how-to (`install` excepted) have runnable blocks. `test_m5_exit.py` runs the tutorial page, and `examples/demo/README.md` becomes a pointer to it.

## Exit codes and environment
`exit-codes-and-environment` is hand-written. A test checks that every `DATA_FINOPS_*` name in `src/` appears on it, and that its exit codes match the CLI's.

## Build and deploy
- **PRs:** the site tests run in pytest inside the existing required `quality` job, with no new required check.
- **Deploy:** `.github/workflows/pages.yml` runs on push to `main` and on `workflow_dispatch`. It has only `pages: write` and `id-token: write` permissions, pins `actions/upload-pages-artifact` and `actions/deploy-pages` to commit SHAs, and uploads `site/` as is.
- **Pages setting:** the owner sets the Pages source to "GitHub Actions". The agent asks first.
- **README:** keeps status, a three-line quick start, a link to the site and the contributor "Start here".

## Testing
Four seams, each test-first with hand-made mutation checks recorded in the PR:
1. **The checker:** `check_site(root) -> list[Problem]` over tiny sites in `tmp_path`, one problem each. One test runs it over the real `site/` and expects none.
2. **The generator:** `build(repo) -> {path: html}`. Each renderer is tested with a small input (a toy argparse parser, a two-route OpenAPI document, a three-term glossary), and nav stamping is tested too. One drift test compares `build(repo)` with the committed `site/` and names the regenerate command.
3. **The page runner:** it pulls the `run` blocks out of a page and runs them. There is one test for the tutorial and one for each offline how-to, plus unit tests on tiny pages for the extraction and `…` matching rules.
4. **The deploy workflow:** a unit test reads `pages.yml` and asserts SHA-pinned actions, the two write permissions only, and the triggers. The first real deploy is the end-to-end check.

Assertions are only on what each seam returns. Tests run with the venv's `python -m pytest` under a per-run timeout.

## Exit
The site is live on GitHub Pages from `main`. `check`, the drift test and the page-runner tests pass in CI. progress.md records a manual pass:
- one screenshot in light and one in dark
- a keyboard-only pass through the nav
- the tutorial followed by hand from the live site in under five minutes (spec 01)
