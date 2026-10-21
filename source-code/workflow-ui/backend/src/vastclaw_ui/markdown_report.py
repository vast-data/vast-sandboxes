"""Convert a subset of Markdown into ReportLab flowables for PDF reports."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Flowable

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")
_HRULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline_html(text: str) -> str:
    """Map **bold**, *italic*, `code` to ReportLab paragraph markup."""
    parts: list[str] = []
    pos = 0
    for match in re.finditer(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_|`(.+?)`", text):
        if match.start() > pos:
            parts.append(_esc(text[pos : match.start()]))
        if match.group(1) is not None:
            parts.append(f"<b>{_esc(match.group(1))}</b>")
        elif match.group(2) is not None or match.group(3) is not None:
            inner = match.group(2) if match.group(2) is not None else match.group(3)
            parts.append(f"<i>{_esc(inner or '')}</i>")
        elif match.group(4) is not None:
            parts.append(f'<font name="Courier">{_esc(match.group(4))}</font>')
        pos = match.end()
    if pos < len(text):
        parts.append(_esc(text[pos:]))
    return "".join(parts) or _esc(text)


def markdown_to_flowables(
    text: str,
    *,
    body: ParagraphStyle,
    heading_styles: dict[int, ParagraphStyle],
    quote_style: ParagraphStyle | None = None,
) -> list[Flowable]:
    """Parse model-generated markdown into ReportLab flowables."""
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, Spacer

    quote = quote_style or body
    lines = (text or "").replace("\r\n", "\n").split("\n")
    story: list[Flowable] = []
    para_buf: list[str] = []
    list_items: list[str] | None = None
    list_ordered = False
    quote_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            story.append(Paragraph(_inline_html(" ".join(para_buf)), body))
            para_buf = []

    def flush_list() -> None:
        nonlocal list_items, list_ordered
        if list_items:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(_inline_html(item), body)) for item in list_items],
                    bulletType="1" if list_ordered else "bullet",
                    leftIndent=14,
                )
            )
            list_items = None
            list_ordered = False

    def flush_quote() -> None:
        nonlocal quote_buf
        if quote_buf:
            story.append(Paragraph(_inline_html(" ".join(quote_buf)), quote))
            quote_buf = []

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote_buf.append(stripped.lstrip(">").strip())
            continue
        if quote_buf:
            flush_quote()

        if not stripped:
            flush_para()
            flush_list()
            continue

        if _HRULE.match(stripped):
            flush_para()
            flush_list()
            story += [Spacer(1, 4), HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cfd6e4")), Spacer(1, 6)]
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush_para()
            flush_list()
            level = min(6, len(heading.group(1)))
            style = heading_styles.get(level, heading_styles.get(3, body))
            story.append(Paragraph(_inline_html(heading.group(2)), style))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            flush_para()
            if list_items is None or list_ordered:
                flush_list()
                list_items = []
                list_ordered = False
            list_items.append(bullet.group(1))
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            flush_para()
            if list_items is None or not list_ordered:
                flush_list()
                list_items = []
                list_ordered = True
            list_items.append(numbered.group(1))
            continue

        flush_list()
        para_buf.append(stripped)

    flush_quote()
    flush_para()
    flush_list()
    return story
