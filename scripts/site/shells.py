"""Windows (PowerShell) and macOS/Linux versions of every command block (#173, spec 07).

A page holds one macOS/Linux block; `build` puts a Windows version beside it, derived by the
fixed rules below, or taken from a hand-written `<pre class="windows">` right after it. A line
the rules cannot translate fails the build, so no Windows reader is handed a command that only
looks right. Output blocks and `usage` blocks have a class and are left alone.
"""

import html
import re

from scripts.site.nav import SiteError

_GROUP = re.compile(
    r'<div class="shell-tabs">\n<pre class="shell-posix">(?P<posix>.*?</pre>)\n'
    r'<pre class="shell-windows"(?P<derived> data-derived="")?>(?P<windows>.*?</pre>)\n</div>',
    re.DOTALL,
)
_COMMANDS = re.compile(
    r"<pre>(?P<open><code[^>]*>)(?P<posix>.*?)</code></pre>"
    r'(?:\n<pre class="windows"><code>(?P<windows>.*?)</code></pre>)?',
    re.DOTALL,
)
_LINE_SPAN = re.compile(r'<span class="line">(.*?)</span>', re.DOTALL)
# <your-export>, <your profile>: replaced by the reader, not run. A redirect has a space after it.
_PLACEHOLDER = re.compile(r"<(?=\S)[^<>]*(?<=\S)>")
_CAT = re.compile(r"\$\(cat [^\s)]+\)")  # PowerShell runs $(cat FILE) too: Get-Content
_NAME = r"[A-Z_][A-Z0-9_]*"


def shell_tabs(page: str, text: str) -> str:
    """`text` with each command block grouped with its Windows version, as tabs."""
    text = _GROUP.sub(_ungroup, text)

    def group(block: re.Match[str]) -> str:
        posix = block["posix"]
        if block["windows"] is None and "<" in _LINE_SPAN.sub(r"\1", posix):
            return block[0]  # other markup: not a plain command, left as written
        if block["windows"] is not None:
            windows, derived = block["windows"], ""
        else:
            windows, derived = _derive(page, _LINE_SPAN.sub(r"\1", posix)), ' data-derived=""'
        return (
            '<div class="shell-tabs">\n'
            f'<pre class="shell-posix">{block["open"]}{posix}</code></pre>\n'
            f'<pre class="shell-windows"{derived}><code>{windows}</code></pre>\n</div>'
        )

    return _COMMANDS.sub(group, text)


def _ungroup(group: re.Match[str]) -> str:
    """A built group back as the page's source: the block, and a hand-written Windows one."""
    source = f"<pre>{group['posix']}"
    if group["derived"]:
        return source
    windows = _LINE_SPAN.sub(r"\1", group["windows"])
    return f'{source}\n<pre class="windows">{windows}'


def _derive(page: str, block: str) -> str:
    lines = html.unescape(block).split("\n")
    return "\n".join(
        html.escape(_powershell(page, number, line), quote=False)
        for number, line in enumerate(lines, 1)
    )


def _powershell(page: str, number: int, line: str) -> str:
    """One macOS/Linux line as PowerShell, or SiteError when no fixed rule can say."""
    if reason := _untranslatable(line):
        raise SiteError(
            f"{page}: no Windows version for line {number} of a code block ({reason}): "
            f'{line!r}. Add a hand-written <pre class="windows"> right after the block'
        )
    if match := re.fullmatch(rf"export ({_NAME})=(.*)", line):
        return f"$env:{match[1]} = {_quoted(match[2])}"
    if match := re.fullmatch(rf"({_NAME})=(\S*)", line):
        return f"${match[1]} = {_quoted(match[2])}"
    line = re.sub(r"^curl(?=\s|$)", "curl.exe", line)
    return " ".join(f"'{word}'" if word.startswith("@") else word for word in line.split(" "))


def _untranslatable(line: str) -> str:
    unquoted = _PLACEHOLDER.sub("x", _without_quoted(line))
    if line.rstrip().endswith("\\"):
        return "a line continuation"
    if "$(" in _CAT.sub("", line):
        return "a command substitution"
    for operator, name in (("&&", "&&"), ("||", "||"), ("|", "a pipe"), (">", "a redirect")):
        if operator in unquoted:
            return name
    if "<" in unquoted:
        return "a redirect"
    return ""


def _without_quoted(line: str) -> str:
    """`line` with everything inside quotes blanked, so only shell syntax is left."""
    out, quote = [], ""
    for char in line:
        if quote:
            out.append(" " if char != quote else char)
            quote = "" if char == quote else quote
        else:
            out.append(char)
            quote = char if char in "'\"" else ""
    return "".join(out)


def _quoted(value: str) -> str:
    return value if value[:1] in ("'", '"') else f'"{value}"'
