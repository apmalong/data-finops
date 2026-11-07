"""Render the generated reference pages from their sources (spec 07, "The site tool").

Each renderer is a pure function from its source to the HTML of a page's `reference` region:
the CLI from its argparse parser, the HTTP API from the OpenAPI document, the data contracts
from the JSON schemas, the Glossary from GLOSSARY.md, and the deployment settings from the
stack's source. Stdlib only.
"""

import argparse
import ast
import json
import re
from collections.abc import Iterator
from html import escape
from typing import Any

from scripts.site.nav import SiteError

SCHEMA_SOURCE = "https://github.com/apmalong/data-finops/blob/main/schemas/"


def render_cli(parser: argparse.ArgumentParser) -> str:
    """Every command group and command, with its usage and arguments; hidden options left out."""
    sections = [f"<p>{escape(_text(parser.description))}</p>"] if parser.description else []
    for path, command, summary, subcommands in _commands(parser, []):
        parts = [
            f'<h2 id="{_slug("-".join(path))}"><code>{escape(command.prog)}</code></h2>',
            f"<p>{escape(summary)}</p>" if summary else "",
        ]
        if subcommands:
            links = ", ".join(
                f'<a href="#{_slug("-".join([*path, name]))}"><code>{escape(prog)}</code></a>'
                for name, prog in subcommands
            )
            parts.append(f"<p>Commands: {links}.</p>")
        else:
            parts.append(
                f'<pre class="usage"><code>{escape(command.format_usage().strip())}</code></pre>'
            )
            rows = [_argument_row(a) for a in _documented(command)]
            parts.append(_table(["Argument", "Required", "Default", "Meaning"], rows))
        sections.append("\n".join(p for p in parts if p))
    return "\n".join(sections)


def _commands(
    parser: argparse.ArgumentParser, path: list[str]
) -> Iterator[tuple[list[str], argparse.ArgumentParser, str, list[tuple[str, str]]]]:
    """Each command under `parser`, a group before its commands: path, parser, help, and the
    group's commands as (name, prog). Every choice is listed, with help or without."""
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {choice.dest: choice.help or "" for choice in action._choices_actions}
        for name, sub in action.choices.items():
            sub_path = [*path, name]
            group = next(
                (a for a in sub._actions if isinstance(a, argparse._SubParsersAction)), None
            )
            children = [(n, p.prog) for n, p in group.choices.items()] if group else []
            yield sub_path, sub, helps.get(name, ""), children
            if group:
                yield from _commands(sub, sub_path)


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
    meaning = action.help or ""
    if action.choices is not None:
        meaning += " One of " + ", ".join(str(c) for c in action.choices) + "."
    return (
        f"<tr><td><code>{escape(name)}</code></td>"
        f"<td>{'required' if required else 'optional'}</td>"
        f"<td>{shown}</td><td>{escape(meaning.strip())}</td></tr>"
    )


_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_PATH_KEYS = {"summary", "description", "servers", "parameters", "$ref"}  # not operations


def render_http(openapi: dict[str, Any]) -> str:
    """Every route, then every schema the routes use, each with an anchor."""
    components: dict[str, Any] = openapi.get("components", {}).get("schemas", {})
    sections = []
    for path, operations in openapi.get("paths", {}).items():
        if unknown := sorted(set(operations) - set(_METHODS) - _PATH_KEYS):
            raise SiteError(f"openapi.json: {path} has keys that are not HTTP methods: {unknown}")
        for method in _METHODS:
            if method in operations:
                sections.append(_operation(path, method, operations[method], components))
    if components:
        sections.append('<h2 id="schemas">Schemas</h2>')
        for name, schema in components.items():
            sections.append(_definition(f"schema-{_slug(name)}", name, schema, components))
    return "\n".join(sections)


def _operation(path: str, method: str, operation: dict[str, Any], defs: dict[str, Any]) -> str:
    parts = [
        f'<h2 id="{_slug(f"{method} {path}")}"><code>{method.upper()} {escape(path)}</code>: '
        f"{escape(operation.get('summary', ''))}</h2>"
    ]
    if operation.get("description"):
        parts.append(f"<p>{escape(_text(operation['description']))}</p>")
    if operation.get("parameters"):
        rows = [_parameter_row(p, defs) for p in operation["parameters"]]
        parts.append(_table(["Parameter", "In", "Required", "Type", "Meaning"], rows))
    if body := operation.get("requestBody"):
        types = ", ".join(f"<code>{escape(t)}</code>" for t in body.get("content", {}))
        need = "required" if body.get("required") else "optional"
        parts.append(f"<p>Request body: {types}, {need}.</p>")
    rows = [_response_row(code, r, defs) for code, r in operation.get("responses", {}).items()]
    parts.append(_table(["Status", "Meaning", "Body"], rows))
    return "\n".join(parts)


def _parameter_row(parameter: dict[str, Any], defs: dict[str, Any]) -> str:
    required = "required" if parameter.get("required") else "optional"
    return (
        f"<tr><td><code>{escape(parameter['name'])}</code></td>"
        f"<td>{escape(parameter.get('in', ''))}</td><td>{required}</td>"
        f"<td>{escape(describe(parameter.get('schema', {}), defs))}</td>"
        f"<td>{escape(parameter.get('description', ''))}</td></tr>"
    )


def _response_row(code: str, response: dict[str, Any], defs: dict[str, Any]) -> str:
    bodies = []
    for media, content in response.get("content", {}).items():
        schema = content.get("schema", {})
        if media == "application/problem+json":
            what = "Problem Details (RFC 9457)"
        elif "$ref" in schema and (name := _ref_name(schema)) in defs:
            what = f'<a href="#schema-{_slug(name)}">{escape(name)}</a>'
        else:
            what = escape(describe(schema, defs))
        media_html = f"<code>{escape(media)}</code>"
        bodies.append(f"{media_html}: {what}" if what else media_html)
    return (
        f"<tr><td>{escape(code)}</td><td>{escape(response.get('description', ''))}</td>"
        f"<td>{', '.join(bodies)}</td></tr>"
    )


def describe(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> str:
    """A short reading of a JSON Schema: its type, bounds, choices and default. A reference to
    an enum in `defs` names the enum and spells out its values."""
    text: str
    if "$ref" in schema:
        name = _ref_name(schema)
        target = (defs or {}).get(name, {})
        text = f"{name}: {describe({'enum': target['enum']})}" if "enum" in target else name
    elif "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf", schema.get("oneOf", []))
        named = [describe(o, defs) for o in options if o.get("type") != "null"]
        text = " or ".join(named)
        if len(named) < len(options):
            text += ", or null"
    elif "allOf" in schema:
        text = " and ".join(describe(part, defs) for part in schema["allOf"])
    elif "enum" in schema:
        text = "one of " + ", ".join(json.dumps(v) for v in schema["enum"])
    elif "const" in schema:
        text = f"always {json.dumps(schema['const'])}"
    else:
        kind = schema.get("type", "")
        if isinstance(kind, list):
            kind = " or ".join(kind)
        if kind == "array":
            kind = f"list of {describe(schema.get('items', {}), defs) or 'items'}"
        elif kind == "object" and isinstance(schema.get("additionalProperties"), dict):
            kind = f"object of {describe(schema['additionalProperties'], defs)}"
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
    if schema.get("default") is not None:
        text += f", default {json.dumps(schema['default'])}"
    return text


def render_contracts(schemas: dict[str, dict[str, Any]]) -> str:
    """Each JSON schema: its fields, then its definitions, keyed by the schema's file name."""
    sections = []
    for filename, schema in schemas.items():
        stem = filename.removesuffix(".schema.json")
        defs = schema.get("$defs", {})
        parts = [
            f'<h2 id="{_slug(stem)}"><code>{escape(filename)}</code>: '
            f"{escape(schema.get('title', stem))}</h2>"
        ]
        if schema.get("description"):
            parts.append(f"<p>{escape(_text(schema['description']))}</p>")
        parts.append(_fields(schema, defs))
        if "allOf" in schema:
            parts.append(
                "<p>The schema also has conditional rules between fields; "
                f'<a href="{SCHEMA_SOURCE}{escape(filename)}">the schema file</a> is '
                "authoritative.</p>"
            )
        for name, definition in defs.items():
            parts.append(_definition(f"{_slug(stem)}-{_slug(name)}", name, definition, defs))
        sections.append("\n".join(parts))
    return "\n".join(sections)


def _definition(anchor: str, name: str, definition: dict[str, Any], defs: dict[str, Any]) -> str:
    """One named definition: an enum spelled out, or an object's fields."""
    parts = [f'<h3 id="{anchor}">{escape(name)}</h3>']
    sentences = [_text(definition.get("description", ""))]
    if "enum" in definition:
        sentences.append("One of " + ", ".join(str(v) for v in definition["enum"]) + ".")
    if text := " ".join(s for s in sentences if s):
        parts.append(f"<p>{escape(text)}</p>")
    if "properties" in definition:
        parts.append(_fields(definition, defs))
    return "\n".join(parts)


def _fields(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    rows = [
        f"<tr><td><code>{escape(name)}</code></td>"
        f"<td>{'required' if name in required else 'optional'}</td>"
        f"<td>{escape(describe(field, defs))}</td>"
        f"<td>{escape(_text(field.get('description', '')))}</td></tr>"
        for name, field in schema.get("properties", {}).items()
    ]
    return _table(["Field", "Required", "Type", "Meaning"], rows)


_TERM = re.compile(r"\*\*([^*]+)\*\*:")
_FLAGGED = "Flagged ambiguities"


def render_glossary(markdown: str) -> str:
    """GLOSSARY.md, read by its fixed format: a title, intro paragraphs, then `## Area`s of
    `**Term**:` lines, each followed by its one-line definition and an optional `_Avoid_:` line,
    and a closing `## Flagged ambiguities` list. Any other line raises SiteError naming it."""
    out: list[str] = []
    area = ""
    lines = markdown.split("\n")
    number = 0
    while number < len(lines):
        text = lines[number]
        number += 1
        if not text.strip() or (number == 1 and text.startswith("# ")):
            continue
        if text.startswith("## "):
            if area:
                out.append(_close(area))
            area = text[3:].strip()
            out.append(f'<h2 id="area-{_slug(area)}">{escape(area)}</h2>')  # terms keep plain ids
            out.append("<ul>" if area == _FLAGGED else "<dl>")
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
            out.append(f'<dt id="{_slug(term.group(1))}">{escape(term.group(1))}</dt>')
            out.append(f"<dd>{_inline(definition)}</dd>")
            number += 1
            if number < len(lines) and lines[number].startswith("_Avoid_: "):
                out.append(f'<dd class="avoid">Avoid: {_inline(lines[number][9:])}</dd>')
                number += 1
        else:
            raise SiteError(
                f"GLOSSARY.md:{number}: not part of the format (a '**Term**:' line, its "
                "definition, then an optional '_Avoid_:' line)"
            )
    if area:
        out.append(_close(area))
    return "\n".join(out)


def render_deployment_settings(settings_source: str, program_source: str) -> str:
    """The Deployment's settings and stack outputs (spec 08), read from the source of the
    stack's `deployment/settings.py` and `deployment/program.py` without importing them: the
    site tool stays stdlib-only, and the root environment has no Pulumi.

    Each `Settings` field gives one row: its key from `CONFIG_KEYS`, its default, its
    docstring, and its bounds from `BOUNDS`. A field whose keys are `field.sub` entries (the
    Budget) gives one row per field of its own dataclass. The outputs come from `OUTPUTS`.
    Anything the reference cannot document raises SiteError naming it."""
    module = ast.parse(settings_source)
    constants = _literals(module)
    keys: dict[str, str] = dict(constants.get("CONFIG_KEYS", {}))
    bounds: dict[str, tuple[int, int]] = constants.get("BOUNDS", {})
    classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
    if "Settings" not in classes:
        raise SiteError("deployment/settings.py has no Settings class")
    rows = []
    for name, annotation, default, doc in _dataclass_fields(classes["Settings"], constants):
        if name in keys:
            meaning = doc
            if name in bounds:
                least, most = bounds[name]
                meaning += f" A whole number from {least} to {most}."
            rows.append(_setting_row(keys.pop(name), default, meaning))
            continue
        nested = next((n for n in _names(annotation) if n in classes), None)
        if nested is None or not any(key.startswith(f"{name}.") for key in keys):
            raise SiteError(f"Settings.{name} has no configuration key in CONFIG_KEYS")
        for sub, _, _, sub_doc in _dataclass_fields(classes[nested], constants):
            if f"{name}.{sub}" not in keys:
                raise SiteError(f"{nested}.{sub} has no configuration key in CONFIG_KEYS")
            rows.append(_setting_row(keys.pop(f"{name}.{sub}"), default, sub_doc))
    for extra in keys:
        raise SiteError(f"CONFIG_KEYS names {extra}, which is not a setting")
    outputs = _literals(ast.parse(program_source)).get("OUTPUTS")
    if not outputs:
        raise SiteError("deployment/program.py has no OUTPUTS")
    output_rows = [
        f"<tr><td><code>{escape(name)}</code></td><td>{_inline(_text(meaning))}</td></tr>"
        for name, meaning in outputs.items()
    ]
    return "\n".join(
        [
            '<h2 id="settings">Settings</h2>',
            _table(["Key", "Default", "Meaning"], rows),
            '<h2 id="outputs">Outputs</h2>',
            _table(["Output", "Meaning"], output_rows),
        ]
    )


_NO_DEFAULT = object()


def _literals(module: ast.Module) -> dict[str, Any]:
    """The module's top-level names bound to a literal, by their value."""
    found: dict[str, Any] = {}
    for node in module.body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and value is not None:
            try:
                found[target.id] = ast.literal_eval(value)
            except ValueError:
                continue
    return found


def _dataclass_fields(
    cls: ast.ClassDef, constants: dict[str, Any]
) -> Iterator[tuple[str, ast.expr, Any, str]]:
    """Each annotated field of a dataclass: its name, annotation, default and docstring."""
    body = cls.body
    for index, node in enumerate(body):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        name = node.target.id
        after = body[index + 1] if index + 1 < len(body) else None
        if not (
            isinstance(after, ast.Expr)
            and isinstance(after.value, ast.Constant)
            and isinstance(after.value.value, str)
        ):
            raise SiteError(f"{cls.name}.{name} has no docstring")
        if node.value is None:
            default: Any = _NO_DEFAULT
        elif isinstance(node.value, ast.Name) and node.value.id in constants:
            default = constants[node.value.id]
        else:
            try:
                default = ast.literal_eval(node.value)
            except ValueError:
                raise SiteError(
                    f"{cls.name}.{name} has a default the reference cannot read"
                ) from None
        yield name, node.annotation, default, after.value.value


def _names(annotation: ast.expr) -> list[str]:
    return [node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)]


def _setting_row(key: str, default: Any, meaning: str) -> str:
    if default is _NO_DEFAULT:
        shown = "required"
    elif default is None:
        shown = "unset"
    elif default == "":
        shown = "empty"
    else:
        value = str(default).lower() if isinstance(default, bool) else str(default)
        shown = f"<code>{escape(value)}</code>"
    return (
        f"<tr><td><code>{escape(key)}</code></td><td>{shown}</td>"
        f"<td>{_inline(_text(meaning))}</td></tr>"
    )


def _close(area: str) -> str:
    return "</ul>" if area == _FLAGGED else "</dl>"


def _inline(text: str) -> str:
    """Markdown text as HTML: escaped, with `code` spans."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text.strip()))


def _table(headers: list[str], rows: list[str]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "\n".join(rows)
    # A wide table scrolls in its own box, which keyboard users can focus, not the whole page.
    return (
        '<div class="table-scroll" tabindex="0" role="region"'
        ' aria-label="Table, scrolls sideways">\n'
        f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>\n</div>"
    )


def _text(text: str | None) -> str:
    """Prose from a source with its line breaks and runs of spaces collapsed."""
    return " ".join((text or "").split())


def _ref_name(schema: dict[str, Any]) -> str:
    return str(schema["$ref"]).rsplit("/", 1)[-1]


def _slug(text: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)
    return re.sub(r"[^a-z0-9]+", "-", spaced.lower()).strip("-")
