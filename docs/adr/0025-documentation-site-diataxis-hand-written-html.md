# ADR 0025: The documentation site follows Diátaxis, is hand-written HTML and is deployed as committed

Status: Accepted (design, 2026-11-05). Decides #135; refines ADR 0009 and ADR 0014 for documentation.

## Context
Operators (data engineers, FinOps practitioners) have only the README and the demo walkthrough (`examples/demo/README.md`). The repository's docs (specs, plan, progress, research, ADRs) are written for contributors and agents. Users need tutorials, how-to guides, reference and explanation that stay true as the code changes. The dashboard already shows that a static page with no libraries, no CDN and a written design checklist works here (spec 06). Mermaid diagrams in `docs/architecture/` render only on GitHub.

## Decision
1. The site is organised by [Diátaxis](https://diataxis.fr/): tutorials, how-to guides, reference and explanation. Each page is exactly one of the four types.
2. Pages are hand-written HTML, CSS and optional JavaScript in a top-level `site/` directory, one page per document. No static-site generator. `site/` is deployed exactly as committed, so every page opens from a checkout.
3. One stdlib-only tool in `scripts/site/` (dev tooling, outside the wheel, linted and type-checked) does three things. It generates the reference pages (CLI from argparse, HTTP API from `schemas/openapi.json`, data contracts from `schemas/`, Glossary from `GLOSSARY.md`). It stamps the shared nav from `site/nav.json` between markers in every page. It checks the site. A drift test fails when a committed page differs from what the tool would write.
4. No external assets: no CDN, no web fonts, no diagram or API-viewer JavaScript. Diagrams are committed SVGs rendered from D2. Pages work without JavaScript. Every link is relative, so the site works from `file://` and from the Pages subpath.
5. Runnable commands in the tutorial and offline how-to guides are marked in the HTML and run by a test, with marked output compared. The site tutorial replaces `examples/demo/README.md` as the walkthrough's source.
6. Specs, ADRs, plan, progress and research stay in the repository and stay authoritative. Explanation pages are written for users and cite them. A Contributing page links to them.
7. One site is built from `main` with no versions until a PyPI release. GitHub Actions deploys it to GitHub Pages on push to `main`.
8. Every ticket names the documentation type it changes, or none (AGENTS.md).

## Consequences
No docs dependency joins the build-vs-reuse register, and the site keeps the dashboard's design rules. Writing raw HTML is slower than Markdown, and the generator, nav stamping and checker are code the project owns and tests. The checker's rules, not a W3C validator, define a "valid" page. Generated reference and the tutorial can't drift from the code without failing CI.

## Alternatives rejected
- MkDocs with Material, Sphinx or Docusaurus: a toolchain and a theme the project does not control, with Markdown that can't carry the dashboard's design rules. The owner chose hand-written HTML (#135).
- One long page: about 25 documents mixing four Diátaxis types, with no stable URL per document.
- A nav built by JavaScript: breaks without JavaScript. Hand-copied nav: drifts.
- Mermaid or a vendored Mermaid library: a large runtime dependency for diagrams D2 already renders to SVG.
- Swagger UI or ReDoc from a CDN for the HTTP API: an external asset. The reference page is generated from the same OpenAPI document instead.
- The W3C Nu validator or external link checking in CI: Java and a network in CI for little gain at this size.

## Revisit triggers
More than about 50 pages, or readers need search. A PyPI release needs versioned docs. Contributors find hand-written HTML a barrier to writing docs.
