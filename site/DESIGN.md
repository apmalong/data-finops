# Site design note

Each rule of spec 07's "Design and writing" section, quoted as written, then how the site meets it. `tests/unit/test_site_design.py` fails if a rule is missing, reworded or unticked. Review every page against this list.

- [x] The dashboard's visual rules (spec 06): Tufte and Few, light and dark through `prefers-color-scheme`, a system font stack, muted colour used only to draw attention.
  `site.css` keeps the dashboard's muted palette: ink on the page, one link colour, and `--attention` only on a `.notice` the reader must not miss. Light and dark follow the system setting; `data-theme` on `<html>` forces one. The font stack is `system-ui`, so nothing is downloaded. Amount tables use right-aligned tabular figures and light rules.
- [x] `site.css` starts as a copy of the dashboard's tokens and evolves separately. Foreground and background token pairs reach at least 4.5:1 contrast in both themes.
  The tokens are the dashboard's, renamed by role (`--text*`, `--link`, `--bg-*`) so the checker can pair them. `python -m scripts.site check` computes WCAG contrast for every text token on every background token in each theme block and reports `low-contrast` under 4.5:1. The dashboard's light `--ink-3` (#75746f) is 4.44:1 on the page, so the site's `--text-3` is #6b6a65 (5.14:1).
- [x] Accessible: `lang`, one `<h1>`, ordered headings, alt text on every image, visible focus, and the nav usable by keyboard. Pages work without JavaScript.
  The checker requires `lang`, one `<h1>`, alt text on every image and balanced tags. Every page starts with a skip link to `<main id="main">`, and `:focus-visible` draws a 2px ring in the link colour. The nav is plain links in lists, so it is keyboard-usable with no script. The site has no JavaScript yet. Heading order is reviewed by eye.
- [x] No `http(s)://` in CSS or JS. Outbound links in page text are fine.
  The checker reports `external-url` for `http(s)://` or `//` in CSS and JS files, inline styles and scripts, and in any asset an element loads; outbound `<a>` links are allowed.
- [x] Diagrams are committed SVGs rendered from D2 sources in `docs/architecture/` (`d2 --layout elk`). There is no Mermaid.
  No page has a diagram yet. When one does, its D2 source goes in `docs/architecture/` and its SVG in `site/assets/`, and the checker requires the image to exist and have alt text.
- [x] Writing: glossary terms capitalised as in GLOSSARY, never their avoided synonyms. Short declarative sentences. Explanation pages cite their ADRs and specs, which stay authoritative. The only account ID is the placeholder `123456789012`. No real account IDs, bucket names, credentials or SQL literals appear.
  Reviewed for each page against GLOSSARY.md. The checker reports `account-id` for any 12-digit number other than `123456789012`, without echoing it.
