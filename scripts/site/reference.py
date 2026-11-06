"""Render the generated reference pages from their sources (spec 07, "The site tool").

Each renderer is a pure function from its source to the HTML of a page's `reference` region:
the CLI from its argparse parser, the HTTP API from the OpenAPI document, the data contracts
from the JSON schemas, and the Glossary from GLOSSARY.md. Stdlib only.
"""

import argparse
import re
from collections.abc import Iterator
from html import escape
from typing import Any

from scripts.site.nav import SiteError


def render_cli(parser: argparse.ArgumentParser) -> str:
    """Every command, with its usage and its arguments; hidden options are left out."""
    sections = []
    for path, command, summary in _commands(parser, []):
        rows = "\n".join(_argument_row(a) for a in _documented(command))
        sections.append(
            f'<h2 id="{"-".join(path)}"><code>{escape(command.prog)}</code></h2>\n'
            f"<p>{escape(summary)}</p>\n"
            f"<pre><code>{escape(command.format_usage().strip())}</code></pre>\n"
            "<table>\n<thead><tr><th>Argument</th><th>Required</th><th>Default</th>"
            f"<th>Meaning</th></tr></thead>\n<tbody>\n{rows}\n</tbody>\n</table>"
        )
    return "\n".join(sections)


def _commands(
    parser: argparse.ArgumentParser, path: list[str]
) -> Iterator[tuple[list[str], argparse.ArgumentParser, str]]:
    """Each leaf command under `parser`: its path of names, its parser and its help."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                sub = action.choices[choice.dest]
                if any(isinstance(a, argparse._SubParsersAction) for a in sub._actions):
                    yield from _commands(sub, [*path, choice.dest])
                else:
                    yield [*path, choice.dest], sub, choice.help or ""


def _documented(command: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        a
        for a in command._actions
        if not isinstance(a, argparse._HelpAction) and a.help != argparse.SUPPRESS
    ]


def _argument_row(action: argparse.Action) -> str:
    if action.option_strings:
        flag = max(action.option_strings, key=len)
        metavar = action.metavar or action.dest.upper()
        name = flag if action.nargs == 0 else f"{flag} {metavar}"
        required = action.required
    else:
        name = str(action.metavar or action.dest)
        required = action.nargs not in ("?", "*")
    default = action.default
    shown = "" if default in (None, False, argparse.SUPPRESS) else escape(str(default))
    return (
        f"<tr><td><code>{escape(name)}</code></td>"
        f"<td>{'required' if required else 'optional'}</td>"
        f"<td>{shown}</td><td>{escape(action.help or '')}</td></tr>"
    )


_METHODS = ("get", "post", "put", "patch", "delete")


def render_http(openapi: dict[str, Any]) -> str:
    """Every route: its summary and description, parameters, request body and responses."""
    sections = []
    for path, operations in openapi.get("paths", {}).items():
        for method in _METHODS:
            if method not in operations:
                continue
            operation = operations[method]
            anchor = re.sub(r"[^a-z0-9]+", "-", f"{method} {path}".lower()).strip("-")
            parts = [
                f'<h2 id="{anchor}"><code>{method.upper()} {escape(path)}</code>: '
                f"{escape(operation.get('summary', ''))}</h2>"
            ]
            if operation.get("description"):
                parts.append(f"<p>{escape(' '.join(operation['description'].split()))}</p>")
            if operation.get("parameters"):
                rows = "\n".join(_parameter_row(p) for p in operation["parameters"])
                parts.append(
                    "<table>\n<thead><tr><th>Parameter</th><th>In</th><th>Required</th>"
                    f"<th>Type</th><th>Meaning</th></tr></thead>\n<tbody>\n{rows}\n</tbody>\n"
                    "</table>"
                )
            if body := operation.get("requestBody"):
                types = ", ".join(f"<code>{escape(t)}</code>" for t in body.get("content", {}))
                need = "required" if body.get("required") else "optional"
                parts.append(f"<p>Request body: {types}, {need}.</p>")
            rows = "\n".join(
                _response_row(code, response)
                for code, response in operation.get("responses", {}).items()
            )
            parts.append(
                "<table>\n<thead><tr><th>Status</th><th>Meaning</th><th>Body</th></tr></thead>\n"
                f"<tbody>\n{rows}\n</tbody>\n</table>"
            )
            sections.append("\n".join(parts))
    return "\n".join(sections)


def _parameter_row(parameter: dict[str, Any]) -> str:
    required = "required" if parameter.get("required") else "optional"
    return (
        f"<tr><td><code>{escape(parameter['name'])}</code></td>"
        f"<td>{escape(parameter.get('in', ''))}</td><td>{required}</td>"
        f"<td>{escape(describe(parameter.get('schema', {})))}</td>"
        f"<td>{escape(parameter.get('description', ''))}</td></tr>"
    )


def _response_row(code: str, response: dict[str, Any]) -> str:
    bodies = []
    for media, content in response.get("content", {}).items():
        if media == "application/problem+json":
            what = "Problem Details (RFC 9457)"
        else:
            what = describe(content.get("schema", {}))
        bodies.append(
            f"<code>{escape(media)}</code>: {escape(what)}"
            if what
            else f"<code>{escape(media)}</code>"
        )
    return (
        f"<tr><td>{escape(code)}</td><td>{escape(response.get('description', ''))}</td>"
        f"<td>{', '.join(bodies)}</td></tr>"
    )


def describe(schema: dict[str, Any]) -> str:
    """A short reading of a JSON Schema: its type, bounds, choices and default."""
    text: str
    if "$ref" in schema:
        text = str(schema["$ref"]).rsplit("/", 1)[-1]
    elif "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema["oneOf"]
        named = [describe(o) for o in options if o.get("type") != "null"]
        text = " or ".join(named)
        if len(named) < len(options):
            text += ", or null"
    elif "enum" in schema:
        text = "one of " + ", ".join(str(v) for v in schema["enum"])
    elif "const" in schema:
        text = f"always {schema['const']}"
    else:
        kind = schema.get("type", "")
        if isinstance(kind, list):
            kind = " or ".join(kind)
        if kind == "array":
            kind = f"list of {describe(schema.get('items', {})) or 'items'}"
        bounds = []
        if "minimum" in schema and "maximum" in schema:
            bounds.append(f"{schema['minimum']} to {schema['maximum']}")
        elif "maximum" in schema:
            bounds.append(f"at most {schema['maximum']}")
        elif "minimum" in schema:
            bounds.append(f"at least {schema['minimum']}")
        if "maxLength" in schema:
            bounds.append(f"at most {schema['maxLength']} characters")
        if "format" in schema:
            bounds.append(str(schema["format"]))
        if "pattern" in schema:
            bounds.append(f"matching {schema['pattern']}")
        text = ", ".join([str(kind), *bounds]) if kind else ", ".join(bounds)
    if "default" in schema and schema["default"] is not None:
        text += f", default {schema['default']}"
    return text


SCHEMA_SOURCE = "https://github.com/apmalong/data-finops/blob/main/schemas/"


def render_contracts(schemas: dict[str, dict[str, Any]]) -> str:
    """Each JSON schema: its fields, then its definitions, keyed by the schema's file name."""
    sections = []
    for filename, schema in schemas.items():
        stem = filename.removesuffix(".schema.json")
        parts = [
            f'<h2 id="{_slug(stem)}"><code>{escape(filename)}</code>: '
            f"{escape(schema.get('title', stem))}</h2>"
        ]
        if schema.get("description"):
            parts.append(f"<p>{escape(' '.join(schema['description'].split()))}</p>")
        parts.append(_fields(schema))
        if "allOf" in schema:
            parts.append(
                "<p>The schema also has conditional rules between fields; "
                f'<a href="{SCHEMA_SOURCE}{escape(filename)}">the schema file</a> is '
                "authoritative.</p>"
            )
        for name, definition in schema.get("$defs", {}).items():
            parts.append(f'<h3 id="{_slug(stem)}-{_slug(name)}">{escape(name)}</h3>')
            sentences = [" ".join(definition.get("description", "").split())]
            if "enum" in definition:
                sentences.append("One of " + ", ".join(str(v) for v in definition["enum"]) + ".")
            text = " ".join(s for s in sentences if s)
            if text:
                parts.append(f"<p>{escape(text)}</p>")
            if "properties" in definition:
                parts.append(_fields(definition))
        sections.append("\n".join(parts))
    return "\n".join(sections)


def _fields(schema: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    rows = "\n".join(
        f"<tr><td><code>{escape(name)}</code></td>"
        f"<td>{'required' if name in required else 'optional'}</td>"
        f"<td>{escape(describe(field))}</td>"
        f"<td>{escape(' '.join(field.get('description', '').split()))}</td></tr>"
        for name, field in schema.get("properties", {}).items()
    )
    return (
        "<table>\n<thead><tr><th>Field</th><th>Required</th><th>Type</th><th>Meaning</th></tr>"
        f"</thead>\n<tbody>\n{rows}\n</tbody>\n</table>"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text).lower()).strip(
        "-"
    )


_TERM = re.compile(r"\*\*([^*]+)\*\*:")
_FLAGGED = "Flagged ambiguities"


def render_glossary(markdown: str) -> str:
    """GLOSSARY.md, read by its fixed format: a title, intro paragraphs, then `## Area`s of
    `**Term**:` lines, each followed by its one-line definition and an optional `_Avoid_:` line,
    and a closing `## Flagged ambiguities` list. Any other line raises SiteError naming it."""
    out: list[str] = []
    area = ""
    in_list = False  # a <dl> or <ul> is open
    lines = markdown.split("\n")
    number = 0
    while number < len(lines):
        text = lines[number]
        number += 1
        if not text.strip() or (number == 1 and text.startswith("# ")):
            continue
        if text.startswith("## "):
            if in_list:
                out.append("</ul>" if area == _FLAGGED else "</dl>")
            area = text[3:].strip()
            out.append(f'<h2 id="area-{_slug(area)}">{escape(area)}</h2>')  # terms keep plain ids
            out.append("<ul>" if area == _FLAGGED else "<dl>")
            in_list = True
        elif not area:
            out.append(f"<p>{_inline(text)}</p>")
        elif area == _FLAGGED:
            if not text.startswith("- "):
                raise SiteError(f"GLOSSARY.md:{number}: {_FLAGGED} holds only '- ' bullets")
            out.append(f"<li>{_inline(text[2:])}</li>")
        elif term := _TERM.fullmatch(text):
            definition = lines[number] if number < len(lines) else ""
            if not definition.strip() or definition.startswith(("**", "_Avoid_", "- ", "#")):
                raise SiteError(
                    f"GLOSSARY.md:{number}: a term needs its definition on the next line"
                )
            name = term.group(1)
            out.append(f'<dt id="{_slug(name)}">{escape(name)}</dt>')
            out.append(f"<dd>{_inline(definition)}</dd>")
            number += 1
            if number < len(lines) and lines[number].startswith("_Avoid_: "):
                avoid = lines[number].removeprefix("_Avoid_: ")
                out.append(f'<dd class="avoid">Avoid: {_inline(avoid)}</dd>')
                number += 1
        else:
            raise SiteError(
                f"GLOSSARY.md:{number}: not part of the format (a '**Term**:' line, its "
                "definition, then an optional '_Avoid_:' line)"
            )
    if in_list:
        out.append("</ul>" if area == _FLAGGED else "</dl>")
    return "\n".join(out)


def _inline(text: str) -> str:
    """Markdown text as HTML: escaped, with `code` spans."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text.strip()))
