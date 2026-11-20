# Documentation site (D)

Decided in the D design session (2026-11-05, #135). Format and structure are in ADR 0025. The site is for people who run data-finops. Contributors come second.

## Pages
One page per document under `site/`, each one Diátaxis type. Home is `index.html`, with an index page for each type.

- **Tutorials:** `first-allocation` (the demo walkthrough). "Instrument an Airflow and dbt workload" waits until after M6 and #26, since it costs real AWS money.
- **How-to guides:** `install`, `ingest-focus-export`, `receive-openlineage`, `ingest-dbt-artifacts`, `ingest-athena-history`, `calculate`, `handle-restated-export`, `serve-with-token`, `read-cost-explanation`, `deploy-reference-workload` (teardown steps link to the reference workload). M6 adds `deploy-to-aws`, `send-to-inbox`, `read-deployment` and `tear-down-deployment`, a generated `deployment-settings` reference page and a `deployment-architecture` explanation (spec 08). M7 adds the how-to `configure-cost-dimensions`, the explanations `compared-with-aws-tools` and `public-demo`, and links the Public Demo from the home page and the navigation (spec 09).
- **Reference:** `cli`, `http-api`, `data-contracts`, `deployment-settings` and `glossary` (generated), and `exit-codes-and-environment` (hand-written).
- **Explanation:** `bill-is-authoritative`, `allocation-and-coverage`, `identity-links-and-evidence`, `export-snapshots-and-restatements`, `security-model`, `decision-records` (an index of ADR titles with links to GitHub, never copies). The Athena policy text names the current policy version and what #91 found in the real export.
- `contributing.html` links to the specs, ADRs, plan, progress and research on GitHub.

## Layout
- `site/index.html`, `site/{tutorials,how-to,reference,explanation}/`, `site/assets/` (`site.css`, optional `site.js`, SVGs, screenshots), `site/nav.json`, `site/DESIGN.md`.
- Every page has the same `<head>`, header, nav and footer. The nav sits between `<!-- nav:start -->` and `<!-- nav:end -->` and is stamped from `site/nav.json`.
- The footer says the site describes `main`, that nothing is released yet, and that the license is not chosen yet (OQ-8). It makes no license claim until OQ-8 is decided.
- The readable template (owner, 2026-11-06, #171) restyles the same HTML:
  - The layout follows docs sites such as GitLab's (owner, 2026-11-15, #274). `build` stamps all of it in the `nav` region, and no part needs JavaScript:
    - A top bar across the full width: the site name, the search box, and links to the Public Demo and to GitHub. It stays in view.
    - A sidebar with every section. Each section that has pages is a `<details>`, with the section's own page first as "Overview", and only the current section is open. The current page has `aria-current`. On wide screens the sidebar stays in view and scrolls by itself. On narrow screens it comes after the page, and a "Menu" link in the top bar goes to it.
    - Breadcrumbs above the `h1`: Home, the section, then the page. The home page has none.
  - Search (#274): `build` writes `site/assets/search-index.json`, with the path, title, section, `h2`s and text of each page. The text leaves out the shared regions (the nav, "On this page", the previous and next links and a section's list of pages), the output of commands and the Windows version of each command. A generated reference page keeps its generated text. There is no external service.
    - `site.js` searches the index in the browser: a page matches when it holds each word of the query as text. A word in the title counts most, then a word in a heading, then a word in the text.
    - The box is hidden without JavaScript. `?q=` in the address fills it. Enter opens the first result, and Escape clears the box.
  - Each page has an "On this page" list of its `h2`s, between `<!-- onpage:start -->` and `<!-- onpage:end -->` after its `h1`. It is stamped from the finished page, generated reference included, and an `h2` without an `id` fails the build. On screens over about 1200px it is a right-hand column. On narrower screens it sits under the `h1`.
  - Previous and next links, in `nav.json` order, sit between `<!-- pager:start -->` and `<!-- pager:end -->` at the end of `main`.
  - A limit that a page states, such as a cap, a default or a policy version, sits between `<!-- limit:NAME -->` and `<!-- /limit -->` (#247). `build` stamps it from the constant that enforces it. `scripts/site/limits.py` names each constant and reads it from the source, without an import.
    - A number with a unit that a page states by hand fails the build. The exceptions are in `limits.py`, each with its reason, for example an AWS fact or a worked example. The output of commands is not checked.
  - `build` puts each line of every plain-text code block (run and output blocks included) in `<span class="line">`. Thus a long command wraps with an indent on its next lines, and does not scroll. The text the page runner reads is unchanged. A wide table scrolls inside its own focusable box, never the page.
- Every command example has Windows (PowerShell) and macOS/Linux versions (owner, 2026-11-06, #173):
  - A page holds the macOS/Linux block, which the page runner runs. `build` puts a Windows version beside it, in a `shell-tabs` group.
  - The Windows version comes from fixed rules:
    - `curl` becomes `curl.exe`.
    - `export X=v` becomes `$env:X = "v"`.
    - `X=v` becomes `$X = "v"`.
    - An unquoted `@file` argument gets quotes.
  - A line the rules cannot translate fails the build, until a hand-written `<pre class="windows">` follows the block. Such lines include redirects (PowerShell 5.1 writes `>` as UTF-16), `&&`, pipes, `$(…)` other than `$(cat FILE)`, and line continuations.
  - Output blocks, the CLI reference's `usage` blocks, and a block that holds other markup get no tabs. A `<pre class="windows">` that does not follow a command block fails the build.
  - `site/assets/site.js` shows one version at a time, chosen by the reader's OS and then their last choice, kept in `localStorage`. Without it, both versions show, each with its label.
- Every link is relative. Root-absolute links (`/…`) are refused, so the same files work from `file://`, a checkout and `https://apmalong.github.io/data-finops/`.

## Design and text (`site/DESIGN.md`)
- The dashboard's visual rules (spec 06): Tufte and Few, light and dark through `prefers-color-scheme`, a system font stack, muted color used only to get attention.
- `site.css` starts as a copy of the dashboard's tokens and evolves separately. Foreground and background token pairs reach at least 4.5:1 contrast in both themes.
- Readable: a 17px body, prose lines of at most about 68ch, a 1.6 line height, and a distinct heading scale with room above each `h2`.
- Accessible: `lang`, one `<h1>`, ordered headings, alt text on every image, visible focus, and the nav usable by keyboard. Pages work without JavaScript.
- No `http(s)://` in CSS or JS. Outbound links in page text are fine.
- Diagrams are committed SVGs rendered from D2 sources in `docs/architecture/` (`d2 --layout elk`). There is no Mermaid.
- Text: follow the `simplified-technical-english` skill (`.claude/skills/`). Glossary terms are capitalized as in GLOSSARY, never their avoided synonyms. Explanation pages cite their ADRs and specs, which stay authoritative. The only account ID is the placeholder `123456789012`. No real account IDs, bucket names, credentials or SQL literals appear.

## The site tool (`scripts/site/`)
It is stdlib only, and it is dev tooling outside the wheel, under ruff and mypy. Two parts import data_finops. The page runner imports it (with FastAPI) to run commands, and only tests import the page runner. The CLI reference of `build` imports it to load the parser of the command line, only when it renders that page. So `check` stays stdlib-only for the Pages workflow. It has two commands: `python -m scripts.site build` rewrites the generated pages and nav, and `python -m scripts.site check` reports problems and exits non-zero on any.

- **CLI reference:** from the argparse parser of `data_finops.cli`. It covers each command, argument, default and help string.
- **HTTP API reference:** from `schemas/openapi.json`. It covers each path, method, parameter, response and Problem Details.
- **Data contracts:** from the JSON schemas in `schemas/`.
- **Deployment settings:** from the stack's `infra/pulumi/aws/deployment/settings.py` and `program.py`, parsed with `ast` and never imported, since the root environment has no Pulumi (#190). Each `Settings` field gives its key from `CONFIG_KEYS`, its default, its docstring and its `BOUNDS`. The outputs come from `OUTPUTS`. Those stay literals, and the stack's tests hold `from_config` and the program's outputs to them.
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
- A `<pre><code class="run">` block holds only `data-finops …` and `curl …` lines. `shlex` splits them, and they use repo-relative fixture paths. They run in order through `main(argv)` with a temporary directory as the current directory.
- A `serve` line opens FastAPI's test client over that database. A `curl` line in a `run` block goes through that client. It takes `-s`, `-X GET|POST`, `-H 'Name: value'` and `--data-binary @file` (with a Content-Type header), as in curl, and nothing else. A non-2xx answer is a problem.
- In a `run` line, `$CALCULATION_RUN_ID` is the ID of the Calculation Run that the last `calculate` printed, and `$(cat FILE)` is the text of the file. A line must not put either in single quotes, where a shell would not expand it.
- The token how-to sends the dummy `fixtures/docs/example.token` with `$(cat …)`. No page writes a token into a command, so the secret scan has nothing to flag.
- A `<pre class="output checked">` right after a `run` block must match the output, where `…` matches any text in a line. Other output blocks are illustrative.
- Commands with placeholders (`<your-export>`) are never marked `run`. Steps that require AWS are labeled as not run.
- `first-allocation` and every offline how-to (`install` excepted) have runnable blocks. `test_m5_exit.py` runs the tutorial page, and `examples/demo/README.md` becomes a pointer to it.

## Exit codes and environment
`exit-codes-and-environment` is hand-written. A test checks that the `DATA_FINOPS_*` names on it are exactly the names in `src/` and `scripts/`. It also checks that its exit codes are exactly the `ExitCode` values of the CLI.

## Build and deploy
- **PRs:** the site tests run in pytest inside the required `quality` job that already exists, with no new required check.
- **Deploy:** `.github/workflows/pages.yml` runs on push to `main` and on `workflow_dispatch`. It has only `contents: read`, `pages: write` and `id-token: write` permissions, pins `actions/upload-pages-artifact` and `actions/deploy-pages` to commit SHAs, and uploads `site/` as is.
- **Pages setting:** the owner sets the Pages source to "GitHub Actions". The agent asks first.
- **README:** keeps status, a three-line quick start, a link to the site and the contributor "Start here".

## Tests
Four seams, each test-first with hand-made mutation checks recorded in the PR:
1. **The checker:** `check_site(root) -> list[Problem]` over tiny sites in `tmp_path`, one problem each. One test runs it over the real `site/` and expects none.
2. **The generator:** `build(repo) -> {path: html}`. Each renderer is tested with a small input (a toy argparse parser, a two-route OpenAPI document, a three-term glossary), and the nav stamp is tested too. One drift test compares `build(repo)` with the committed `site/` and names the regenerate command.
3. **The page runner:** it pulls the `run` blocks out of a page and runs them. There is one test for the tutorial, and one for each offline how-to. Unit tests on tiny pages cover the extraction rules and the `…` match rules.
4. **The deploy workflow:** a unit test reads `pages.yml` and asserts SHA-pinned actions, the two write permissions only, and the triggers. The first real deploy is the end-to-end check.

Assertions are only on what each seam returns. Tests run with the venv's `python -m pytest` under a per-run timeout.

## Exit
The site is live on GitHub Pages from `main`. `check`, the drift test and the page-runner tests pass in CI. progress.md records a manual pass:
- one screenshot in light and one in dark
- a keyboard-only pass through the nav
- the tutorial followed by hand from the live site in under five minutes (spec 01)
