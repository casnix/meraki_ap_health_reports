"""
meraki_ap_report.py
-------------------
Reads the JSON file produced by meraki_ap_crawler.py and generates a
formatted PDF report.

New in this version:
  - Per-network status summary bar above each AP table
  - Per-AP client score columns (association, auth, DHCP, DNS pass rates
    and composite 0-100 score)
  - Per-network security alarm section at the end of each network block

Usage:
  python meraki_ap_report.py
  python meraki_ap_report.py --input my_data.json --output my_report.pdf
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
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
DARK_TEAL  = colors.HexColor("#007A72")
LIGHT_TEAL = colors.HexColor("#D6F0EE")
SILVER     = colors.HexColor("#F4F6F8")
MID_GREY   = colors.HexColor("#BEC5CE")
DARK_GREY  = colors.HexColor("#6B7280")
RED        = colors.HexColor("#C0392B")
LIGHT_RED  = colors.HexColor("#FDECEA")
ORANGE     = colors.HexColor("#E67E22")
GREEN      = colors.HexColor("#27AE60")
AMBER      = colors.HexColor("#F59E0B")
WHITE      = colors.white
BLACK      = colors.black

MARGIN = 0.5 * inch

# ---------------------------------------------------------------------------
# Security alarm classification
# ---------------------------------------------------------------------------

SECURITY_KEYWORDS: frozenset[str] = frozenset([
    "rogue",
    "radar",
    "security",
    "spoofing",
    "deauth",
    "flood",
    "intrusion",
    "attack",
    "anomaly",
    "unauthorized",
    "evil twin",
])


def _is_security_alarm(alarm_type: str) -> bool:
    lower = alarm_type.lower()
    return any(kw in lower for kw in SECURITY_KEYWORDS)


# ---------------------------------------------------------------------------
# Status / score helpers
# ---------------------------------------------------------------------------

STATUS_COLORS: dict[str, Any] = {
    "online":   GREEN,
    "alerting": ORANGE,
    "offline":  RED,
    "dormant":  MID_GREY,
    "unknown":  MID_GREY,
}


def status_color(status: str) -> Any:
    return STATUS_COLORS.get((status or "").lower(), MID_GREY)


def _score_color(score: float | None) -> Any:
    if score is None:
        return MID_GREY
    if score >= 90:
        return GREEN
    if score >= 70:
        return AMBER
    return RED


def _pct_str(fail: int, total: int) -> str:
    """Return pass-rate string: '95.0%' or '—' if no data."""
    if total <= 0:
        return "—"
    passed = total - fail
    return f"{passed / total * 100:.0f}%"


def _score_str(score: float | None) -> str:
    if score is None:
        return "—"
    return f"{score:.0f}"


# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

def _cover_page_template(doc: BaseDocTemplate) -> PageTemplate:
    frame = Frame(
        MARGIN, MARGIN, doc.width, doc.height,
        id="cover",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    return PageTemplate(id="Cover", frames=[frame], pagesize=letter)


def _data_page_template(doc: BaseDocTemplate) -> PageTemplate:
    W, H = landscape(letter)
    frame = Frame(
        MARGIN, MARGIN + 0.3 * inch,
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
        canvas.drawRightString(W - MARGIN, 0.25 * inch, f"Page {doc.page}")
        canvas.restoreState()

    return PageTemplate(
        id="Data", frames=[frame],
        pagesize=landscape(letter), onPage=_footer,
    )


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles() -> dict[str, ParagraphStyle]:
    s: dict[str, ParagraphStyle] = {}

    s["cover_title"] = ParagraphStyle(
        "cover_title", fontSize=28, leading=34,
        textColor=WHITE, alignment=TA_CENTER, fontName="Helvetica-Bold",
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub", fontSize=13, leading=18,
        textColor=LIGHT_TEAL, alignment=TA_CENTER, fontName="Helvetica",
    )
    s["cover_meta"] = ParagraphStyle(
        "cover_meta", fontSize=9, leading=13,
        textColor=WHITE, alignment=TA_CENTER, fontName="Helvetica",
    )
    s["section_heading"] = ParagraphStyle(
        "section_heading", fontSize=11, leading=14,
        textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=4,
    )
    s["network_heading"] = ParagraphStyle(
        "network_heading", fontSize=10, leading=13,
        textColor=WHITE, fontName="Helvetica-Bold",
    )
    s["subheading"] = ParagraphStyle(
        "subheading", fontSize=8, leading=11,
        textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=2,
    )
    s["table_header"] = ParagraphStyle(
        "table_header", fontSize=6.5, leading=9,
        textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_CENTER,
    )
    s["cell"] = ParagraphStyle(
        "cell", fontSize=6.5, leading=8.5,
        textColor=BLACK, fontName="Helvetica", wordWrap="CJK",
    )
    s["cell_center"] = ParagraphStyle(
        "cell_center", fontSize=6.5, leading=8.5,
        textColor=BLACK, fontName="Helvetica", alignment=TA_CENTER,
    )
    s["alarm_cell"] = ParagraphStyle(
        "alarm_cell", fontSize=6.5, leading=8.5,
        textColor=RED, fontName="Helvetica-Bold", alignment=TA_CENTER,
    )
    s["sec_alarm_ap"] = ParagraphStyle(
        "sec_alarm_ap", fontSize=7, leading=9,
        textColor=NAVY, fontName="Helvetica-Bold",
    )
    s["sec_alarm_type"] = ParagraphStyle(
        "sec_alarm_type", fontSize=7, leading=9,
        textColor=RED, fontName="Helvetica-Bold",
    )
    s["sec_alarm_cell"] = ParagraphStyle(
        "sec_alarm_cell", fontSize=7, leading=9,
        textColor=BLACK, fontName="Helvetica",
    )
    return s


# ---------------------------------------------------------------------------
# Network status summary bar
# ---------------------------------------------------------------------------

def _network_summary_bar(
    net_name: str,
    net_aps: list[dict[str, Any]],
    avail_w: float,
    styles: dict[str, ParagraphStyle],
) -> Table:
    """
    A two-row block: coloured heading strip + status/score stat cells.
    """
    total    = len(net_aps)
    online   = sum(1 for a in net_aps if (a.get("status") or "").lower() == "online")
    offline  = sum(1 for a in net_aps if (a.get("status") or "").lower() == "offline")
    alerting = sum(1 for a in net_aps if (a.get("status") or "").lower() == "alerting")
    dormant  = sum(1 for a in net_aps if (a.get("status") or "").lower() == "dormant")
    alarmed  = sum(1 for a in net_aps if a.get("alarms"))

    # Avg client score across APs that have data
    scored = [a["client_score"] for a in net_aps if a.get("client_score") is not None]
    avg_score: float | None = (sum(scored) / len(scored)) if scored else None

    sec_alarms = sum(
        1 for a in net_aps
        for alm in (a.get("alarms") or [])
        if _is_security_alarm(alm)
    )

    def _stat(label: str, value: str, val_color: Any = BLACK) -> list:
        return [
            Paragraph(label, ParagraphStyle("sl", fontSize=6, leading=8,
                      textColor=DARK_GREY, fontName="Helvetica")),
            Paragraph(value, ParagraphStyle("sv", fontSize=11, leading=13,
                      textColor=val_color, fontName="Helvetica-Bold",
                      alignment=TA_CENTER)),
        ]

    score_val  = _score_str(avg_score)
    score_col  = _score_color(avg_score)

    stat_cols = [
        ("Total APs",      str(total),    NAVY),
        ("Online",         str(online),   GREEN),
        ("Offline",        str(offline),  RED   if offline  else BLACK),
        ("Alerting",       str(alerting), ORANGE if alerting else BLACK),
        ("Dormant",        str(dormant),  MID_GREY),
        ("With Alarms",    str(alarmed),  RED   if alarmed  else BLACK),
        ("Avg Client Score", score_val,   score_col),
        ("Security Alarms",  str(sec_alarms), RED if sec_alarms else BLACK),
    ]
    n_cols = len(stat_cols)
    col_w  = avail_w / n_cols

    # Row 0: network name spanning full width
    heading_row = [Paragraph(f"Network: {net_name}", styles["network_heading"])]
    # Row 1: stat cells
    stat_row = []
    for label, value, col in stat_cols:
        cell_tbl = Table(
            [[Paragraph(label, ParagraphStyle(
                "sl", fontSize=6, leading=7.5,
                textColor=DARK_GREY, fontName="Helvetica", alignment=TA_CENTER,
            ))],
             [Paragraph(value, ParagraphStyle(
                "sv", fontSize=12, leading=14,
                textColor=col, fontName="Helvetica-Bold", alignment=TA_CENTER,
            ))]],
            colWidths=[col_w],
        )
        cell_tbl.setStyle(TableStyle([
            ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        stat_row.append(cell_tbl)

    # Both rows must have the same number of cells for SPAN to work.
    # Pad the heading row with empty strings to match n_cols stat cells.
    outer = Table(
        [
            [heading_row[0]] + [""] * (n_cols - 1),
            stat_row,
        ],
        colWidths=[col_w] * n_cols,
        rowHeights=[0.28 * inch, 0.52 * inch],
    )
    outer.setStyle(TableStyle([
        # Heading row spans all columns
        ("SPAN",          (0, 0), (-1, 0)),
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("LEFTPADDING",   (0, 0), (-1, 0), 8),
        ("VALIGN",        (0, 0), (-1, 0), "MIDDLE"),
        # Stat row
        ("BACKGROUND",    (0, 1), (-1, 1), SILVER),
        ("ALIGN",         (0, 1), (-1, 1), "CENTER"),
        ("VALIGN",        (0, 1), (-1, 1), "MIDDLE"),
        ("INNERGRID",     (0, 1), (-1, 1), 0.4, MID_GREY),
        ("BOX",           (0, 0), (-1, -1), 0.5, MID_GREY),
        ("TOPPADDING",    (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
    ]))
    return outer


# ---------------------------------------------------------------------------
# AP data table
# ---------------------------------------------------------------------------

# Columns: AP Name, Serial, Model, Tags, Status, Last Seen,
#          Assoc%, Auth%, DHCP%, DNS%, Score, Alarms
COL_HEADERS = [
    "AP Name", "Serial", "Model", "Tags",
    "Status", "Last Seen",
    "Assoc\n%", "Auth\n%", "DHCP\n%", "DNS\n%", "Score",
    "Alarms",
]
COL_RATIOS = [
    0.13, 0.10, 0.07, 0.10,
    0.07, 0.12,
    0.04, 0.04, 0.04, 0.04, 0.04,
    0.21,
]  # sum = 1.0


def _ap_table(
    net_aps: list[dict[str, Any]],
    avail_w: float,
    styles: dict[str, ParagraphStyle],
) -> Table:
    col_widths = [avail_w * r for r in COL_RATIOS]
    header_row = [Paragraph(h, styles["table_header"]) for h in COL_HEADERS]
    rows = [header_row]

    sorted_aps = sorted(net_aps, key=lambda x: (x.get("name") or "").lower())

    for ap in sorted_aps:
        status = ap.get("status") or "unknown"
        alarms = ap.get("alarms") or []
        tags   = ", ".join(ap.get("tags") or []) or "—"

        alarm_text  = "\n".join(alarms) if alarms else "None"
        alarm_style = styles["alarm_cell"] if alarms else styles["cell_center"]

        assoc_pct = _pct_str(ap.get("assoc_fail", 0), ap.get("assoc_total", 0))
        auth_pct  = _pct_str(ap.get("auth_fail",  0), ap.get("auth_total",  0))
        dhcp_pct  = _pct_str(ap.get("dhcp_fail",  0), ap.get("dhcp_total",  0))
        dns_pct   = _pct_str(ap.get("dns_fail",   0), ap.get("dns_total",   0))
        score_s   = _score_str(ap.get("client_score"))

        rows.append([
            Paragraph(ap.get("name")     or "", styles["cell"]),
            Paragraph(ap.get("serial")   or "", styles["cell"]),
            Paragraph(ap.get("model")    or "", styles["cell_center"]),
            Paragraph(tags,                     styles["cell"]),
            Paragraph(status.capitalize(),      styles["cell_center"]),
            Paragraph(ap.get("last_seen") or "N/A", styles["cell"]),
            Paragraph(assoc_pct, styles["cell_center"]),
            Paragraph(auth_pct,  styles["cell_center"]),
            Paragraph(dhcp_pct,  styles["cell_center"]),
            Paragraph(dns_pct,   styles["cell_center"]),
            Paragraph(score_s,   styles["cell_center"]),
            Paragraph(alarm_text, alarm_style),
        ])

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)

    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("GRID",          (0, 0), (-1, -1), 0.3, MID_GREY),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, SILVER]),
    ]
    # Per-row coloring for status and score columns
    for i, ap in enumerate(sorted_aps, start=1):
        sc = status_color(ap.get("status") or "unknown")
        cmds.append(("TEXTCOLOR", (4, i), (4, i), sc))
        cmds.append(("FONTNAME",  (4, i), (4, i), "Helvetica-Bold"))

        score = ap.get("client_score")
        sc2 = _score_color(score)
        cmds.append(("TEXTCOLOR", (10, i), (10, i), sc2))
        cmds.append(("FONTNAME",  (10, i), (10, i), "Helvetica-Bold"))

    tbl.setStyle(TableStyle(cmds))
    return tbl


# ---------------------------------------------------------------------------
# Security alarm section
# ---------------------------------------------------------------------------

def _security_alarm_section(
    net_aps: list[dict[str, Any]],
    avail_w: float,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    """
    Return flowables for the per-network security alarm table.
    Returns an empty list if there are no security alarms.
    """
    sec_rows: list[tuple[str, str]] = []   # (ap_name, alarm_type)
    for ap in sorted(net_aps, key=lambda x: (x.get("name") or "").lower()):
        ap_name = ap.get("name") or ap.get("serial") or "Unknown"
        for alm in (ap.get("alarms") or []):
            if _is_security_alarm(alm):
                sec_rows.append((ap_name, alm))

    if not sec_rows:
        return []

    flowables: list[Any] = [
        Spacer(1, 0.12 * inch),
        HRFlowable(width=avail_w, thickness=1, color=RED, spaceAfter=4),
        Paragraph("⚠ Security Alarms", ParagraphStyle(
            "sec_head", fontSize=8, leading=11,
            textColor=RED, fontName="Helvetica-Bold", spaceAfter=3,
        )),
    ]

    tbl_data = [
        [
            Paragraph("AP Name",    styles["table_header"]),
            Paragraph("Alarm Type", styles["table_header"]),
        ]
    ] + [
        [
            Paragraph(ap_name, styles["sec_alarm_ap"]),
            Paragraph(alarm,   styles["sec_alarm_type"]),
        ]
        for ap_name, alarm in sec_rows
    ]

    col_w = [avail_w * 0.35, avail_w * 0.65]
    sec_tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    sec_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#8B0000")),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("GRID",          (0, 0), (-1, -1), 0.3, MID_GREY),
        ("BACKGROUND",    (0, 1), (-1, -1), LIGHT_RED),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    flowables.append(sec_tbl)
    return flowables


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------

def _cover(meta: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    W, H = letter
    story: list[Any] = []

    banner = Table(
        [[Paragraph("Meraki Access Point<br/>Status Report", styles["cover_title"])]],
        colWidths=[W - 2 * MARGIN],
        rowHeights=[3 * inch],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), NAVY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 24),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 24),
    ]))
    story.append(banner)
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"Generated: {_now_str()}", styles["cover_sub"]))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(f"Scope: {meta.get('scope', 'N/A')}", styles["cover_meta"]))
    story.append(Spacer(1, 0.25 * inch))

    aps: list[dict[str, Any]] = meta.get("access_points", [])
    total    = len(aps)
    online   = sum(1 for a in aps if (a.get("status") or "").lower() == "online")
    offline  = sum(1 for a in aps if (a.get("status") or "").lower() == "offline")
    alerting = sum(1 for a in aps if (a.get("status") or "").lower() == "alerting")
    alarmed  = sum(1 for a in aps if a.get("alarms"))
    sec_count = sum(
        1 for a in aps for alm in (a.get("alarms") or []) if _is_security_alarm(alm)
    )
    scored = [a["client_score"] for a in aps if a.get("client_score") is not None]
    avg_score = sum(scored) / len(scored) if scored else None

    stats_data = [
        ["Total APs", "Online", "Offline", "Alerting", "With Alarms", "Security Alarms", "Avg Score"],
        [
            str(total), str(online), str(offline), str(alerting),
            str(alarmed), str(sec_count), _score_str(avg_score),
        ],
    ]
    col_w = (W - 2 * MARGIN) / 7
    stats_tbl = Table(stats_data, colWidths=[col_w] * 7,
                      rowHeights=[0.3 * inch, 0.5 * inch])
    stats_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 16),
        ("TEXTCOLOR",     (0, 1), (0, 1),  NAVY),
        ("TEXTCOLOR",     (1, 1), (1, 1),  GREEN),
        ("TEXTCOLOR",     (2, 1), (2, 1),  RED),
        ("TEXTCOLOR",     (3, 1), (3, 1),  ORANGE),
        ("TEXTCOLOR",     (4, 1), (4, 1),  RED),
        ("TEXTCOLOR",     (5, 1), (5, 1),  RED),
        ("TEXTCOLOR",     (6, 1), (6, 1),  _score_color(avg_score)),
        ("BACKGROUND",    (0, 1), (-1, 1), SILVER),
        ("BOX",           (0, 0), (-1, -1), 0.5, MID_GREY),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, MID_GREY),
    ]))
    story.append(stats_tbl)
    story.append(Spacer(1, 0.4 * inch))

    # Active alarms summary
    alarms_flat = [
        (a.get("name") or a.get("serial") or "?", alarm_str)
        for a in aps
        for alarm_str in (a.get("alarms") or [])
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
            ("BACKGROUND",    (0, 0), (-1, 0), TEAL),
            ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, SILVER]),
            ("TEXTCOLOR",     (0, 1), (-1, -1), BLACK),
            ("GRID",          (0, 0), (-1, -1), 0.4, MID_GREY),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(alm_tbl)

    return story


# ---------------------------------------------------------------------------
# Data pages (landscape, one section per network)
# ---------------------------------------------------------------------------

def _data_pages(meta: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    W, _ = landscape(letter)
    avail_w = W - 2 * MARGIN

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
        # 1. Network summary bar (heading + stats)
        summary = _network_summary_bar(net_name, net_aps, avail_w, styles)

        # 2. AP table
        ap_tbl = _ap_table(net_aps, avail_w, styles)

        # 3. Security alarm section
        sec_flowables = _security_alarm_section(net_aps, avail_w, styles)

        # Keep summary + table header together at minimum; rest flows naturally
        story.append(KeepTogether([summary, Spacer(1, 4), ap_tbl]))
        for f in sec_flowables:
            story.append(f)
        story.append(Spacer(1, 0.25 * inch))

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
    p.add_argument("--input",  metavar="FILE", default="ap_data.json",
                   help="JSON from meraki_ap_crawler.py (default: ap_data.json).")
    p.add_argument("--output", metavar="FILE", default="ap_report.pdf",
                   help="Destination PDF path (default: ap_report.pdf).")
    return p


def main() -> None:
    args = build_parser().parse_args()
    build_report(args.input, args.output)


if __name__ == "__main__":
    main()