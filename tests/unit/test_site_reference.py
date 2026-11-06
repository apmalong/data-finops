"""The generated reference pages (spec 07, "The site tool"): each renderer on a small input."""

import argparse

import pytest

from scripts.site.nav import SiteError
from scripts.site.reference import render_cli, render_contracts, render_glossary, render_http


def toy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool", description="Do <things>.")
    commands = parser.add_subparsers(required=True, metavar="command")
    ingest = commands.add_parser("ingest", help="load one input")
    kinds = ingest.add_subparsers(required=True, metavar="kind")
    focus = kinds.add_parser("focus", help="a FOCUS export & more")
    focus.add_argument("path", metavar="PATH", help="the file")
    focus.add_argument("--db", required=True, help="the database file")
    focus.add_argument("--data-dir", metavar="DIR", help="where the data files are")
    serve = commands.add_parser("serve", help="serve the API")
    serve.add_argument("-p", "--port", type=int, default=8000, help="the port")
    serve.add_argument("--secret", help=argparse.SUPPRESS)
    return parser


def test_the_cli_reference_lists_every_command_with_its_arguments() -> None:
    html = render_cli(toy_parser())
    assert '<h2 id="ingest-focus"><code>tool ingest focus</code></h2>' in html
    assert '<h2 id="serve"><code>tool serve</code></h2>' in html
    assert "<p>a FOCUS export &amp; more</p>" in html
    assert "usage: tool ingest focus [-h] --db DB [--data-dir DIR] PATH" in html
    focus = html[html.index('id="ingest-focus"') : html.index('id="serve"')]
    assert "<td><code>PATH</code></td><td>required</td><td></td><td>the file</td>" in focus
    assert "<td><code>--db DB</code></td><td>required</td><td></td>" in focus
    assert "<td><code>--data-dir DIR</code></td><td>optional</td><td></td>" in focus
    serve = html[html.index('id="serve"') :]
    assert "<td><code>--port PORT</code></td><td>optional</td><td>8000</td>" in serve
    assert "--secret" not in html  # hidden options are not documented
    assert "--help" not in focus.split("usage:")[1].split("</pre>")[1]  # -h is not a row


OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/v1/things": {
            "get": {
                "summary": "Things",
                "description": "One page of things.",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {
                            "type": "integer",
                            "maximum": 1000,
                            "minimum": 1,
                            "default": 100,
                        },
                        "description": "At most this many",
                    },
                    {
                        "name": "source",
                        "in": "query",
                        "schema": {
                            "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Successful Response",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Page"}}
                        },
                    },
                    "400": {
                        "description": "Bad Request",
                        "content": {
                            "application/problem+json": {
                                "schema": {"$ref": "#/components/schemas/Problem"}
                            }
                        },
                    },
                },
            }
        },
        "/api/v1/things/{ref}": {
            "post": {
                "summary": "Add a thing",
                "parameters": [
                    {"name": "ref", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {}}}},
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
}


def test_the_http_reference_lists_every_route_its_parameters_and_responses() -> None:
    html = render_http(OPENAPI)
    assert '<h2 id="get-v1-things"><code>GET /v1/things</code>: Things</h2>' in html
    assert (
        '<h2 id="post-api-v1-things-ref"><code>POST /api/v1/things/{ref}</code>: Add a thing</h2>'
        in html
    )
    assert "<p>One page of things.</p>" in html
    things = html[: html.index('id="post-')]
    assert (
        "<tr><td><code>limit</code></td><td>query</td><td>optional</td>"
        "<td>integer, 1 to 1000, default 100</td><td>At most this many</td></tr>"
    ) in things
    assert (
        "<td><code>source</code></td><td>query</td><td>optional</td>"
        "<td>string, at most 128 characters, or null</td>" in things
    )
    assert (
        "<tr><td>200</td><td>Successful Response</td>"
        "<td><code>application/json</code>: Page</td></tr>" in things
    )
    assert (
        "<tr><td>400</td><td>Bad Request</td><td><code>application/problem+json</code>: "
        "Problem Details (RFC 9457)</td></tr>" in things
    )
    post = html[html.index('id="post-') :]
    assert "<td><code>ref</code></td><td>path</td><td>required</td><td>string</td>" in post
    assert "<p>Request body: <code>application/json</code>, required.</p>" in post


THING = {
    "title": "Thing",
    "description": "One <thing>, exactly.",
    "type": "object",
    "required": ["amount", "kind"],
    "properties": {
        "amount": {"type": "string", "pattern": "^-?[0-9]+$", "description": "A Decimal string"},
        "kind": {"$ref": "#/$defs/Kind"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "$defs": {"Kind": {"type": "string", "enum": ["a", "b"], "description": "Which kind."}},
}


def test_the_contracts_reference_lists_each_schema_its_fields_and_definitions() -> None:
    html = render_contracts({"thing.schema.json": THING})
    assert '<h2 id="thing"><code>thing.schema.json</code>: Thing</h2>' in html
    assert "<p>One &lt;thing&gt;, exactly.</p>" in html
    assert (
        "<tr><td><code>amount</code></td><td>required</td>"
        "<td>string, matching ^-?[0-9]+$</td><td>A Decimal string</td></tr>"
    ) in html
    assert "<tr><td><code>kind</code></td><td>required</td><td>Kind</td><td></td></tr>" in html
    assert (
        "<tr><td><code>tags</code></td><td>optional</td><td>list of string</td><td></td></tr>"
        in html
    )
    assert '<h3 id="thing-kind">Kind</h3>' in html
    assert "<p>Which kind. One of a, b.</p>" in html


GLOSSARY = """# Data FinOps

Attributes cloud charges & more.

## Billing

**Billing Line**:
One FOCUS row, with `BilledCost`.
_Avoid_: bill row, invoice line

**Export Snapshot**:
Every Billing Line published at one time.

## Allocation

**Unallocated**:
Money no policy assigns <yet>.
_Avoid_: unknown

## Flagged ambiguities

- "Query cost" implies an exact price.
- `no_evidence` names two things.
"""


def test_the_glossary_lists_each_term_by_area_with_an_anchor_and_its_avoided_synonyms() -> None:
    html = render_glossary(GLOSSARY)
    assert "<p>Attributes cloud charges &amp; more.</p>" in html
    assert '<h2 id="area-billing">Billing</h2>' in html
    assert (
        '<dt id="billing-line">Billing Line</dt>\n'
        "<dd>One FOCUS row, with <code>BilledCost</code>.</dd>\n"
        '<dd class="avoid">Avoid: bill row, invoice line</dd>'
    ) in html
    assert (
        '<dt id="export-snapshot">Export Snapshot</dt>\n'
        "<dd>Every Billing Line published at one time.</dd>\n</dl>" in html
    )
    assert '<h2 id="area-allocation">Allocation</h2>' in html
    assert "<dd>Money no policy assigns &lt;yet&gt;.</dd>" in html
    assert '<h2 id="area-flagged-ambiguities">Flagged ambiguities</h2>' in html
    assert "<li><code>no_evidence</code> names two things.</li>" in html


@pytest.mark.parametrize(
    ("broken", "line"),
    [
        ("**Orphan**:\n\n", 16),  # a term with no definition
        ("A stray sentence.\n\n", 16),  # text that belongs to no term
        ("- a bullet outside Flagged ambiguities\n\n", 16),
        ("**Term**: inline definition\n\n", 16),  # not the format
    ],
    ids=["term without definition", "stray text", "stray bullet", "inline definition"],
)
def test_a_glossary_line_outside_the_format_fails_naming_it(broken: str, line: int) -> None:
    text = GLOSSARY.replace("## Allocation\n\n", "## Allocation\n\n" + broken, 1)
    with pytest.raises(SiteError, match=f"GLOSSARY.md:{line}:"):
        render_glossary(text)


def test_flagged_ambiguities_hold_only_bullets() -> None:
    text = GLOSSARY.replace('- "Query cost"', 'A sentence.\n- "Query cost"', 1)
    with pytest.raises(SiteError, match="GLOSSARY.md:22:"):
        render_glossary(text)
