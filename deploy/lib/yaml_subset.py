"""Just enough YAML for the deploy config files (maps and scalars, no lists)."""

from __future__ import annotations

from typing import Any


def _strip_inline_comment(rest: str) -> str:
    """Drop a trailing `# comment`, including one that follows a quoted scalar."""
    if rest[:1] in ("'", '"'):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end > 0:
            return rest[: end + 1]
    if " #" in rest:
        return rest.split(" #", 1)[0].rstrip()
    return rest


def parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw[:1] in "\"'" and raw[-1:] == raw[:1] and len(raw) >= 2:
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def extract_top_map(text: str, name: str) -> dict[str, Any]:
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if line.startswith(f"{name}:") and not line.startswith(" "):
            start = i
            break
    if start < 0:
        return {}

    out: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, out)]
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("#"):
            rest = ""
        rest = _strip_inline_comment(rest)

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if rest in ("", "|", ">", ">-", "|-", "|+") or rest.startswith(">"):
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_scalar(rest)
    return out


def set_block_scalar(text: str, block: str, key: str, value: str) -> str:
    """Replace one scalar under a top-level block. Value is written double-quoted."""
    lines = text.splitlines(keepends=True)
    in_block = False
    found = False
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    for i, line in enumerate(lines):
        if line.startswith(f"{block}:"):
            in_block = True
            continue
        if in_block and line.strip() and not line.startswith((" ", "\t")):
            break
        if not in_block:
            continue
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            indent = line[: len(line) - len(line.lstrip(" "))]
            nl = "\n" if line.endswith("\n") else ""
            lines[i] = f"{indent}{key}: {quoted}{nl}"
            found = True
            break
    if not found:
        raise RuntimeError(f"could not find {block}.{key} in config")
    return "".join(lines)


def redact_platform_password(text: str) -> str:
    """Blank platform.vms_password so the pod secret does not carry the VMS admin password."""
    try:
        return set_block_scalar(text, "platform", "vms_password", "")
    except RuntimeError:
        return text
