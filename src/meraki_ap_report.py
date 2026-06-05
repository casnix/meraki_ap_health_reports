"""
meraki_ap_report.py
-------------------
Reads the JSON file produced by meraki_ap_crawler.py and generates a
formatted PDF report.

Usage:
  python meraki_ap_report.py                          # uses ap_data.json → ap_report.pdf
  python meraki_ap_report.py --input my_data.json --output my_report.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
NAVY       = colors.HexColor("#0D2B55")
TEAL       = colors.HexColor("#00A89D")
LIGHT_TEAL = colors.HexColor("#D6F0EE")
SILVER     = colors.HexColor("#F4F6F8")
MID_GREY   = colors.HexColor("#BEC5CE")
RED        = colors.HexColor("#C0392B")
ORANGE     = colors.HexColor("#E67E22")
GREEN      = colors.HexColor("#27AE60")
WHITE      = colors.white
BLACK      = colors.black

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

STATUS_COLORS: dict[str, Any] = {
    "online":        GREEN,
    "alerting":      ORANGE,
    "offline":       RED,
    "dormant":       MID_GREY,
    "unknown":       MID_GREY,
}

def status_color(status: str) -> Any:
    return STATUS_COLORS.get(status.lower(), MID_GREY)


def alarm_badge(alarms: list[str]) -> str:
    if not alarms:
        return "None"
    return f"{len(alarms)} alarm{'s' if len(alarms) > 1 else ''}"


# ---------------------------------------------------------------------------
# Page templates (portrait cover + landscape data pages)
# ---------------------------------------------------------------------------

MARGIN = 0.5 * inch

def _cover_page_template(doc: BaseDocTemplate) -> PageTemplate:
    frame = Frame(
        MARGIN, MARGIN,
        doc.width, doc.height,
        id="cover",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    return PageTemplate(id="Cover", frames=[frame], pagesize=letter)


def _data_page_template(doc: BaseDocTemplate) -> PageTemplate:
    W, H = landscape(letter)
    frame = Frame(
        MARGIN, MARGIN + 0.3 * inch,          # leave room for footer
        W - 2 * MARGIN,
        H - 2 * MARGIN - 0.5 * inch,
        id="data",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def _footer(canvas, doc):  # noqa: ANN001
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MID_GREY)
        canvas.drawString(MARGIN, 0.25 * inch,
                          "Meraki AP Status Report  –  Confidential")
        canvas.drawRightString(
            W - MARGIN, 0.25 * inch,
            f"Page {doc.page}",
        )
        canvas.restoreState()

    return PageTemplate(id="Data", frames=[frame], pagesize=landscape(letter),
                        onPage=_footer)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s: dict[str, ParagraphStyle] = {}

    s["cover_title"] = ParagraphStyle(
        "cover_title",
        fontSize=28, leading=34,
        textColor=WHITE, alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub",
        fontSize=13, leading=18,
        textColor=LIGHT_TEAL, alignment=TA_CENTER,
        fontName="Helvetica",
    )
    s["cover_meta"] = ParagraphStyle(
        "cover_meta",
        fontSize=9, leading=13,
        textColor=WHITE, alignment=TA_CENTER,
        fontName="Helvetica",
    )
    s["section_heading"] = ParagraphStyle(
        "section_heading",
        fontSize=11, leading=14,
        textColor=NAVY, fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    s["network_heading"] = ParagraphStyle(
        "network_heading",
        fontSize=9, leading=12,
        textColor=NAVY, fontName="Helvetica-Bold",
    )
    s["table_header"] = ParagraphStyle(
        "table_header",
        fontSize=7.5, leading=10,
        textColor=WHITE, fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    s["cell"] = ParagraphStyle(
        "cell",
        fontSize=7, leading=9,
        textColor=BLACK, fontName="Helvetica",
        wordWrap="CJK",
    )
    s["cell_center"] = ParagraphStyle(
        "cell_center",
        fontSize=7, leading=9,
        textColor=BLACK, fontName="Helvetica",
        alignment=TA_CENTER,
    )
    s["alarm_cell"] = ParagraphStyle(
        "alarm_cell",
        fontSize=7, leading=9,
        textColor=RED, fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    s["summary_label"] = ParagraphStyle(
        "summary_label",
        fontSize=8, leading=11,
        textColor=NAVY, fontName="Helvetica-Bold",
    )
    s["summary_value"] = ParagraphStyle(
        "summary_value",
        fontSize=8, leading=11,
        textColor=BLACK, fontName="Helvetica",
    )
    return s


# ---------------------------------------------------------------------------
# Cover page flowables
# ---------------------------------------------------------------------------

def _cover(meta: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    W, H = letter
    story: list[Any] = []

    # Navy banner
    banner = Table(
        [[Paragraph("Meraki Access Point<br/>Status Report", styles["cover_title"])]],
        colWidths=[W - 2 * MARGIN],
        rowHeights=[3 * inch],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), NAVY),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",  (0, 0), (-1, -1), 24),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 24),
    ]))
    story.append(banner)
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph(
        f"Generated: {_now_str()}",
        styles["cover_sub"],
    ))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(
        f"Scope: {meta.get('scope', 'N/A')}",
        styles["cover_meta"],
    ))
    story.append(Spacer(1, 0.25 * inch))

    # Summary stats
    aps: list[dict[str, Any]] = meta.get("access_points", [])
    total   = len(aps)
    online  = sum(1 for a in aps if a.get("status", "").lower() == "online")
    offline = sum(1 for a in aps if a.get("status", "").lower() == "offline")
    alerting= sum(1 for a in aps if a.get("status", "").lower() == "alerting")
    alarmed = sum(1 for a in aps if a.get("alarms"))

    stats_data = [
        ["Total APs", "Online", "Offline", "Alerting", "With Alarms"],
        [
            str(total),
            str(online),
            str(offline),
            str(alerting),
            str(alarmed),
        ],
    ]
    col_w = (W - 2 * MARGIN) / 5
    stats_tbl = Table(stats_data, colWidths=[col_w] * 5, rowHeights=[0.3 * inch, 0.5 * inch])
    stats_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 18),
        ("TEXTCOLOR",     (0, 1), (0, 1),  NAVY),
        ("TEXTCOLOR",     (1, 1), (1, 1),  GREEN),
        ("TEXTCOLOR",     (2, 1), (2, 1),  RED),
        ("TEXTCOLOR",     (3, 1), (3, 1),  ORANGE),
        ("TEXTCOLOR",     (4, 1), (4, 1),  RED),
        ("BACKGROUND",    (0, 1), (-1, 1), SILVER),
        ("BOX",           (0, 0), (-1, -1), 0.5, MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, MID_GREY),
    ]))
    story.append(stats_tbl)
    story.append(Spacer(1, 0.4 * inch))

    # Alarm summary table (top alarms)
    alarms_flat = [
        (a.get("name", a.get("serial", "?")), alarm_str)
        for a in aps
        for alarm_str in a.get("alarms", [])
    ]
    if alarms_flat:
        story.append(Paragraph("Active Alarms Summary", styles["section_heading"]))
        story.append(Spacer(1, 4))
        alarm_rows = [["AP Name", "Alarm"]] + [
            [ap, alm] for ap, alm in alarms_flat[:20]
        ]
        if len(alarms_flat) > 20:
            alarm_rows.append([f"… and {len(alarms_flat) - 20} more", ""])
        alarm_col = [(W - 2 * MARGIN) * r for r in (0.35, 0.65)]
        alm_tbl = Table(alarm_rows, colWidths=alarm_col, repeatRows=1)
        alm_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), TEAL),
            ("TEXTCOLOR",   (0, 0), (-1, 0), WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, SILVER]),
            ("TEXTCOLOR",   (0, 1), (-1, -1), BLACK),
            ("GRID",        (0, 0), (-1, -1), 0.4, MID_GREY),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",  (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ]))
        story.append(alm_tbl)

    return story


# ---------------------------------------------------------------------------
# Data table flowables (landscape pages, grouped by network)
# ---------------------------------------------------------------------------

COL_HEADERS = ["AP Name", "Serial", "Model", "Tags", "Status", "Last Seen", "Alarms"]
COL_RATIOS  = [0.18, 0.13, 0.09, 0.14, 0.08, 0.16, 0.22]   # must sum to 1.0


def _data_pages(meta: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    W, _ = landscape(letter)
    avail_w = W - 2 * MARGIN
    col_widths = [avail_w * r for r in COL_RATIOS]

    aps: list[dict[str, Any]] = meta.get("access_points", [])

    # Group by network
    networks: dict[str, list[dict[str, Any]]] = {}
    for ap in aps:
        net = ap.get("network_name") or ap.get("network_id", "Unknown")
        networks.setdefault(net, []).append(ap)

    story: list[Any] = []
    story.append(NextPageTemplate("Data"))
    story.append(PageBreak())

    for net_name, net_aps in sorted(networks.items()):
        header_row = [
            Paragraph(h, styles["table_header"]) for h in COL_HEADERS
        ]
        rows = [header_row]

        for ap in sorted(net_aps, key=lambda x: x.get("name", "").lower()):
            status = ap.get("status", "unknown")
            sc     = status_color(status)
            alarms = ap.get("alarms", [])
            tags   = ", ".join(ap.get("tags", [])) or "—"

            alarm_text = (
                "\n".join(alarms) if alarms else "None"
            )
            alarm_style = styles["alarm_cell"] if alarms else styles["cell_center"]

            row = [
                Paragraph(ap.get("name", ""), styles["cell"]),
                Paragraph(ap.get("serial", ""), styles["cell"]),
                Paragraph(ap.get("model", ""), styles["cell_center"]),
                Paragraph(tags, styles["cell"]),
                Paragraph(status.capitalize(), styles["cell_center"]),
                Paragraph(ap.get("last_seen", "N/A"), styles["cell"]),
                Paragraph(alarm_text, alarm_style),
            ]
            rows.append(row)

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)

        # Build per-row status coloring commands
        cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
            ("GRID",          (0, 0), (-1, -1), 0.3, MID_GREY),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, SILVER]),
        ]
        # Colour status cell per row
        for i, ap in enumerate(net_aps, start=1):
            sc = status_color(ap.get("status", "unknown"))
            cmds.append(("TEXTCOLOR",  (4, i), (4, i), sc))
            cmds.append(("FONTNAME",   (4, i), (4, i), "Helvetica-Bold"))

        tbl.setStyle(TableStyle(cmds))

        net_block: list[Any] = [
            Paragraph(f"Network: {net_name}", styles["network_heading"]),
            Spacer(1, 3),
            tbl,
            Spacer(1, 0.2 * inch),
        ]
        story.append(KeepTogether(net_block[:3]))   # keep heading + table together if possible
        story.append(net_block[3])                  # spacer separately

    return story


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_report(input_path: str, output_path: str) -> None:
    with open(input_path, encoding="utf-8") as fh:
        meta: dict[str, Any] = json.load(fh)

    styles = _styles()

    doc = BaseDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="Meraki AP Status Report",
        author="meraki_ap_report.py",
    )

    doc.addPageTemplates([
        _cover_page_template(doc),
        _data_page_template(doc),
    ])

    story: list[Any] = []
    story += _cover(meta, styles)
    story += _data_pages(meta, styles)

    doc.build(story)
    print(f"Report written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a PDF report from Meraki AP crawler output.",
    )
    p.add_argument(
        "--input",
        metavar="FILE",
        default="ap_data.json",
        help="JSON file produced by meraki_ap_crawler.py (default: ap_data.json).",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default="ap_report.pdf",
        help="Destination PDF path (default: ap_report.pdf).",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    build_report(args.input, args.output)


if __name__ == "__main__":
    main()