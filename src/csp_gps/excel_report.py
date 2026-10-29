"""Writes the 7-sheet Excel workbook. Summary figures are live formulas over the Detailed Issue Tracker,
so edits the team makes in the tracker (status, owner) flow through when the workbook recalculates."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import NOT_AVAILABLE
from .extract import CATEGORIES, OTHER
from .labels import problem_name, status_name
from .pipeline import ALL_COLUMNS, TRACKER_COLUMNS
from .visits import done_csps, score_zero_csps

NAVY = "1F3864"
BLUE = "2F5597"
GREY = "7F7F7F"
LIGHT = "D9E2F3"
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
EXTRA_FILL = PatternFill("solid", fgColor=GREY)
TITLE_FONT = Font(bold=True, size=16, color=NAVY)
SUB_FONT = Font(italic=True, size=10, color="595959")
BANNER_FILL = PatternFill("solid", fgColor="C00000")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

T = "'Detailed Issue Tracker'"
FIRST_ROW = 4
CATEGORY_NAMES = [name for name, _ in CATEGORIES] + [OTHER]
COL = {name: get_column_letter(i + 1) for i, name in enumerate(ALL_COLUMNS)}
WIDTHS = {"Record ID": 11, "Message Date": 12, "Message Time": 10, "Group/Chat": 24, "Reported By": 18,
          "CSP Name": 20, "CSP ID": 13, "State": 12, "District": 14, "Village/Branch": 16, "Issue Type": 26,
          "Issue Description": 48, "Actual Location": 20, "Planned Location": 20, "Distance Mismatch (metres)": 13,
          "Visit Status": 18, "Evidence Filename": 30, "Message Reference": 34, "Duplicate Flag": 17,
          "Confidence": 11, "Resolution Status": 16, "Assigned To": 16, "Resolution Notes": 30,
          "Verification Reasons": 45, "Screenshot OCR Text": 45, "Resolution Evidence": 40, "Field Sources": 36,
          "Suggested Action": 55, "Next Step": 26, "Days Pending": 10, "Added On": 12}


def _rng(col: str, last: int) -> str:
    return f"{T}!${COL[col]}${FIRST_ROW}:${COL[col]}${last}"


def _title(ws, title: str, summary: dict, width: int = 8) -> int:
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    period = f"{summary.get('period_start') or 'start of data'} to {summary.get('period_end') or 'end of data'}"
    ws["A2"] = (f"Reporting period: {period} ({summary.get('end_date_rule')}) | Times: {summary.get('timezone')} | "
                f"Generated {summary.get('generated_at')}")
    ws["A2"].font = SUB_FONT
    row = 3
    if summary.get("data_source") == "SYNTHETIC TEST DATA":
        ws.cell(row=3, column=1, value="SYNTHETIC TEST DATA - generated for testing only. Not real WhatsApp evidence.")
        for c in range(1, width + 1):
            ws.cell(row=3, column=c).fill = BANNER_FILL
        ws.cell(row=3, column=1).font = Font(bold=True, color="FFFFFF", size=12)
        row = 4
    return row + 1


def _header(ws, row: int, labels: list[str], col: int = 1, fill=HEADER_FILL) -> None:
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=label)
        c.font, c.fill, c.border = HEADER_FONT, fill, BORDER
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")


def _section(ws, row: int, text: str, col: int = 1) -> None:
    c = ws.cell(row=row, column=col, value=text)
    c.font = Font(bold=True, size=12, color=BLUE)


def _cell_value(v):
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def write_report(issues: pd.DataFrame, evidence: pd.DataFrame, summary: dict, path: Path,
                 statuses: list[str], visits: pd.DataFrame | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    n = len(issues)
    last = FIRST_ROW + max(n, 1) - 1 + 500   # headroom so rows the team adds are still counted

    daily_ws = wb.active
    daily_ws.title = "Daily Update"
    exec_ws = wb.create_sheet("Executive Summary")
    tracker = wb.create_sheet("Detailed Issue Tracker")
    cat_ws = wb.create_sheet("Issue Category Analysis")
    dist_ws = wb.create_sheet("District and CSP Analysis")
    res_ws = wb.create_sheet("Resolution Tracker")
    dq_ws = wb.create_sheet("Data Quality and Verification")
    ev_ws = wb.create_sheet("Evidence Index")
    visits_ws = wb.create_sheet("App Visits")
    lists = wb.create_sheet("Lists")
    lists.sheet_state = "hidden"

    for i, v in enumerate(CATEGORY_NAMES, start=1):
        lists.cell(row=i, column=1, value=v)
    for i, v in enumerate(statuses, start=1):
        lists.cell(row=i, column=2, value=v)
    for i, v in enumerate(["High", "Medium", "Low"], start=1):
        lists.cell(row=i, column=3, value=v)

    _write_daily(daily_ws, issues, summary)
    _write_tracker(tracker, issues, summary, statuses, last)
    _write_executive(exec_ws, issues, summary, statuses, last)
    _write_categories(cat_ws, issues, summary, last)
    _write_district_csp(dist_ws, issues, summary, last)
    _write_resolution(res_ws, issues, summary, statuses, last)
    _write_quality(dq_ws, issues, summary, last)
    _write_evidence(ev_ws, evidence, summary)
    _write_visits(visits_ws, visits if visits is not None else pd.DataFrame(), issues)
    wb.save(path)


# --- Detailed Issue Tracker -----------------------------------------------------------------------
def _write_tracker(ws, issues: pd.DataFrame, summary: dict, statuses: list[str], last: int) -> None:
    ws["A1"] = "Detailed Issue Tracker"
    ws["A1"].font = TITLE_FONT
    note = ("Edit Resolution Status / Assigned To / Resolution Notes here - edits are kept on the next processing run. "
            "Grey columns are supporting detail. Blank distance = not reported (not zero).")
    if summary.get("data_source") == "SYNTHETIC TEST DATA":
        note = "SYNTHETIC TEST DATA - NOT REAL EVIDENCE. " + note
        ws["A2"].font = Font(bold=True, color="C00000")
    else:
        ws["A2"].font = SUB_FONT
    ws["A2"] = note
    hdr = FIRST_ROW - 1
    _header(ws, hdr, TRACKER_COLUMNS)
    _header(ws, hdr, ALL_COLUMNS[len(TRACKER_COLUMNS):], col=len(TRACKER_COLUMNS) + 1, fill=EXTRA_FILL)
    ws.row_dimensions[hdr].height = 32

    for r_i, (_, rec) in enumerate(issues.iterrows(), start=FIRST_ROW):
        for c_i, col in enumerate(ALL_COLUMNS, start=1):
            c = ws.cell(row=r_i, column=c_i, value=_cell_value(rec[col]))
            c.border = BORDER
            c.alignment = WRAP
        ws[f"{COL['Message Date']}{r_i}"].number_format = "dd-mmm-yyyy"
        ws[f"{COL['Message Time']}{r_i}"].number_format = "hh:mm:ss"
        ws[f"{COL['Distance Mismatch (metres)']}{r_i}"].number_format = "#,##0"
        ws[f"{COL['Added On']}{r_i}"].number_format = "dd-mmm-yyyy"
        first_path = str(rec["Evidence Paths"] or "").split("; ")[0]
        if first_path:
            link = ws[f"{COL['Evidence Filename']}{r_i}"]
            link.hyperlink = first_path   # relative to the workbook folder
            link.font = Font(color="0563C1", underline="single")

    for col in ALL_COLUMNS:
        ws.column_dimensions[COL[col]].width = WIDTHS.get(col, 15)
    end_col = get_column_letter(len(ALL_COLUMNS))
    ws.freeze_panes = f"C{FIRST_ROW}"
    ws.auto_filter.ref = f"A{hdr}:{end_col}{FIRST_ROW + max(len(issues), 1) - 1}"

    dv_issue = DataValidation(type="list", formula1=f"=Lists!$A$1:$A${len(CATEGORY_NAMES)}", allow_blank=True)
    dv_status = DataValidation(type="list", formula1=f"=Lists!$B$1:$B${len(statuses)}", allow_blank=False,
                               showErrorMessage=True, errorTitle="Resolution Status",
                               error="Choose a status from the list.")
    dv_conf = DataValidation(type="list", formula1="=Lists!$C$1:$C$3", allow_blank=True)
    for dv, col in ((dv_issue, "Issue Type"), (dv_status, "Resolution Status"), (dv_conf, "Confidence")):
        ws.add_data_validation(dv)
        dv.add(f"{COL[col]}{FIRST_ROW}:{COL[col]}{last}")

    def fill(color):
        return PatternFill("solid", fgColor=color, bgColor=color)

    rs = f"{COL['Resolution Status']}{FIRST_ROW}:{COL['Resolution Status']}{last}"
    for status, color in (("Open", "F8CBAD"), ("In Progress", "FFE699"), ("Escalated", "FF9999"),
                          ("Resolved", "C6EFCE"), ("Closed - Not an Issue", "D9D9D9")):
        ws.conditional_formatting.add(rs, CellIsRule(operator="equal", formula=[f'"{status}"'], fill=fill(color)))
    dup = f"{COL['Duplicate Flag']}{FIRST_ROW}:{COL['Duplicate Flag']}{last}"
    ws.conditional_formatting.add(dup, CellIsRule(operator="equal", formula=['"Possible Duplicate"'], fill=fill("FCE4D6"),
                                                  font=Font(bold=True, color="C55A11")))
    conf = f"{COL['Confidence']}{FIRST_ROW}:{COL['Confidence']}{last}"
    ws.conditional_formatting.add(conf, CellIsRule(operator="equal", formula=['"Low"'], font=Font(bold=True, color="C00000")))
    ws.conditional_formatting.add(conf, CellIsRule(operator="equal", formula=['"High"'], font=Font(bold=True, color="375623")))
    nv_col = COL["Needs Verification"]
    ws.conditional_formatting.add(f"A{FIRST_ROW}:A{last}",
                                  FormulaRule(formula=[f'${nv_col}{FIRST_ROW}="Yes"'], fill=fill("FFF2CC")))
    dist = f"{COL['Distance Mismatch (metres)']}{FIRST_ROW}:{COL['Distance Mismatch (metres)']}{last}"
    ws.conditional_formatting.add(dist, DataBarRule(start_type="num", start_value=0, end_type="max", color="5B9BD5"))
    for col in ("CSP ID", "District", "CSP Name", "Actual Location", "Planned Location", "Visit Status"):
        rng = f"{COL[col]}{FIRST_ROW}:{COL[col]}{last}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{NOT_AVAILABLE}"'],
                                                      font=Font(italic=True, color="A6A6A6")))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"Needs Verification"'],
                                                      font=Font(italic=True, color="C55A11")))


# --- Executive Summary -----------------------------------------------------------------------------
# --- Daily Update (simple sheet to share every day) ----------------------------------------------
def _write_daily(ws, issues: pd.DataFrame, summary: dict) -> None:
    today = date.today()
    ws.sheet_view.showGridLines = False
    widths = [11, 13, 24, 22, 13, 26, 24, 70]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = f"CSP GPS Complaints – Aaj ka update ({today:%d %b %Y})"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (f"WhatsApp group se app-visit-fail complaints, {summary.get('period_start') or '—'} se "
                f"{summary.get('period_end') or '—'} tak. Updated {str(summary.get('generated_at', '')).replace('T', ' ')[:16]}.")
    ws["A2"].font = SUB_FONT
    row = 3
    if summary.get("data_source") == "SYNTHETIC TEST DATA":
        ws["A3"] = "SYNTHETIC TEST DATA - not real complaints"
        ws["A3"].font = Font(bold=True, color="C00000")
        row = 4

    has = len(issues) > 0
    resolved = issues["Resolution Status"].isin(["Resolved", "Closed - Not an Issue"]) if has else pd.Series(dtype=bool)
    not_dup = (issues["Duplicate Flag"] != "Possible Duplicate") if has else pd.Series(dtype=bool)
    new_today = issues[(issues["Added On"] == today) & not_dup] if has else issues
    pending = issues[~resolved & not_dup].sort_values("Message Date") if has else issues
    escalate = pending[pending["Next Step"].astype(str).str.startswith(("Escalate", "Escalated"))] if has else issues

    row += 1
    numbers = [("Aaj nayi", len(new_today)), ("Kul complaints", int(not_dup.sum()) if has else 0),
               ("Pending", len(pending)), ("Escalate karna hai", len(escalate)),
               ("Solved", int((resolved & not_dup).sum()) if has else 0)]
    for i, (label, value) in enumerate(numbers):
        col = 1 + i * 2 if i < 4 else 8
        lab = ws.cell(row=row, column=col, value=label)
        lab.font = Font(bold=True, color="FFFFFF")
        lab.fill = HEADER_FILL if label not in ("Escalate karna hai",) else PatternFill("solid", fgColor="C0392B")
        lab.alignment = Alignment(horizontal="center")
        num = ws.cell(row=row + 1, column=col, value=value)
        num.font = Font(bold=True, size=20, color=NAVY)
        num.alignment = Alignment(horizontal="center")
    row += 3

    def table(title: str, frame: pd.DataFrame, cols: list[tuple[str, callable]], empty: str) -> None:
        nonlocal row
        _section(ws, row, title)
        row += 1
        if frame is None or not len(frame):
            ws.cell(row=row, column=1, value=empty).font = SUB_FONT
            row += 2
            return
        _header(ws, row, [c for c, _ in cols])
        for _, r in frame.iterrows():
            row += 1
            for j, (_, fn) in enumerate(cols, start=1):
                cell = ws.cell(row=row, column=j, value=_cell_value(fn(r)))
                cell.border, cell.alignment = BORDER, WRAP
            ws.cell(row=row, column=2).number_format = "dd-mmm-yyyy"
        row += 2

    table("Aaj ki nayi complaints", new_today, [
        ("No.", lambda r: r["Record ID"]), ("Date", lambda r: r["Message Date"]),
        ("Kisne bheja", lambda r: r["Reported By"]), ("Problem", lambda r: problem_name(r["Issue Type"])),
        ("CSP ID", lambda r: r["CSP ID"]), ("WhatsApp group", lambda r: r["Group/Chat"]),
        ("Status", lambda r: status_name(r["Resolution Status"])), ("Kya karna hai", lambda r: r["Suggested Action"]),
    ], "Aaj koi nayi complaint nahi aayi.")
    table("Pending complaints – sabse purani pehle", pending, [
        ("No.", lambda r: r["Record ID"]), ("Date", lambda r: r["Message Date"]),
        ("Kisne bheja", lambda r: r["Reported By"]), ("Problem", lambda r: problem_name(r["Issue Type"])),
        ("CSP ID", lambda r: r["CSP ID"]), ("Kitne din se", lambda r: r["Days Pending"]),
        ("Next step", lambda r: r["Next Step"]), ("Kya karna hai", lambda r: r["Suggested Action"]),
    ], "Koi complaint pending nahi hai.")

    _section(ws, row, "Problem-wise ginti")
    row += 1
    _header(ws, row, ["Problem", "Complaints", "Pending"])
    counts = issues[not_dup].groupby("Issue Type") if has else None
    if counts is not None:
        for issue_type, grp in sorted(counts, key=lambda kv: -len(kv[1])):
            row += 1
            vals = [problem_name(issue_type), len(grp),
                    int((~grp["Resolution Status"].isin(["Resolved", "Closed - Not an Issue"])).sum())]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=row, column=j, value=v).border = BORDER
    ws.freeze_panes = "A4"


def _write_executive(ws, issues: pd.DataFrame, summary: dict, statuses: list[str], last: int) -> None:
    row = _title(ws, "CSP GPS / Location Issue Report - Executive Summary", summary)
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["E"].width = 34
    ws.column_dimensions["F"].width = 12

    ids = _rng("Record ID", last)
    issue = _rng("Issue Type", last)
    visit = _rng("Visit Status", last)
    unique_csps = issues.loc[issues["CSP ID"] != NOT_AVAILABLE, "CSP ID"].nunique() if len(issues) else 0
    kpis = [
        ("Total reported cases", f"=COUNTA({ids})", None),
        ("Unique CSPs affected (where CSP ID stated)", unique_csps, "computed at generation"),
        ("Cases with no CSP ID stated", f'=COUNTIF({_rng("CSP ID", last)},"{NOT_AVAILABLE}")', None),
        ("GPS / location mismatch cases (mismatch + distance over radius)",
         f'=COUNTIF({issue},"Location Mismatch (Actual vs Planned)")+COUNTIF({issue},"Distance Exceeds Radius")', None),
        ("GPS not detected", f'=COUNTIF({issue},"GPS Not Detected")', None),
        ("Failed or not-updated visits",
         f'=SUMPRODUCT(--((({visit}="Not Completed / Not Updated")+({issue}="Visit Not Completed/Updated"))>0))', None),
        ("Largest reported distance mismatch (m)", f'=IF(COUNT({_rng("Distance Mismatch (metres)", last)})=0,"Not reported",'
                                                   f'MAX({_rng("Distance Mismatch (metres)", last)}))', None),
        ("Cases with a distance reported", f'=COUNT({_rng("Distance Mismatch (metres)", last)})', None),
        ("Records needing manual verification", f'=COUNTIF({_rng("Needs Verification", last)},"Yes")', None),
        ("Possible duplicates (kept, flagged for review)", f'=COUNTIF({_rng("Duplicate Flag", last)},"Possible Duplicate")', None),
    ]
    _section(ws, row, "Key figures")
    row += 1
    _header(ws, row, ["Measure", "Value", "Note"])
    for label, value, note in kpis:
        row += 1
        ws.cell(row=row, column=1, value=label).border = BORDER
        v = ws.cell(row=row, column=2, value=value)
        v.border, v.font = BORDER, Font(bold=True, size=12, color=NAVY)
        v.number_format = "#,##0"
        ws.cell(row=row, column=3, value=note).font = SUB_FONT

    row += 2
    _section(ws, row, "Resolution status")
    row += 1
    _header(ws, row, ["Status", "Cases"])
    status_start = row + 1
    for s in statuses:
        row += 1
        ws.cell(row=row, column=1, value=s).border = BORDER
        ws.cell(row=row, column=2, value=f'=COUNTIF({_rng("Resolution Status", last)},"{s}")').border = BORDER
    pie = PieChart()
    pie.title = "Cases by resolution status"
    pie.add_data(Reference(ws, min_col=2, min_row=status_start - 1, max_row=row), titles_from_data=True)
    pie.set_categories(Reference(ws, min_col=1, min_row=status_start, max_row=row))
    pie.height, pie.width = 7.5, 11
    ws.add_chart(pie, "H5")

    row += 2
    _section(ws, row, "Issue type distribution")
    row += 1
    _header(ws, row, ["Issue type", "Cases"])
    cat_start = row + 1
    for cat in CATEGORY_NAMES:
        row += 1
        ws.cell(row=row, column=1, value=cat).border = BORDER
        ws.cell(row=row, column=2, value=f'=COUNTIF({issue},"{cat}")').border = BORDER
    bar = BarChart()
    bar.type = "bar"
    bar.title = "Cases by issue type"
    bar.add_data(Reference(ws, min_col=2, min_row=cat_start - 1, max_row=row), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=cat_start, max_row=row))
    bar.legend = None
    bar.height, bar.width = 8, 16
    ws.add_chart(bar, "H21")

    row += 2
    _section(ws, row, "Daily trend (by WhatsApp message date)")
    row += 1
    _header(ws, row, ["Date", "Cases"])
    trend_start = row + 1
    for d in _date_span(issues, summary):
        row += 1
        c = ws.cell(row=row, column=1, value=d)
        c.number_format = "dd-mmm-yyyy"
        c.border = BORDER
        ws.cell(row=row, column=2, value=f'=COUNTIF({_rng("Message Date", last)},A{row})').border = BORDER
    if row >= trend_start:
        line = LineChart()
        line.title = "Daily reported cases"
        line.add_data(Reference(ws, min_col=2, min_row=trend_start - 1, max_row=row), titles_from_data=True)
        line.set_categories(Reference(ws, min_col=1, min_row=trend_start, max_row=row))
        line.x_axis.number_format = "dd-mmm"
        line.legend = None
        line.height, line.width = 8, 16
        ws.add_chart(line, "H39")

    row += 2
    _section(ws, row, "Processing summary")
    stats = [
        ("Chat files parsed", len(summary.get("files_parsed", []))),
        ("Files that failed", len(summary.get("files_failed", []))),
        ("Messages parsed (all dates)", summary.get("messages_parsed")),
        ("Messages in reporting period", summary.get("messages_in_range")),
        ("Images processed", summary.get("images_processed")),
        ("Attachments referenced but missing", summary.get("attachments_missing")),
        ("Images not linked to any issue", summary.get("unlinked_images")),
        ("OCR engine", summary.get("ocr_status")),
        ("Data source", summary.get("data_source")),
    ]
    for label, value in stats:
        row += 1
        ws.cell(row=row, column=1, value=label).border = BORDER
        ws.cell(row=row, column=2, value=value).border = BORDER


def _date_span(issues: pd.DataFrame, summary: dict) -> list[date]:
    if not len(issues):
        return []
    start = date.fromisoformat(summary["period_start"]) if summary.get("period_start") else min(issues["Message Date"])
    end = date.fromisoformat(summary["period_end"]) if summary.get("period_end") else max(issues["Message Date"])
    if (end - start).days > 400:
        start = min(issues["Message Date"])
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --- Issue Category Analysis ------------------------------------------------------------------------
def _write_categories(ws, issues: pd.DataFrame, summary: dict, last: int) -> None:
    row = _title(ws, "Issue Category Analysis", summary)
    labels = ["Issue type (primary)", "Cases", "% of cases", "Also mentioned (incl. secondary)",
              "Needs verification", "Possible duplicates", "Cases with distance", "Avg distance (m)",
              "Max distance (m)", "Unique CSPs"]
    _header(ws, row, labels)
    widths = [36, 9, 11, 16, 13, 13, 13, 13, 13, 11]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    issue, dist = _rng("Issue Type", last), _rng("Distance Mismatch (metres)", last)
    start = row + 1
    for cat in CATEGORY_NAMES:
        row += 1
        sub = issues[issues["Issue Type"] == cat] if len(issues) else issues
        values = [
            cat,
            f'=COUNTIF({issue},"{cat}")',
            f'=IF(COUNTA({_rng("Record ID", last)})=0,0,B{row}/COUNTA({_rng("Record ID", last)}))',
            f'=COUNTIF({_rng("All Issue Categories", last)},"*{cat}*")',
            f'=COUNTIFS({issue},"{cat}",{_rng("Needs Verification", last)},"Yes")',
            f'=COUNTIFS({issue},"{cat}",{_rng("Duplicate Flag", last)},"Possible Duplicate")',
            f'=COUNTIFS({issue},"{cat}",{dist},">0")',
            f'=IFERROR(AVERAGEIFS({dist},{issue},"{cat}"),"Not reported")',
            float(sub["Distance Mismatch (metres)"].max()) if len(sub) and sub["Distance Mismatch (metres)"].notna().any() else "Not reported",
            int(sub.loc[sub["CSP ID"] != NOT_AVAILABLE, "CSP ID"].nunique()) if len(sub) else 0,
        ]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c, value=v)
            cell.border = BORDER
        ws.cell(row=row, column=3).number_format = "0.0%"
        ws.cell(row=row, column=8).number_format = "#,##0"
        ws.cell(row=row, column=9).number_format = "#,##0"
    ws.cell(row=row + 2, column=1, value="Max distance and unique CSPs are computed at generation; other columns are live formulas.").font = SUB_FONT
    ws.cell(row=row + 3, column=1, value="Primary issue type follows a fixed priority; a message can mention several issues (column D).").font = SUB_FONT
    bar = BarChart()
    bar.title = "Primary vs. all mentions"
    bar.add_data(Reference(ws, min_col=2, min_row=start - 1, max_row=row), titles_from_data=True)
    bar.add_data(Reference(ws, min_col=4, min_row=start - 1, max_row=row), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=start, max_row=row))
    bar.height, bar.width = 9, 22
    ws.add_chart(bar, f"A{row + 5}")
    ws.freeze_panes = ws.cell(row=start, column=2)


# --- District and CSP Analysis ------------------------------------------------------------------------
def _write_district_csp(ws, issues: pd.DataFrame, summary: dict, last: int) -> None:
    row = _title(ws, "District and CSP Analysis", summary, width=10)
    _section(ws, row, "By district (as stated in messages/screenshots)")
    row += 1
    labels = ["District", "Cases", "Mismatch / distance cases", "Failed visits", "Open", "Resolved",
              "Unique CSPs", "Max distance (m)"]
    _header(ws, row, labels)
    for i, w in enumerate([26, 9, 14, 11, 9, 10, 11, 13, 30, 14], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    district = _rng("District", last)
    issue = _rng("Issue Type", last)
    status = _rng("Resolution Status", last)
    start = row + 1
    districts = (issues["District"].value_counts().index.tolist() if len(issues) else [])
    for d in districts:
        row += 1
        sub = issues[issues["District"] == d]
        failed = int(((sub["Visit Status"] == "Not Completed / Not Updated") |
                      (sub["Issue Type"] == "Visit Not Completed/Updated")).sum())
        values = [
            d if d != NOT_AVAILABLE else NOT_AVAILABLE,
            f'=COUNTIF({district},A{row})',
            f'=COUNTIFS({district},A{row},{issue},"Location Mismatch (Actual vs Planned)")+'
            f'COUNTIFS({district},A{row},{issue},"Distance Exceeds Radius")',
            failed,
            f'=COUNTIFS({district},A{row},{status},"Open")',
            f'=COUNTIFS({district},A{row},{status},"Resolved")',
            int(sub.loc[sub["CSP ID"] != NOT_AVAILABLE, "CSP ID"].nunique()),
            float(sub["Distance Mismatch (metres)"].max()) if sub["Distance Mismatch (metres)"].notna().any() else "Not reported",
        ]
        for c, v in enumerate(values, start=1):
            ws.cell(row=row, column=c, value=v).border = BORDER
        ws.cell(row=row, column=8).number_format = "#,##0"
    if not districts:
        row += 1
        ws.cell(row=row, column=1, value="No records")
    elif len(districts) > 0:
        bar = BarChart()
        bar.type = "bar"
        bar.title = "Cases by district"
        bar.add_data(Reference(ws, min_col=2, min_row=start - 1, max_row=row), titles_from_data=True)
        bar.set_categories(Reference(ws, min_col=1, min_row=start, max_row=row))
        bar.legend = None
        bar.height, bar.width = max(6, 0.6 * len(districts) + 3), 14
        ws.add_chart(bar, "K5")

    row += 3
    _section(ws, row, "By CSP (only where a CSP ID is stated)")
    row += 1
    labels = ["CSP ID", "Cases", "CSP Name", "District", "First reported", "Last reported", "Max distance (m)",
              "Open cases", "Issue types", "Groups"]
    _header(ws, row, labels)
    csp = issues[issues["CSP ID"] != NOT_AVAILABLE] if len(issues) else issues
    if len(csp):
        grouped = csp.groupby("CSP ID")
        order = grouped.size().sort_values(ascending=False).index
        for cid in order:
            sub = grouped.get_group(cid)
            row += 1
            names = [x for x in sub["CSP Name"].unique() if x != NOT_AVAILABLE]
            dists = [x for x in sub["District"].unique() if x != NOT_AVAILABLE]
            values = [
                cid,
                f'=COUNTIF({_rng("CSP ID", last)},A{row})',
                "; ".join(names) or NOT_AVAILABLE,
                "; ".join(dists) or NOT_AVAILABLE,
                min(sub["Message Date"]), max(sub["Message Date"]),
                float(sub["Distance Mismatch (metres)"].max()) if sub["Distance Mismatch (metres)"].notna().any() else "Not reported",
                f'=COUNTIFS({_rng("CSP ID", last)},A{row},{status},"Open")',
                "; ".join(sorted(sub["Issue Type"].unique())),
                "; ".join(sorted(sub["Group/Chat"].unique())),
            ]
            for c, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=c, value=v)
                cell.border = BORDER
            ws.cell(row=row, column=5).number_format = "dd-mmm-yyyy"
            ws.cell(row=row, column=6).number_format = "dd-mmm-yyyy"
            ws.cell(row=row, column=7).number_format = "#,##0"
    else:
        row += 1
        ws.cell(row=row, column=1, value="No CSP IDs were stated in the reports.")
    no_id = int((issues["CSP ID"] == NOT_AVAILABLE).sum()) if len(issues) else 0
    ws.cell(row=row + 2, column=1, value=f"{no_id} case(s) have no CSP ID and are excluded from this table "
                                         f"(see Data Quality sheet).").font = SUB_FONT


# --- Resolution Tracker ---------------------------------------------------------------------------------
def _write_resolution(ws, issues: pd.DataFrame, summary: dict, statuses: list[str], last: int) -> None:
    row = _title(ws, "Resolution Tracker", summary, width=10)
    ws.cell(row=row, column=1, value="Update statuses in the Detailed Issue Tracker; this sheet recalculates.").font = SUB_FONT
    row += 2
    _section(ws, row, "Status by issue type (live)")
    row += 1
    _header(ws, row, ["Issue type"] + statuses + ["Total"])
    ws.column_dimensions["A"].width = 36
    for i in range(2, len(statuses) + 3):
        ws.column_dimensions[get_column_letter(i)].width = 14
    for cat in CATEGORY_NAMES:
        row += 1
        ws.cell(row=row, column=1, value=cat).border = BORDER
        for j, s in enumerate(statuses, start=2):
            ws.cell(row=row, column=j, value=f'=COUNTIFS({_rng("Issue Type", last)},$A{row},'
                                             f'{_rng("Resolution Status", last)},"{s}")').border = BORDER
        end = get_column_letter(len(statuses) + 1)
        ws.cell(row=row, column=len(statuses) + 2, value=f"=SUM(B{row}:{end}{row})").border = BORDER

    row += 3
    _section(ws, row, "Cases not yet resolved - what to do (oldest first)")
    row += 1
    labels = ["Record ID", "Message Date", "Age (days)", "Group/Chat", "CSP ID", "District", "Issue Type",
              "Resolution Status", "Next Step", "Suggested Action", "Assigned To", "Resolution Evidence"]
    _header(ws, row, labels)
    for i, w in enumerate([11, 13, 10, 24, 13, 14, 30, 16, 26, 60, 16, 50], start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(ws.column_dimensions[get_column_letter(i)].width or 0, w)
    pending = issues[~issues["Resolution Status"].isin(["Resolved", "Closed - Not an Issue"])] if len(issues) else issues
    for _, r in pending.iterrows():
        row += 1
        values = [r["Record ID"], r["Message Date"], f"=TODAY()-B{row}", r["Group/Chat"], r["CSP ID"], r["District"],
                  r["Issue Type"], r["Resolution Status"], r["Next Step"], r["Suggested Action"], r["Assigned To"],
                  r["Resolution Evidence"]]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c, value=_cell_value(v))
            cell.border = BORDER
        ws.cell(row=row, column=2).number_format = "dd-mmm-yyyy"
        ws.cell(row=row, column=3).number_format = "0"
    if len(pending):
        ws.conditional_formatting.add(f"C{row - len(pending) + 1}:C{row}",
                                      CellIsRule(operator="greaterThan", formula=["7"],
                                                 fill=PatternFill("solid", bgColor="F8CBAD")))
    resolved = issues[issues["Auto Resolution Status"] == "Resolved"] if len(issues) else issues
    row += 2
    ws.cell(row=row, column=1, value=f"{len(resolved)} case(s) were auto-marked Resolved from a follow-up message; "
                                     f"the quoted message is in Resolution Evidence - please confirm.").font = SUB_FONT


# --- Data Quality ------------------------------------------------------------------------------------------
def _write_quality(ws, issues: pd.DataFrame, summary: dict, last: int) -> None:
    row = _title(ws, "Data Quality and Verification", summary, width=9)
    _section(ws, row, "Completeness")
    row += 1
    _header(ws, row, ["Check", "Records", "% of cases"])
    for i, w in enumerate([44, 12, 12, 24, 26, 14, 60, 34, 34], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    total = max(len(issues), 1)

    def cnt(mask) -> int:
        return int(mask.sum()) if len(issues) else 0

    checks = [
        ("CSP ID not stated", cnt(issues["CSP ID"] == NOT_AVAILABLE) if len(issues) else 0),
        ("CSP name not stated", cnt(issues["CSP Name"] == NOT_AVAILABLE) if len(issues) else 0),
        ("District not stated", cnt(issues["District"] == NOT_AVAILABLE) if len(issues) else 0),
        ("No distance reported (blank, not zero)", cnt(issues["Distance Mismatch (metres)"].isna()) if len(issues) else 0),
        ("Actual location not identified", cnt(issues["Actual Location"].isin([NOT_AVAILABLE, "Needs Verification"])) if len(issues) else 0),
        ("Screenshot not read by OCR", cnt(issues["Verification Reasons"].str.contains("not read by OCR")) if len(issues) else 0),
        ("Media missing from export", cnt(issues["Verification Reasons"].str.contains("Media missing")) if len(issues) else 0),
        ("Possible duplicates", cnt(issues["Duplicate Flag"] == "Possible Duplicate") if len(issues) else 0),
        ("Low confidence classification", cnt(issues["Confidence"] == "Low") if len(issues) else 0),
        ("Needs manual verification", cnt(issues["Needs Verification"] == "Yes") if len(issues) else 0),
    ]
    for label, v in checks:
        row += 1
        ws.cell(row=row, column=1, value=label).border = BORDER
        ws.cell(row=row, column=2, value=v).border = BORDER
        c = ws.cell(row=row, column=3, value=v / total)
        c.number_format, c.border = "0%", BORDER

    row += 2
    _section(ws, row, "Input files")
    row += 1
    _header(ws, row, ["File", "Result"])
    for f in summary.get("files_parsed", []):
        row += 1
        ws.cell(row=row, column=1, value=f)
        ws.cell(row=row, column=2, value="Parsed")
    for f in summary.get("files_failed", []):
        row += 1
        ws.cell(row=row, column=1, value=f["file"])
        ws.cell(row=row, column=2, value=f"FAILED: {f['error']}").font = Font(color="C00000", bold=True)
    if not summary.get("files_parsed") and not summary.get("files_failed"):
        row += 1
        ws.cell(row=row, column=1, value="No input files found")

    row += 2
    _section(ws, row, "Records needing verification (highest priority first)")
    row += 1
    labels = ["Record ID", "Priority", "Message Date", "Group/Chat", "Reported By", "Issue Type",
              "Verification Reasons", "Message Reference", "Evidence Filename"]
    _header(ws, row, labels)
    hdr = row
    if len(issues):
        need = issues[issues["Needs Verification"] == "Yes"].copy()
        need["_p"] = need["Verification Priority"].map({"High": 0, "Medium": 1, "Low": 2}).fillna(3)
        for _, r in need.sort_values(["_p", "Message Date"]).iterrows():
            row += 1
            for c, col in enumerate(["Record ID", "Verification Priority", "Message Date", "Group/Chat", "Reported By",
                                     "Issue Type", "Verification Reasons", "Message Reference", "Evidence Filename"], start=1):
                cell = ws.cell(row=row, column=c, value=_cell_value(r[col]))
                cell.border, cell.alignment = BORDER, WRAP
            ws.cell(row=row, column=3).number_format = "dd-mmm-yyyy"
    if row > hdr:
        ws.auto_filter.ref = f"A{hdr}:I{row}"
        ws.conditional_formatting.add(f"B{hdr + 1}:B{row}", CellIsRule(operator="equal", formula=['"High"'],
                                      fill=PatternFill("solid", bgColor="F8CBAD")))
        ws.conditional_formatting.add(f"B{hdr + 1}:B{row}", CellIsRule(operator="equal", formula=['"Medium"'],
                                      fill=PatternFill("solid", bgColor="FFE699")))


# --- Evidence Index ---------------------------------------------------------------------------------------
def _write_evidence(ws, evidence: pd.DataFrame, summary: dict) -> None:
    row = _title(ws, "Evidence Index", summary, width=len(evidence.columns))
    ws.cell(row=row, column=1, value="Paths are relative to the folder containing this workbook. "
                                     "Images not linked to an issue are listed for manual review.").font = SUB_FONT
    row += 1
    _header(ws, row, list(evidence.columns))
    hdr = row
    widths = [34, 11, 24, 12, 10, 18, 44, 9, 18, 18, 16, 50, 34, 40]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for _, r in evidence.iterrows():
        row += 1
        for c, col in enumerate(evidence.columns, start=1):
            cell = ws.cell(row=row, column=c, value=_cell_value(r[col]))
            cell.border, cell.alignment = BORDER, WRAP
        ws.cell(row=row, column=4).number_format = "dd-mmm-yyyy"
        ws.cell(row=row, column=5).number_format = "hh:mm:ss"
        if r["Relative Path"]:
            link = ws.cell(row=row, column=7)
            link.hyperlink = r["Relative Path"]
            link.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = ws.cell(row=hdr + 1, column=2)
    if row > hdr:
        ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(evidence.columns))}{row}"
        ws.conditional_formatting.add(f"N{hdr + 1}:N{row}", FormulaRule(formula=[f'LEFT(N{hdr + 1},10)="Not linked"'],
                                      fill=PatternFill("solid", bgColor="FFF2CC")))


# --- App Visits (SBI CSP Visit App report) -------------------------------------------------------------
def _write_visits(ws, visits: pd.DataFrame, issues: pd.DataFrame) -> None:
    ws["A1"] = "App Visits – visits recorded in the SBI CSP Visit App"
    ws["A1"].font = TITLE_FONT
    for i, w in enumerate([13, 13, 16, 14, 16, 20, 11, 11, 13, 13, 36], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    if visits is None or visits.empty:
        ws["A3"] = "No visit report loaded. Put the app's visit report (.xlsx) in input/visit_reports/ or in Downloads."
        ws["A3"].font = SUB_FONT
        return
    u, zero = done_csps(visits), score_zero_csps(visits)   # each CSP once
    if u.empty:
        u = visits[visits["Duplicate Row"] == "No"]
    files = sorted({Path(f).name for f in visits["Source File"]})
    ws["A2"] = (f"Period {min(u['Visit Date']):%d %b %Y} – {max(u['Visit Date']):%d %b %Y} · latest visit "
                f"{max(u['Visit Date']):%A %d %b %Y} · {len(u)} done visits from {len(files)} report file(s): "
                f"{', '.join(files)} (each CSP counted once; {len(zero)} CSP(s) with only score-0 visits not counted, listed below)")
    ws["A2"].font = SUB_FONT
    row = 4
    _header(ws, row, ["Done visits (CSPs)", "Score 0 (not done)", "DC users", "Avg score", "Score below 80"])
    row += 1
    vals = [len(u), len(zero), u["DC User"].nunique(), round(float(u["Visit Score"].mean()), 1),
            int((u["Visit Score"] < 80).sum())]
    for j, v in enumerate(vals, start=1):
        c = ws.cell(row=row, column=j, value=v)
        c.font, c.border, c.alignment = Font(bold=True, size=14, color=NAVY), BORDER, Alignment(horizontal="center")

    row += 2
    _section(ws, row, "By circle")
    _section(ws, row, "By DC (app user)", col=5)
    row += 1
    _header(ws, row, ["Circle", "Visits", "Avg score"])
    _header(ws, row, ["DC user", "DC name", "Visits", "Avg score"], col=5)
    by_c = u.groupby("Circle").agg(Visits=("CSP Code", "count"), Score=("Visit Score", "mean")).sort_values("Visits", ascending=False)
    by_u = u.groupby(["DC User", "DC Name"]).agg(Visits=("CSP Code", "count"), Score=("Visit Score", "mean")).sort_values("Visits", ascending=False)
    start = row + 1
    for k, (circle, r) in enumerate(by_c.iterrows()):
        for j, v in enumerate([circle, int(r["Visits"]), round(float(r["Score"]), 1)], start=1):
            ws.cell(row=start + k, column=j, value=v).border = BORDER
    for k, ((user, name), r) in enumerate(by_u.iterrows()):
        for j, v in enumerate([user, name, int(r["Visits"]), round(float(r["Score"]), 1)], start=5):
            ws.cell(row=start + k, column=j, value=v).border = BORDER
    row = start + max(len(by_c), len(by_u)) + 1

    _section(ws, row, "By visit date (newest first)")
    row += 1
    _header(ws, row, ["Date", "Day", "Visits", "CSPs", "DC users", "Avg score", "Score < 80"])
    for d, g in sorted(u.groupby("Visit Date"), key=lambda kv: kv[0], reverse=True):
        row += 1
        vals = [d, f"{d:%A}", len(g), g["CSP Code"].nunique(), g["DC User"].nunique(),
                round(float(g["Visit Score"].mean()), 1), int((g["Visit Score"] < 80).sum())]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=row, column=j, value=v).border = BORDER
        ws.cell(row=row, column=1).number_format = "dd-mmm-yyyy"
    row += 2

    if len(zero):
        _section(ws, row, f"Score 0 visits – recorded in the app but not completed ({len(zero)}, not counted above)")
        row += 1
        _header(ws, row, ["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name", "Visit Score"])
        for _, r in zero.sort_values("Visit Date").iterrows():
            row += 1
            for j, col in enumerate(["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name",
                                     "Visit Score"], start=1):
                c = ws.cell(row=row, column=j, value=_cell_value(r[col]))
                c.border, c.fill = BORDER, PatternFill("solid", fgColor="F8CBAD")
            ws.cell(row=row, column=2).number_format = "dd-mmm-yyyy"
        row += 2

    _section(ws, row, "Complaint CSPs – was the visit later recorded in the app?")
    row += 1
    _header(ws, row, ["Record ID", "Complaint date", "CSP ID", "Problem", "Status", "App visit on", "App score"])
    cmp_ = issues[(issues["Duplicate Flag"] != "Possible Duplicate") & (issues["CSP ID"] != NOT_AVAILABLE)] if len(issues) else issues
    for _, r in cmp_.sort_values("Message Date").iterrows():
        row += 1
        visited = r.get("App Visit After Complaint")
        vals = [r["Record ID"], r["Message Date"], r["CSP ID"], problem_name(r["Issue Type"]),
                status_name(r["Resolution Status"]), visited if visited else "Not in app report",
                r.get("App Visit Score")]
        for j, v in enumerate(vals, start=1):
            c = ws.cell(row=row, column=j, value=_cell_value(v))
            c.border = BORDER
        ws.cell(row=row, column=2).number_format = "dd-mmm-yyyy"
        ws.cell(row=row, column=6).number_format = "dd-mmm-yyyy"
        if not visited:
            ws.cell(row=row, column=6).font = Font(bold=True, color="C0392B")

    row += 3
    _section(ws, row, "All app visits")
    row += 1
    cols = ["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name", "Visit Score", "Visit Done",
            "Compliance Completed", "Closures Completed", "Duplicate Row"]
    _header(ws, row, cols)
    hdr = row
    for _, r in visits.iterrows():
        row += 1
        for j, col in enumerate(cols, start=1):
            ws.cell(row=row, column=j, value=_cell_value(r[col])).border = BORDER
        ws.cell(row=row, column=2).number_format = "dd-mmm-yyyy"
    ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(cols))}{row}"
    ws.conditional_formatting.add(f"G{hdr + 1}:G{row}", CellIsRule(operator="lessThan", formula=["80"],
                                  fill=PatternFill("solid", bgColor="F8CBAD")))
