"""Files added from the dashboard's upload box (on when CSP_UPLOADS=1).

A WhatsApp export (.zip / .txt) goes to input/, an SBI CSP Visit App report (.xlsx / .csv) to
input/visit_reports/. Each file is checked first, and a file whose content is already there is skipped, so
uploading the same export twice changes nothing. After saving, `run_report()` rebuilds everything exactly like
`process.py` from the command line.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .locations import is_location_list
from .visits import COLUMN_ALIASES

log = logging.getLogger(__name__)

EXPORT_SUFFIXES = (".zip", ".txt")
REPORT_SUFFIXES = (".xlsx", ".csv")
MAX_BYTES = 10 * 1024 * 1024   # the indev nginx route accepts up to 10 MB per request
WHATSAPP_LINE = re.compile(r"\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}")


@dataclass
class UploadResult:
    name: str
    ok: bool          # saved as a new file
    message: str      # short Hinglish line for the dashboard


def uploads_enabled(env: dict) -> bool:
    return str(env.get("CSP_UPLOADS", "")).strip().lower() in ("1", "true", "yes", "on")


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^\w .()\-]", "_", base).strip(" .")
    return base or "upload"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _looks_like_export(name: str, data: bytes) -> bool:
    if name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.namelist():
                    if member.lower().endswith(".txt"):
                        head = z.read(member)[:20000].decode("utf-8", "ignore")
                        if WHATSAPP_LINE.search(head):
                            return True
        except zipfile.BadZipFile:
            return False
        return False
    return bool(WHATSAPP_LINE.search(data[:20000].decode("utf-8", "ignore")))


def _looks_like_visit_report(name: str, data: bytes) -> bool:
    try:
        frame = (pd.read_csv(io.BytesIO(data), nrows=5, dtype=str) if name.lower().endswith(".csv")
                 else pd.read_excel(io.BytesIO(data), nrows=5, dtype=str))
    except Exception:
        return False
    cols = {COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip()) for c in frame.columns}
    return {"CSP Code", "Visit Date"} <= cols


def _looks_like_location_list(name: str, data: bytes) -> bool:
    try:
        frame = (pd.read_csv(io.BytesIO(data), nrows=5, dtype=str) if name.lower().endswith(".csv")
                 else pd.read_excel(io.BytesIO(data), nrows=5, dtype=str))
    except Exception:
        return False
    return is_location_list(frame)


def save_upload(name: str, data: bytes, input_dir: Path) -> UploadResult:
    """Check one uploaded file and store it where the pipeline reads it."""
    safe = _safe_name(name)
    suffix = Path(safe).suffix.lower()
    if len(data) > MAX_BYTES:
        return UploadResult(safe, False, "File 10 MB se badi hai. WhatsApp export 'Without media' karke daaliye.")
    if suffix in EXPORT_SUFFIXES:
        if not _looks_like_export(safe, data):
            return UploadResult(safe, False, "Yeh WhatsApp chat export nahi lag raha (chat ki .txt nahi mili).")
        dest_dir, kind = input_dir, "WhatsApp export"
    elif suffix in REPORT_SUFFIXES and _looks_like_location_list(safe, data):
        dest_dir, kind = input_dir / "csp_locations", "CSP location list"
    elif suffix in REPORT_SUFFIXES:
        if not _looks_like_visit_report(safe, data):
            return UploadResult(safe, False, "Yeh SBI visit report nahi lag rahi ('CSP Code' aur 'Visit Date' column nahi mile).")
        dest_dir, kind = input_dir / "visit_reports", "Visit report"
    else:
        return UploadResult(safe, False, "Sirf .zip / .txt (WhatsApp) ya .xlsx / .csv (visit report / CSP location list) daaliye.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    sha = _sha(data)
    for existing in dest_dir.iterdir():
        if existing.is_file() and existing.suffix.lower() == suffix and _sha(existing.read_bytes()) == sha:
            return UploadResult(safe, False, f"Yeh file pehle se hai ({existing.name}), dobara nahi joda.")
    dest = dest_dir / safe
    if dest.exists():
        dest = dest_dir / f"{Path(safe).stem}__{sha[:8]}{suffix}"
    dest.write_bytes(data)
    log.info("Upload saved: %s (%d bytes) -> %s", safe, len(data), dest)
    return UploadResult(dest.name, True, f"{kind} joda gaya.")


def log_upload(log_dir: Path, results: list[UploadResult]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "uploads.log", "a", encoding="utf-8") as f:
        for r in results:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{'saved' if r.ok else 'skipped'}\t{r.name}\t{r.message}\n")


def run_report(project_dir: Path, timeout: int = 600) -> tuple[bool, str]:
    """Rebuild the report from input/ (same as `python process.py`). Returns (ok, last lines of output)."""
    proc = subprocess.run([sys.executable, str(project_dir / "process.py")], cwd=project_dir,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    return proc.returncode == 0, tail
