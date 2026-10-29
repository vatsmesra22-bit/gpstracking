"""SBI CSP Visit App reports (Excel exports of visits that were recorded in the app).

Expected columns (as in "Visit report 1st Oct to 19th Oct.xlsx"):
CSP Code, BC code, Circle Name, Link Branch Code, Visit Date, Visit Score,
Compliance Completed, Closures Completed, username

Rows are never dropped. Exact repeats are kept and marked `Duplicate Row = Yes` so totals can count
each visit once (one CSP, one date, one app user). When repeats differ, the row with the best score is the one
counted.

`Visit Done = No` marks a visit whose score is 0: the app recorded it, but it was not completed, so it is not
counted as a done visit and never closes a complaint. A missing score is treated as done.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import NOT_AVAILABLE

log = logging.getLogger(__name__)

COLUMN_ALIASES = {
    "csp code": "CSP Code", "bc code": "BC Code", "circle name": "Circle", "link branch code": "Branch Code",
    "visit date": "Visit Date", "visit score": "Visit Score", "compliance completed": "Compliance Completed",
    "closures completed": "Closures Completed", "username": "DC User",
}
VISIT_COLUMNS = ["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name", "Visit Score",
                 "Visit Done", "Compliance Completed", "Closures Completed", "BC Code", "Duplicate Row", "Source File"]


def _load_users(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["username"].strip(): r.get("dc_name", "").strip() for r in csv.DictReader(f) if r.get("username")}


def load_visit_reports(folder: Path, project_dir: Path, users_csv: Path | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Read every .xlsx/.csv in `folder`. Returns (visits, failures)."""
    frames, failed = [], []
    if not folder.exists():
        return pd.DataFrame(columns=VISIT_COLUMNS), failed
    users = _load_users(users_csv)
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {".xlsx", ".xls", ".csv"} or path.name.startswith("~$"):
            continue
        rel = path.relative_to(project_dir).as_posix() if path.is_relative_to(project_dir) else path.name
        try:
            raw = (pd.read_csv(path, dtype=str) if path.suffix.lower() == ".csv"
                   else pd.read_excel(path, dtype=str))
        except Exception as exc:
            log.warning("Could not read visit report %s: %s", rel, exc)
            failed.append({"file": rel, "error": str(exc)})
            continue
        raw = raw.rename(columns={c: COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip()) for c in raw.columns})
        missing = {"CSP Code", "Visit Date"} - set(raw.columns)
        if missing:
            failed.append({"file": rel, "error": f"missing columns: {', '.join(sorted(missing))}"})
            continue
        raw["Source File"] = rel
        frames.append(raw)
        log.info("Visit report %s: %d rows", rel, len(raw))
    if not frames:
        return pd.DataFrame(columns=VISIT_COLUMNS), failed

    v = pd.concat(frames, ignore_index=True)
    for col in VISIT_COLUMNS:
        if col not in v.columns:
            v[col] = NOT_AVAILABLE
    v["CSP Code"] = v["CSP Code"].astype(str).str.strip().str.upper()
    v["Circle"] = v["Circle"].fillna(NOT_AVAILABLE).astype(str).str.strip().str.title()
    v["DC User"] = v["DC User"].fillna(NOT_AVAILABLE).astype(str).str.strip()
    v["DC Name"] = v["DC User"].map(users).fillna("").replace("", NOT_AVAILABLE)
    v["Visit Date"] = pd.to_datetime(v["Visit Date"], errors="coerce").dt.date
    v["Visit Score"] = pd.to_numeric(v["Visit Score"], errors="coerce")
    for col in ("Compliance Completed", "Closures Completed"):
        v[col] = v[col].fillna(NOT_AVAILABLE).astype(str).str.strip().str.upper()
    v["Visit Done"] = (v["Visit Score"].isna() | (v["Visit Score"] > 0)).map({True: "Yes", False: "No"})
    # best score first, so that of repeated rows the completed one is the one counted
    v = v.sort_values("Visit Score", ascending=False, na_position="last", kind="stable")
    v["Duplicate Row"] = v.duplicated(["CSP Code", "Visit Date", "DC User"], keep="first").map({True: "Yes", False: "No"})
    v = v[VISIT_COLUMNS].sort_values(["Visit Date", "CSP Code"]).reset_index(drop=True)
    return v, failed


def done_visits(visits: pd.DataFrame) -> pd.DataFrame:
    """Each visit once, only the completed ones (score above 0)."""
    if visits.empty:
        return visits
    done = visits["Visit Done"] != "No" if "Visit Done" in visits.columns else True
    return visits[(visits["Duplicate Row"] == "No") & done]


def score_zero_visits(visits: pd.DataFrame) -> pd.DataFrame:
    """Each visit once, only those recorded with score 0 (not completed)."""
    if visits.empty or "Visit Done" not in visits.columns:
        return visits.iloc[0:0]
    return visits[(visits["Duplicate Row"] == "No") & (visits["Visit Done"] == "No")]


def done_csps(visits: pd.DataFrame) -> pd.DataFrame:
    """One row per CSP that has a completed visit (its latest one). This is the headline "visits done" count:
    a CSP visited on two days is counted once, as the manager counts it."""
    d = done_visits(visits)
    if d.empty:
        return d
    return d.sort_values("Visit Date").drop_duplicates("CSP Code", keep="last")


def score_zero_csps(visits: pd.DataFrame) -> pd.DataFrame:
    """One row per CSP whose visits all have score 0, i.e. no completed visit yet."""
    z = score_zero_visits(visits)
    if z.empty:
        return z
    z = z[~z["CSP Code"].isin(set(done_visits(visits)["CSP Code"]))]
    return z.sort_values("Visit Date").drop_duplicates("CSP Code", keep="last")


def link_visits(issues: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    """For each complaint, the first app visit recorded for the same CSP on or after the complaint date."""
    out = issues.copy()
    out["App Visit After Complaint"] = None
    out["App Visit Score"] = None
    if visits.empty or out.empty:
        return out
    uniq = done_visits(visits)
    by_csp = {k: g.sort_values("Visit Date") for k, g in uniq.groupby("CSP Code")}
    for i, r in out.iterrows():
        g = by_csp.get(str(r["CSP ID"]).upper())
        if g is None:
            continue
        d = r["Message Date"]
        d = d if isinstance(d, date) else datetime.fromisoformat(str(d)).date()
        later = g[g["Visit Date"] >= d]
        if len(later):
            out.at[i, "App Visit After Complaint"] = later.iloc[0]["Visit Date"]
            out.at[i, "App Visit Score"] = later.iloc[0]["Visit Score"]
    return out
