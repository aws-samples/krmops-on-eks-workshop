"""CEL grammar check for KRO RGD expressions.

KRO uses CEL in two shapes:
  1. Interpolations `${...}` inside string spec/status fields.
  2. Standalone expressions in `readyWhen` (kro wraps these in `${...}` too).

Both must parse under CEL grammar. We do NOT evaluate — grounding is the
job of kubectl dry-run + the live cluster. This is purely a syntax gate.

Two things a naive `\\$\\{([^}]+)\\}` regex + bare `celpy.compile` get wrong,
both of which reject syntax the kro docs actively teach:

  * Nested braces. Object/map construction (`${{"k": v}}`), inline maps inside
    `map()`/ternaries, and the `${"${VAR}"}` shell-escape form all contain `}`
    inside the interpolation. The regex truncates at the first `}`. We use a
    brace-/string-/escape-aware scanner (`iter_interpolations`) instead.
  * The optional-field operators `.?field` / `[?key]` (cel-go "optional types",
    enabled by Kubernetes and kro) are not implemented by cel-python's grammar.
    For a syntax gate we normalise them to regular access before compiling; a
    genuinely malformed expression still fails, so real errors are not masked.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import click
import yaml

try:
    import celpy  # type: ignore
except ImportError as e:
    print(
        "cel-python is not installed. Run bootstrap.sh or `pip install cel-python`.",
        file=sys.stderr,
    )
    raise SystemExit(2) from e


# The CEL optional-field operators (`.?field` and `[?key]`) come from cel-go's
# "optional types" extension, which Kubernetes and kro enable but cel-python's
# grammar does not implement. Since this is a pure SYNTAX gate (kubectl
# dry-run + the live cluster do the real grounding), we normalise optional
# access down to regular access so the rest of the expression still parses.
# A genuinely malformed expression (unbalanced brace, dangling operator, ...)
# still fails to compile after this rewrite, so real errors are not masked.
_OPTIONAL_FIELD_RE = re.compile(r"\.\?")
_OPTIONAL_INDEX_RE = re.compile(r"\[\?")


def _normalize_optional(expr: str) -> str:
    return _OPTIONAL_INDEX_RE.sub("[", _OPTIONAL_FIELD_RE.sub(".", expr))


def iter_interpolations(s: str) -> Iterable[tuple[str, bool]]:
    """Yield ``(inner_expr, terminated)`` for each ``${...}`` interpolation in ``s``.

    kro wraps CEL in ``${`` … ``}``. A naive ``\\$\\{([^}]+)\\}`` regex truncates
    at the FIRST ``}``, which breaks every expression the kro docs teach that
    contains nested braces — object/map construction (``${{"k": v}}``), inline
    maps inside ``map()``/ternaries, and the ``${"${VAR}"}`` shell-escape form.

    This scanner tracks brace depth and skips over string literals (single and
    double quoted, honouring backslash escapes) so braces inside strings and
    nested ``{}`` are handled correctly. ``terminated`` is ``False`` when a
    ``${`` has no matching closing ``}`` (itself a syntax error worth flagging).
    """
    i, n = 0, len(s)
    while i < n - 1:
        if s[i] == "$" and s[i + 1] == "{":
            j = i + 2
            depth = 1
            quote: str | None = None
            escaped = False
            while j < n:
                c = s[j]
                if quote is not None:
                    if escaped:
                        escaped = False
                    elif c == "\\":
                        escaped = True
                    elif c == quote:
                        quote = None
                elif c in ('"', "'"):
                    quote = c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j < n and depth == 0:
                yield s[i + 2 : j], True
                i = j + 1
                continue
            # Unterminated ${ … — report the remainder and stop.
            yield s[i + 2 :], False
            return
        i += 1


def cel_check(expr: str) -> str | None:
    """Return an error string if `expr` fails to parse; None on success."""
    env = celpy.Environment()
    try:
        env.compile(_normalize_optional(expr))
    except celpy.CELParseError as e:
        return f"CEL parse error: {e}"
    except Exception as e:  # celpy raises a variety of typed errors
        return f"CEL error: {e}"
    return None


def walk(obj: Any, path: str, findings: list[dict], file_str: str) -> None:
    if isinstance(obj, str):
        # Every ${...} interpolation (in any spec/status/template field, and in
        # readyWhen/includeWhen strings, which are also just ${...} in kro) must
        # parse as CEL. readyWhen bare (unwrapped) expressions are handled by the
        # dedicated visit below.
        for expr, terminated in iter_interpolations(obj):
            if not terminated:
                findings.append(
                    {
                        "severity": "error",
                        "file": file_str,
                        "path": path,
                        "expression": expr.strip(),
                        "message": "unterminated ${ ... } interpolation (no matching '}')",
                    }
                )
                continue
            err = cel_check(expr.strip())
            if err is not None:
                findings.append(
                    {
                        "severity": "error",
                        "file": file_str,
                        "path": path,
                        "expression": expr.strip(),
                        "message": err,
                    }
                )
    elif isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else k, findings, file_str)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]", findings, file_str)


def visit_ready_when(doc: dict, findings: list[dict], file_str: str) -> None:
    spec = doc.get("spec") or {}
    resources = spec.get("resources") or []
    if not isinstance(resources, list):
        return
    for i, r in enumerate(resources):
        if not isinstance(r, dict):
            continue
        rw = r.get("readyWhen")
        if not rw:
            continue
        items = rw if isinstance(rw, list) else [rw]
        for j, expr in enumerate(items):
            if not isinstance(expr, str):
                continue
            # kro readyWhen entries are ${...} interpolations; those are already
            # grammar-checked by walk(). Only fall back to checking the whole
            # string here for the bare (unwrapped) form, so we neither emit a
            # false positive on the `${` wrapper nor double-report.
            if "${" in expr:
                continue
            err = cel_check(expr.strip())
            if err is not None:
                findings.append(
                    {
                        "severity": "error",
                        "file": file_str,
                        "path": f"spec.resources[{i}].readyWhen[{j}]",
                        "expression": expr,
                        "message": err,
                    }
                )


def iter_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        yield target
        return
    for ext in ("*.yaml", "*.yml"):
        yield from sorted(target.rglob(ext))


@click.command()
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def main(target: Path, output_format: str) -> None:
    findings: list[dict] = []
    for path in iter_files(target):
        file_str = str(path)
        try:
            with path.open() as f:
                docs = list(yaml.safe_load_all(f))
        except yaml.YAMLError as e:
            findings.append(
                {"severity": "error", "file": file_str, "message": f"YAML parse error: {e}"}
            )
            continue

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "ResourceGraphDefinition":
                visit_ready_when(doc, findings, file_str)
            walk(doc, "", findings, file_str)

    if output_format == "json":
        sys.stdout.write(json.dumps({"findings": findings}, indent=2) + "\n")
    else:
        for f in findings:
            path = f.get("path", "?")
            expr = f.get("expression", "")
            expr_hint = f" `{expr}`" if expr else ""
            print(f"[ERROR] {f['file']} {path}{expr_hint}: {f['message']}")

    if any(f["severity"] == "error" for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
