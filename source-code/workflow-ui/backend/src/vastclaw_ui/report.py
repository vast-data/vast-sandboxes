"""Render a workflow's analysis into a self-contained, branded PDF (reportlab)."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

# Brand palette: VAST navy, NVIDIA green, VAST cyan.
_VAST_NAVY = "#0e2a6b"
_NVIDIA_GREEN = "#76b900"
_CLAW_CYAN = "#0e7ea8"


@dataclass
class ReportSection:
    name: str
    findings: list[str] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)


@dataclass
class CustomerAction:
    customer: str
    stage: str = ""
    action: str = ""
    source: str = ""


@dataclass
class ReportContent:
    title: str
    department: str
    created_by: str
    version: int
    created_at: str
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    recommendation: str = ""
    produced_by: str = ""
    customer_actions: list[CustomerAction] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)


def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _metric_chart(metrics: list[dict], width: float, height: float = 130):
    """A branded vertical bar chart of a section's numeric metrics (or None)."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors

    pairs = [(str(m.get("label", "")), _num(m.get("value"))) for m in metrics]
    pairs = [(label, value) for label, value in pairs if value is not None]
    if not pairs:
        return None

    drawing = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x = 24
    chart.y = 28
    chart.width = width - 48
    chart.height = height - 44
    chart.data = [[value for _, value in pairs]]
    chart.categoryAxis.categoryNames = [label[:22] for label, _ in pairs]
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.angle = 20
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.categoryAxis.labels.dy = -2
    chart.valueAxis.valueMin = min(0.0, *(v for _, v in pairs))
    chart.valueAxis.labels.fontSize = 6.5
    chart.barWidth = 8
    chart.groupSpacing = 8
    chart.bars[0].fillColor = colors.HexColor(_CLAW_CYAN)
    chart.bars[0].strokeColor = None
    drawing.add(chart)
    return drawing


def render_report_pdf(content: ReportContent) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from vastclaw_ui.markdown_report import markdown_to_flowables

    styles = getSampleStyleSheet()
    accent = colors.HexColor(_VAST_NAVY)
    page_w, page_h = A4
    content_w = page_w - 40 * mm

    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=accent, fontSize=20, spaceAfter=2)
    meta = ParagraphStyle("meta", parent=styles["Normal"], textColor=colors.grey, fontSize=9)
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"], textColor=accent, fontSize=13, spaceBefore=14, spaceAfter=5
    )
    h3 = ParagraphStyle(
        "h3", parent=styles["Heading3"], textColor=accent, fontSize=11.5, spaceBefore=10, spaceAfter=4
    )
    h4 = ParagraphStyle(
        "h4", parent=styles["Heading4"], textColor=colors.HexColor("#1a3a7a"), fontSize=10.5, spaceBefore=8, spaceAfter=3
    )
    body = ParagraphStyle(
        "body", parent=styles["Normal"], fontSize=10, leading=15, alignment=TA_LEFT, spaceAfter=6
    )
    quote = ParagraphStyle(
        "quote",
        parent=body,
        leftIndent=10,
        borderPadding=6,
        textColor=colors.HexColor("#334155"),
        backColor=colors.HexColor("#f4f7fb"),
    )
    heading_styles = {1: h1, 2: h2, 3: h3, 4: h4, 5: h4, 6: h4}

    def esc(text: str) -> str:
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )

    def inline_esc(text: str) -> str:
        from vastclaw_ui.markdown_report import _inline_html

        return _inline_html(text)

    def recommendation_callout(text: str) -> Table:
        cell = Paragraph(inline_esc(text), body)
        table = Table([[cell]], colWidths=[content_w])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef6fc")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#b8d4ea")),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(_CLAW_CYAN)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        return table

    story: list = [
        Paragraph(esc(content.title), h1),
        Paragraph(
            f"{esc(content.department)} · v{content.version} · {esc(content.created_at)} · "
            f"by {esc(content.created_by)}"
            + (f" · model: {esc(content.produced_by)}" if content.produced_by else ""),
            meta,
        ),
        Spacer(1, 3),
        HRFlowable(width="100%", thickness=1.4, color=colors.HexColor(_NVIDIA_GREEN)),
        Spacer(1, 10),
    ]

    if content.summary:
        story.extend(
            markdown_to_flowables(
                content.summary,
                body=body,
                heading_styles=heading_styles,
                quote_style=quote,
            )
        )

    if content.customer_actions and "per-customer" not in (content.summary or "").lower():
        story += [Spacer(1, 6), Paragraph("Per-customer next steps", h2)]
        rows = [["Customer", "Stage", "Next action", "Source"]]
        for action in content.customer_actions:
            rows.append(
                [
                    action.customer,
                    action.stage or "—",
                    action.action,
                    action.source or "—",
                ]
            )
        table = Table(rows, colWidths=[38 * mm, 32 * mm, 68 * mm, 32 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd6e4")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f2f5fb")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story += [Spacer(1, 4), table]

    if content.recommendation:
        story += [
            Spacer(1, 8),
            Paragraph("Recommendation", h2),
            recommendation_callout(content.recommendation),
        ]

    if content.key_findings:
        story += [
            Spacer(1, 8),
            Paragraph("Key insights", h2),
            ListFlowable(
                [ListItem(Paragraph(inline_esc(k), body)) for k in content.key_findings],
                bulletType="bullet",
                leftIndent=14,
            ),
        ]

    if content.sections:
        story += [Spacer(1, 8), Paragraph("Sub-agent findings", h2)]

    for section in content.sections:
        if not (section.findings or section.metrics):
            continue
        story.append(Paragraph(esc(section.name), h3))
        if section.findings:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(inline_esc(f), body)) for f in section.findings],
                    bulletType="bullet",
                    leftIndent=14,
                )
            )
        if section.metrics:
            chart = _metric_chart(section.metrics, content_w)
            if chart is not None:
                story += [Spacer(1, 4), chart]
            rows = [["Metric", "Value"]]
            for m in section.metrics:
                unit = f" {m.get('unit', '')}".rstrip()
                rows.append([str(m.get("label", "")), f"{m.get('value', '')}{unit}"])
            table = Table(rows, colWidths=[110 * mm, 50 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), accent),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd6e4")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#f2f5fb")],
                        ),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story += [Spacer(1, 4), table]

    def _decorate(canvas: object, doc: object) -> None:
        # Branded header band + footer, drawn on every page.
        canvas.saveState()
        band_h = 12 * mm
        canvas.setFillColor(colors.HexColor(_VAST_NAVY))
        canvas.rect(0, page_h - band_h, page_w, band_h, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor(_NVIDIA_GREEN))
        canvas.rect(0, page_h - band_h - 1.4, page_w, 1.4, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(20 * mm, page_h - 8 * mm, "Secure agents on VAST")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(page_w - 20 * mm, page_h - 8 * mm, "NVIDIA NemoClaw")

        canvas.setStrokeColor(colors.HexColor("#cfd6e4"))
        canvas.line(20 * mm, 14 * mm, page_w - 20 * mm, 14 * mm)
        canvas.setFillColor(colors.grey)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            20 * mm, 10 * mm, "Confidential — secure agents on VAST, with NVIDIA NemoClaw"
        )
        canvas.drawRightString(page_w - 20 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=content.title,
        author="VAST · NVIDIA NemoClaw",
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    doc.build(story, onFirstPage=_decorate, onLaterPages=_decorate)
    return buffer.getvalue()
