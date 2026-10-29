"""CSP location list: the lat/long each CSP sent (a CSV / Excel in input/csp_locations/).

Expected columns (any order, case-insensitive): CSP Code, CSP Name, Number, Latitude, Longitude, Received (UTC),
Reply. A single "Lat, Long" column is also accepted. Phone numbers are masked on load. Each CSP keeps its latest
row. Points outside India are flagged for checking, never dropped.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from . import NOT_AVAILABLE
from .extract import mask_phone

log = logging.getLogger(__name__)

LOCATION_COLUMNS = ["CSP Code", "CSP Name", "Number", "Latitude", "Longitude", "Received", "Reply", "Location Check",
                    "Source File"]
ALIASES = {"csp code": "CSP Code", "csp": "CSP Code", "csp id": "CSP Code", "name": "CSP Name", "csp name": "CSP Name",
           "number": "Number", "mobile": "Number", "phone": "Number", "latitude": "Latitude", "lat": "Latitude",
           "longitude": "Longitude", "long": "Longitude", "lng": "Longitude", "received (utc)": "Received",
           "received": "Received", "reply": "Reply", "other reply": "Reply", "lat, long": "LatLong",
           "location (lat, long)": "LatLong", "location": "LatLong"}
INDIA = ((6.0, 37.6), (68.0, 97.5))


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={c: ALIASES.get(str(c).strip().lower(), str(c).strip()) for c in frame.columns})


def is_location_list(frame: pd.DataFrame) -> bool:
    cols = set(normalise_columns(frame).columns)
    return "CSP Code" in cols and ({"Latitude", "Longitude"} <= cols or "LatLong" in cols)


def load_locations(folder: Path, project_dir: Path) -> pd.DataFrame:
    """config/csp_locations.csv (CSP code + lat/long, shipped with the app) first, then any uploaded lists in
    `folder`; a later list wins for the same CSP."""
    frames = []
    paths = [project_dir / "config" / "csp_locations.csv"]
    if folder.exists():
        paths += sorted(folder.iterdir())
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx"} or path.name.startswith("~$"):
            continue
        try:
            raw = pd.read_csv(path, dtype=str) if path.suffix.lower() == ".csv" else pd.read_excel(path, dtype=str)
        except Exception as exc:
            log.warning("Could not read CSP location list %s: %s", path.name, exc)
            continue
        raw = normalise_columns(raw)
        if "LatLong" in raw.columns and "Latitude" not in raw.columns:
            parts = raw["LatLong"].astype(str).str.split(",", n=1, expand=True)
            raw["Latitude"], raw["Longitude"] = parts[0], parts.get(1)
        raw["Source File"] = path.relative_to(project_dir).as_posix() if path.is_relative_to(project_dir) else path.name
        frames.append(raw)
    if not frames:
        return pd.DataFrame(columns=LOCATION_COLUMNS)
    loc = pd.concat(frames, ignore_index=True)
    for col in LOCATION_COLUMNS:
        if col not in loc.columns:
            loc[col] = None
    loc["CSP Code"] = loc["CSP Code"].astype(str).str.strip().str.upper()
    loc = loc[loc["CSP Code"].str.match(r"^\w{6,10}$")]
    loc["Latitude"] = pd.to_numeric(loc["Latitude"].astype(str).str.strip(), errors="coerce")
    loc["Longitude"] = pd.to_numeric(loc["Longitude"].astype(str).str.strip(), errors="coerce")
    loc["Number"] = loc["Number"].fillna("").astype(str).map(mask_phone).replace("", NOT_AVAILABLE)
    for col in ("CSP Name", "Reply", "Received"):
        loc[col] = loc[col].fillna("").astype(str).str.strip().replace({"nan": ""})
    (lat_lo, lat_hi), (lon_lo, lon_hi) = INDIA
    ok = loc["Latitude"].between(lat_lo, lat_hi) & loc["Longitude"].between(lon_lo, lon_hi)
    loc["Location Check"] = ok.map({True: "OK", False: "Check – not in India / missing"})
    loc = loc.drop_duplicates("CSP Code", keep="last").sort_values("CSP Code", kind="stable")
    return loc[LOCATION_COLUMNS].reset_index(drop=True)


def merge_locations(loc: pd.DataFrame, issues: pd.DataFrame, done: pd.DataFrame) -> pd.DataFrame:
    """Add each CSP's complaints and latest completed app visit, plus a Google Maps link."""
    out = loc.copy()
    if out.empty:
        return out.assign(**{"Complaints": 0, "Pending Complaints": 0, "Last App Visit": None, "Map": ""})
    uniq = issues[issues.get("Duplicate Flag", "") != "Possible Duplicate"] if len(issues) else issues
    by_csp = uniq.groupby("CSP ID") if len(uniq) else None
    out["Complaints"] = out["CSP Code"].map(by_csp.size() if by_csp is not None else {}).fillna(0).astype(int)
    pending = (uniq[uniq["Resolution Status"].isin(["Open", "In Progress", "Escalated"])].groupby("CSP ID").size()
               if len(uniq) else {})
    out["Pending Complaints"] = out["CSP Code"].map(pending).fillna(0).astype(int)
    last = done.groupby("CSP Code")["Visit Date"].max() if len(done) else {}
    out["Last App Visit"] = out["CSP Code"].map(last)
    out["Map"] = [f"https://maps.google.com/?q={a},{b}" if pd.notna(a) and pd.notna(b) else ""
                  for a, b in zip(out["Latitude"], out["Longitude"], strict=True)]
    return out
