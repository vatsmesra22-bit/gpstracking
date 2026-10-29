"""End-to-end processing: parse -> bundle -> evidence/OCR -> extract -> dedupe -> resolve -> outputs."""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import NEEDS_VERIFICATION, NOT_AVAILABLE
from .agent_brief import build_brief, write_brief
from .config import Settings
from .extract import (
    LOCATION_TERMS,
    OTHER,
    VISIT_DONE,
    VISIT_FAILED,
    csp_codes,
    extract,
    infer_role,
    is_acknowledgement,
    is_escalation,
    is_relevant,
    is_resolution_reply,
    mask_phone,
)
from .media import Evidence, OcrEngine, phash_distance, process_attachment
from .parser import Message, discover_and_parse
from .state import StateStore
from .visits import done_csps, done_visits, load_visit_reports, score_zero_csps

log = logging.getLogger(__name__)

TRACKER_COLUMNS = [
    "Record ID", "Message Date", "Message Time", "Group/Chat", "Reported By", "CSP Name", "CSP ID", "State",
    "District", "Village/Branch", "Issue Type", "Issue Description", "Actual Location", "Planned Location",
    "Distance Mismatch (metres)", "Visit Status", "Evidence Filename", "Message Reference", "Duplicate Flag",
    "Confidence", "Resolution Status", "Assigned To", "Resolution Notes",
]
EXTRA_COLUMNS = [
    "Suggested Action", "Next Step", "Days Pending", "Added On",
    "Reporter Role", "Timezone", "All Issue Categories", "Duplicate Of", "Duplicate Reason", "Needs Verification",
    "Verification Priority", "Verification Reasons", "Field Sources", "Other Coordinates (unlabelled)",
    "Distance Text Seen", "Images Count", "OCR Status", "Screenshot OCR Text", "Resolution Evidence",
    "Auto Resolution Status", "Evidence Paths", "Data Source", "App Visit After Complaint", "App Visit Score",
]
ALL_COLUMNS = TRACKER_COLUMNS + EXTRA_COLUMNS
RESOLUTION_STATUSES = ["Open", "In Progress", "Escalated", "Resolved", "Closed - Not an Issue"]


@dataclass
class Bundle:
    chat: str
    sender: str
    messages: list[Message] = field(default_factory=list)

    @property
    def first(self) -> Message:
        return self.messages[0]

    @property
    def last_ts(self) -> datetime:
        return self.messages[-1].timestamp

    @property
    def text(self) -> str:
        return "\n".join(m.text for m in self.messages if m.text.strip())

    @property
    def attachments(self) -> list[tuple[str, Path | None]]:
        return [(a, m.attachment_paths.get(a)) for m in self.messages for a in m.attachments]


def bundle_messages(messages: list[Message], minutes: int) -> list[Bundle]:
    """Merge a sender's caption and the photos sent with it into one report (WhatsApp splits them)."""
    window = timedelta(minutes=minutes)
    open_bundles: dict[tuple[str, str], Bundle] = {}
    bundles: list[Bundle] = []
    for m in messages:
        if m.is_system:
            continue
        key = (m.chat_name, m.sender)
        b = open_bundles.get(key)
        has_text = bool(m.text.strip())
        self_quote = bool(m.quoted) and m.quoted.split(":", 1)[0].strip() == (m.sender or "")
        if b is not None and self_quote and m.timestamp - b.last_ts <= window:
            b.messages.append(m)  # the sender replying to their own complaint ("Update location") adds to it
            continue
        if b is not None and m.source_kind != "image_only" and not m.quoted and m.timestamp - b.last_ts <= window:
            b_has_text = bool(b.text.strip())
            short_followup = has_text and len(m.text) <= 80 and m.timestamp - b.last_ts <= timedelta(minutes=2)
            # Two quick messages about different CSPs are two complaints, not one.
            other_csp = csp_codes(m.text) and csp_codes(b.text) and not (csp_codes(m.text) & csp_codes(b.text))
            if (not b_has_text or not has_text or short_followup) and not other_csp:
                b.messages.append(m)
                continue
        b = Bundle(m.chat_name, m.sender or "", [m])
        open_bundles[key] = b
        bundles.append(b)
    return bundles


LIST_ITEM = re.compile(r"(?m)^\s*(\d{1,2})\s*[.)]\s*-?\s*(?=\S)")


def _segments(text: str) -> list[tuple[int | None, str]]:
    """Split a numbered list that covers several CSPs into one text per item; otherwise keep it whole."""
    starts = [m.start() for m in LIST_ITEM.finditer(text)]
    if len(starts) < 2:
        return _code_lines(text) or [(None, text)]
    items = [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)], strict=True)]
    coded = [it for it in items if csp_codes(it) or re.search(r"\bcode\b", it, re.I)]
    if len(coded) < 2:
        return [(None, text)]
    return [(n, it) for n, it in enumerate(items, start=1)]


def _code_lines(text: str) -> list[tuple[int | None, str]] | None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    code_lines = [ln for ln in lines if csp_codes(ln) and len(ln) <= 60]
    shared = [ln for ln in lines if not csp_codes(ln)]
    if len(code_lines) < 2 or not shared:
        return None
    tail = "\n".join(shared)
    return [(n, f"{ln}\n{tail}") for n, ln in enumerate(code_lines, start=1)]


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "chat"


def _norm_text(text: str) -> str:
    """Comparable form of a message: lower case, punctuation removed, WhatsApp's "[Forwarded]" tag dropped."""
    return re.sub(r"\W+", " ", re.sub(r"\[forwarded\]", " ", text.lower())).strip()


def _load_reporters(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {row["sender"].strip(): row for row in csv.DictReader(f) if row.get("sender")}


def _confidence(ex, message_relevant: bool, ocr_relevant: bool) -> str:
    score = 0
    score += 2 if ex.issue_type not in (OTHER, "App/Server Error") else 0
    score += 1 if message_relevant else 0
    score += 1 if ocr_relevant else 0
    score += 1 if ex.csp_id != NOT_AVAILABLE else 0
    score += 1 if ex.distance_m is not None or ex.actual_location != NOT_AVAILABLE else 0
    return "High" if score >= 4 else "Medium" if score >= 2 else "Low"


def harvest_manual_edits(report_path: Path, store: StateStore) -> int:
    """Keep edits the team made in the previous workbook's tracker (status, owner, notes)."""
    if not report_path.exists():
        return 0
    try:
        df = pd.read_excel(report_path, sheet_name="Detailed Issue Tracker", header=2, dtype=str).fillna("")
    except Exception as exc:
        log.warning("Could not read previous report for manual edits: %s", exc)
        return 0
    n = 0
    for _, row in df.iterrows():
        rid = row.get("Record ID", "")
        if not rid:
            continue
        status, auto = row.get("Resolution Status", ""), row.get("Auto Resolution Status", "")
        assigned, notes = row.get("Assigned To", ""), row.get("Resolution Notes", "")
        if (status and status != auto) or assigned or notes:
            store.save_manual_update(rid, status or None, assigned or None, notes or None)
            n += 1
    return n


def run(settings: Settings) -> dict:
    started = datetime.now()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = settings.state_db.parent / ("work_synthetic" if settings.synthetic else "work")
    store = StateStore(settings.state_db, prefix="SYN" if settings.synthetic else "GPS")
    harvested = harvest_manual_edits(settings.report_path, store)
    ocr = OcrEngine(settings.ocr_enabled, settings.tesseract_cmd, settings.ocr_languages, store)
    reporters = _load_reporters(settings.reporter_directory)
    visits, visit_failures = load_visit_reports(settings.visit_reports_dir, settings.project_dir,
                                                settings.project_dir / "config" / "dc_users.csv")
    app_visits: dict[str, list] = {}
    for _, v in done_visits(visits).iterrows():  # a score-0 visit was not completed, so it never closes a complaint
        app_visits.setdefault(v["CSP Code"], []).append((v["Visit Date"], v["Visit Score"]))

    parsed = discover_and_parse(settings.input_dir, settings.project_dir, work_dir)
    user_msgs = [m for m in parsed.messages if not m.is_system]
    latest = max((m.timestamp.date() for m in user_msgs), default=None)
    start = settings.start_date or date.min
    end = settings.end_date or latest or date.max
    in_range = [m for m in parsed.messages if start <= m.timestamp.date() <= end]
    log.info("Messages: %d parsed, %d in range %s..%s", len(parsed.messages), len(in_range), start, end)

    bundles = bundle_messages(in_range, settings.bundle_minutes)
    records: list[dict] = []
    evidence_rows: list[dict] = []
    images_seen = images_missing = 0

    for b in bundles:
        evs: list[Evidence] = []
        for name, src in b.attachments:
            ev = process_attachment(name, src, _slug(b.chat), settings.evidence_dir, ocr)
            evs.append(ev)
            images_seen += ev.is_image and ev.rel_path is not None
            images_missing += ev.ocr_status == "File missing"
        ocr_text = "\n".join(e.ocr_text for e in evs if e.ocr_text)
        media_omitted = any(m.media_omitted for m in b.messages)
        linked_evidence = False
        # A message listing several CSPs ("1. Name - Code - Distance ...") becomes one record per CSP.
        for seg_no, message_text in _segments(b.text):
            ex = extract(message_text, ocr_text)
            source_kind = b.first.source_kind
            data_source = ("SYNTHETIC TEST DATA" if settings.synthetic or "[SYNTHETIC]" in b.chat
                           else "WhatsApp Web capture" if source_kind == "web_capture"
                           else "Image only (no chat)" if source_kind == "image_only" else "WhatsApp export")

            if not ex.relevant:
                for e in (evs if seg_no in (None, 1) else []):
                    if e.is_image:
                        evidence_rows.append(_evidence_row(e, None, b, "Not linked - no GPS/location text found"
                                                           if e.ocr_status == "OK" else
                                                           "Not linked - image not readable by OCR, review manually"))
                continue
            # "You" is the person who exported the chat (office side). Their own messages without a CSP code
            # are questions or instructions ("Location me aapne kya update kiya hai"), not DC complaints.
            if b.sender == "You" and not csp_codes(message_text):
                continue

            rec_key = b.first.msg_id + (f"#{seg_no}" if seg_no else "")
            rid = store.record_id(rec_key)
            message_relevant = is_relevant(message_text)
            ocr_relevant = bool(ocr_text) and is_relevant(ocr_text)
            confidence = _confidence(ex, message_relevant, ocr_relevant)
            sender = mask_phone(b.sender)
            reporter_info = reporters.get(b.sender, {})
            district = ex.district
            field_sources = dict(ex.sources)
            if district == NOT_AVAILABLE and reporter_info.get("district"):
                district = reporter_info["district"]
                field_sources["District"] = "Reporter directory (inferred)"

            reasons: list[str] = []
            if ex.csp_id == NOT_AVAILABLE:
                reasons.append("CSP ID not stated")
            elif not csp_codes(ex.csp_id):
                reasons.append(f"CSP ID looks incomplete ({ex.csp_id}) - check")
            if district == NOT_AVAILABLE:
                reasons.append("District not stated")
            if any(e.is_image and e.ocr_status in ("OCR unavailable", "OCR failed") for e in evs):
                reasons.append("Screenshot not read by OCR - check image")
            if any(e.ocr_status == "File missing" for e in evs):
                reasons.append("Media missing from export")
            elif media_omitted:
                reasons.append("Photo not included in export - see WhatsApp")
            if ex.issue_type in ("Visit Not Completed/Updated", OTHER) and not LOCATION_TERMS.search(message_text):
                reasons.append("Reason for failed visit not stated")
            if source_kind == "image_only":
                reasons.append("Image only - no chat message, sender or group context")
            elif not message_relevant and ocr_relevant:
                reasons.append("Issue identified from screenshot text only")
            if ex.other_coordinates:
                reasons.append("Coordinates seen but not labelled actual/planned")
            if confidence == "Low":
                reasons.append("Low confidence classification")

            image_names = [e.filename for e in evs]
            records.append({
                "Record ID": rid,
                "_ts": b.first.timestamp,
                "_record_key": rec_key,
                "_msg_ids": {m.msg_id for m in b.messages},
                "_text_norm": _norm_text(message_text),
                "_forwarded": "[forwarded]" in message_text.lower(),
                "_hashes": [(e.sha256, e.phash) for e in evs if e.is_image and e.sha256],
                "_sender_raw": b.sender,
                "_chat": b.chat,
                "Message Date": b.first.timestamp.date(),
                "Message Time": b.first.timestamp.time(),
                "Group/Chat": b.chat,
                "Reported By": sender,
                "CSP Name": ex.csp_name,
                "CSP ID": ex.csp_id,
                "State": ex.state,
                "District": district,
                "Village/Branch": ex.village_branch,
                "Issue Type": ex.issue_type,
                "Issue Description": mask_phone(message_text) or "(no caption - see screenshot)",
                "Actual Location": ex.actual_location,
                "Planned Location": ex.planned_location,
                "Distance Mismatch (metres)": ex.distance_m,
                "Visit Status": ex.visit_status,
                "Evidence Filename": "; ".join(image_names) if image_names else NOT_AVAILABLE,
                "Message Reference": " | ".join(m.reference for m in b.messages[:3]) + (" ..." if len(b.messages) > 3 else ""),
                "Duplicate Flag": "Unique",
                "Confidence": confidence,
                "Resolution Status": "Open",
                "Assigned To": "",
                "Resolution Notes": "",
                "Reporter Role": infer_role(b.sender, reporters),
                "Timezone": f"{settings.timezone} (phone local time, assumed)",
                "All Issue Categories": "; ".join(ex.categories) or OTHER,
                "Duplicate Of": "",
                "Duplicate Reason": "",
                "Needs Verification": "",
                "Verification Priority": "",
                "Verification Reasons": reasons,
                "Field Sources": "; ".join(f"{k}: {v}" for k, v in field_sources.items()) or "Classification inferred from text",
                "Other Coordinates (unlabelled)": "; ".join(ex.other_coordinates),
                "Distance Text Seen": ex.distance_text,
                "Images Count": sum(e.is_image for e in evs),
                "OCR Status": "; ".join(sorted({e.ocr_status for e in evs})) if evs else "No image",
                "Screenshot OCR Text": mask_phone(ocr_text)[:1500],
                "Resolution Evidence": "",
                "Auto Resolution Status": "Open",
                "Evidence Paths": "; ".join(e.rel_path for e in evs if e.rel_path),
                "Data Source": data_source,
            })
            if not linked_evidence:
                for e in evs:
                    evidence_rows.append(_evidence_row(e, rid, b, "Linked to issue"))
                linked_evidence = True

    _flag_duplicates(records, settings)
    _infer_resolution(records, in_range)
    today = date.today()
    for r in records:  # was the visit later recorded in the SBI CSP Visit App?
        later = sorted(x for x in app_visits.get(str(r["CSP ID"]).upper(), []) if x[0] >= r["Message Date"])
        r["App Visit After Complaint"] = later[0][0] if later else None
        r["App Visit Score"] = later[0][1] if later else None
        # Agent action: the SBI CSP Visit App shows the visit went through after the complaint, so close it.
        # A status the team set by hand is applied after this and always wins.
        if later and r["Resolution Status"] in ("Open", "In Progress"):
            r["Resolution Status"] = r["Auto Resolution Status"] = "Resolved"
            score = "" if pd.isna(later[0][1]) else f", score {later[0][1]:.0f}"
            r["Resolution Evidence"] = (f"Auto-closed by agent: SBI CSP Visit App shows a visit for this CSP on "
                                        f"{later[0][0]:%d %b %Y}{score}")
    manual = store.manual_updates()
    for r in records:
        if (m := manual.get(r["Record ID"])):
            for k, v in m.items():
                if v:
                    r[k] = v
        reasons = r["Verification Reasons"]
        if r["Duplicate Flag"] == "Possible Duplicate":
            reasons.insert(0, "Possible duplicate - " + r["Duplicate Reason"])
        high = any(x.startswith(("Possible duplicate", "Media missing", "Low confidence", "Issue identified from screenshot",
                                 "Image only"))
                   for x in reasons)
        medium = any(x.startswith(("CSP ID", "Screenshot not read", "Coordinates seen"))
                     for x in reasons)
        r["Verification Priority"] = "High" if high else "Medium" if medium else "Low" if reasons else "Not needed"
        r["Needs Verification"] = "Yes" if (high or medium) else "No"
        r["Verification Reasons"] = "; ".join(reasons) or "No checks failed"
        if r["Actual Location"] == NOT_AVAILABLE and r["Other Coordinates (unlabelled)"]:
            r["Actual Location"] = NEEDS_VERIFICATION
        _plan_next_step(r, settings, store, today)

    records.sort(key=lambda r: (r["_ts"], r["Record ID"]))
    df = pd.DataFrame(records)
    if df.empty:
        df = pd.DataFrame(columns=ALL_COLUMNS)
    issues = df[ALL_COLUMNS].copy()
    issues["Days Pending"] = pd.to_numeric(issues["Days Pending"], errors="coerce").astype("Int64")
    ev_df = pd.DataFrame(evidence_rows, columns=["Evidence Filename", "Record ID", "Group/Chat", "Message Date",
                                                 "Message Time", "Reported By", "Relative Path", "File Present",
                                                 "SHA-256 (short)", "Perceptual Hash", "OCR Status",
                                                 "OCR Text Excerpt", "Message Reference", "Link Status"])

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "SYNTHETIC TEST DATA" if settings.synthetic else "Real WhatsApp data",
        "period_start": str(start) if settings.start_date else None,
        "period_end": str(end) if end != date.max else None,
        "end_date_rule": "configured" if settings.end_date else "latest message date in inputs",
        "timezone": settings.timezone,
        "files_parsed": parsed.files_ok,
        "files_failed": [{"file": f, "error": e} for f, e in parsed.files_failed],
        "unparsed_lines": parsed.unparsed_lines,
        "messages_parsed": len(parsed.messages),
        "user_messages_parsed": len(user_msgs),
        "messages_in_range": len(in_range),
        "message_bundles_reviewed": len(bundles),
        "images_processed": images_seen,
        "attachments_missing": images_missing,
        "issues_identified": len(issues),
        "needs_verification": int((issues["Needs Verification"] == "Yes").sum()) if len(issues) else 0,
        "possible_duplicates": int((issues["Duplicate Flag"] == "Possible Duplicate").sum()) if len(issues) else 0,
        "unlinked_images": int((ev_df["Record ID"].isna()).sum()),
        "ocr_status": ocr.reason,
        "manual_edits_preserved": harvested,
        "runtime_seconds": round((datetime.now() - started).total_seconds(), 1),
        "visit_reports": sorted(set(visits["Source File"])) if len(visits) else [],
        "visit_rows": int(len(visits)),
        "visits_unique": int((visits["Duplicate Row"] == "No").sum()) if len(visits) else 0,
        "visits_done": int(len(done_csps(visits))) if len(visits) else 0,            # each CSP once
        "visits_score_zero": int(len(score_zero_csps(visits))) if len(visits) else 0,
        "visit_period": [str(min(visits["Visit Date"])), str(max(visits["Visit Date"]))] if len(visits) else None,
        "visit_report_failures": visit_failures,
    }

    prev_csv = settings.output_dir / "issues.csv"
    prev = pd.read_csv(prev_csv, dtype=str, keep_default_na=False) if prev_csv.exists() else None
    brief = build_brief(issues, prev, settings.agent_contacts)
    summary["agent"] = brief["counts"]
    _write_outputs(issues, ev_df, summary, settings, visits)
    write_brief(brief, settings.output_dir)
    store.log_run({k: v for k, v in summary.items() if k not in ("files_parsed",)})
    store.close()
    return summary


def _evidence_row(e: Evidence, rid: str | None, b: Bundle, status: str) -> dict:
    return {
        "Evidence Filename": e.filename, "Record ID": rid, "Group/Chat": b.chat,
        "Message Date": b.first.timestamp.date(), "Message Time": b.first.timestamp.time(),
        "Reported By": mask_phone(b.sender), "Relative Path": e.rel_path or "",
        "File Present": "Yes" if e.rel_path else "No", "SHA-256 (short)": (e.sha256 or "")[:16],
        "Perceptual Hash": e.phash or "", "OCR Status": e.ocr_status,
        "OCR Text Excerpt": mask_phone(e.ocr_text)[:300], "Message Reference": b.first.reference,
        "Link Status": status,
    }


def _flag_duplicates(records: list[dict], s: Settings) -> None:
    records.sort(key=lambda r: r["_ts"])
    csp_window = timedelta(hours=s.same_csp_window_hours)
    text_window = timedelta(hours=s.same_text_window_hours)
    for i, r in enumerate(records):
        for prev in records[:i]:
            reason = None
            gap = r["_ts"] - prev["_ts"]
            # Screenshots of the same app screen look alike even for different CSPs, so a near-identical
            # perceptual hash only counts when the CSP matches (or neither names one) within 72h.
            csp_compatible = r["CSP ID"] == prev["CSP ID"] and gap <= timedelta(hours=72)
            for sha, ph in r["_hashes"]:
                for psha, pph in prev["_hashes"]:
                    if sha == psha:
                        reason = "same image file"
                    elif csp_compatible:
                        d = phash_distance(ph, pph)
                        if d is not None and d <= s.image_hash_max_distance:
                            reason = f"near-identical screenshot (hash distance {d})"
                    if reason:
                        break
                if reason:
                    break
            same_csp = r["CSP ID"] != NOT_AVAILABLE and r["CSP ID"] == prev["CSP ID"]
            # A forwarded copy of an earlier complaint (e.g. someone re-sending a DC's messages days later)
            # repeats it whenever it happens.
            if not reason and r["_forwarded"] and (same_csp or (len(r["_text_norm"]) >= 15
                                                                and r["_text_norm"] == prev["_text_norm"])):
                reason = "forwarded copy of the same complaint"
            if not reason and same_csp and r["Issue Type"] == prev["Issue Type"] and gap <= csp_window:
                reason = f"same CSP ID and issue type within {s.same_csp_window_hours}h"
            if not reason and len(r["_text_norm"]) >= 15 and r["_text_norm"] == prev["_text_norm"] and gap <= text_window:
                reason = "same message text" + (" in another group" if r["_chat"] != prev["_chat"] else "")
            if reason:
                r["Duplicate Flag"] = "Possible Duplicate"
                r["Duplicate Of"] = prev["Duplicate Of"] or prev["Record ID"]
                r["Duplicate Reason"] = f"{reason} as {prev['Record ID']}"
                if prev["Duplicate Flag"] == "Unique":
                    prev["Duplicate Flag"] = "Original (has possible duplicates)"
                break


def _plan_next_step(r: dict, s: Settings, store: StateStore, today: date) -> None:
    """What the team should do now: the action for this kind of problem, and how urgent it is."""
    actions = s.suggested_actions
    r["Suggested Action"] = actions.get(r["Issue Type"]) or actions.get(OTHER) or "Check the message in WhatsApp."
    first = store.first_seen(r["Record ID"])
    r["Added On"] = date.fromisoformat(first[:10]) if first else today
    status = r["Resolution Status"]
    if status in ("Resolved", "Closed - Not an Issue"):
        r["Days Pending"] = None
        if str(r.get("Resolution Evidence", "")).startswith("Auto-closed by agent"):
            r["Next Step"] = f"Done - auto-closed (app visit {r['App Visit After Complaint']:%d %b})"
        else:
            r["Next Step"] = "Done - confirm with DC" if r["Auto Resolution Status"] == "Resolved" else "Done"
        return
    days = (today - r["Message Date"]).days
    r["Days Pending"] = days
    if r["Duplicate Flag"] == "Possible Duplicate":
        r["Next Step"] = f"Check if same as {r['Duplicate Of']}"
    elif r.get("App Visit After Complaint"):
        r["Next Step"] = f"App visit recorded {r['App Visit After Complaint']:%d %b} - confirm and close"
    elif status == "Escalated":
        r["Next Step"] = f"Escalated - track with SBI/IT ({days} days)"
    elif days >= s.escalate_after_days:
        r["Next Step"] = f"Escalate - pending {days} days"
    elif days >= s.follow_up_after_days:
        r["Next Step"] = f"Follow up - pending {days} days"
    elif status == "In Progress":
        r["Next Step"] = "In progress - check today"
    else:
        r["Next Step"] = "New - take suggested action"


def _quotes_record(r: dict, quote: str) -> bool:
    """Does a reply's quoted text refer to this complaint?"""
    q = _norm_text(quote)
    head = r["_text_norm"][:30]
    if len(head) >= 8 and head in q:
        return True
    return (r["CSP ID"] != NOT_AVAILABLE and r["CSP ID"].lower() in quote.lower()
            and bool(LOCATION_TERMS.search(quote)))


def _set_status(r: dict, status: str, m: Message, how: str) -> None:
    r["Auto Resolution Status"] = status
    if r["Resolution Status"] in ("Open", "In Progress") or r["Resolution Status"] == r.get("_auto_prev"):
        r["Resolution Status"] = status
    r["_auto_prev"] = status
    r["Resolution Evidence"] = (f"{how} {m.reference} by {mask_phone(m.sender or '')} on {m.timestamp:%d %b %H:%M}: "
                                f"\"{mask_phone(m.text)[:120]}\"")


def _infer_resolution(records: list[dict], messages: list[Message]) -> None:
    """Status from follow-up messages. Strongest evidence is a reply that quotes the complaint
    (from the .md export): "Issue resolved" -> Resolved, "Ok"/"checking" -> In Progress, an escalation
    request -> Escalated. Without a quote: Resolved only when a later message names the same CSP ID,
    or the same reporter confirms a location issue is fixed within 48h. Everything else stays Open."""
    for r in records:
        for m in messages:
            if m.chat_name != r["_chat"] or m.timestamp <= r["_ts"] or m.is_system or m.msg_id in r["_msg_ids"]:
                continue
            if m.timestamp - r["_ts"] > timedelta(days=14):
                break
            if m.quoted:
                if not _quotes_record(r, m.quoted):
                    continue
                if is_resolution_reply(m.text):
                    _set_status(r, "Resolved", m, "Reply to this complaint in")
                    break
                if is_escalation(m.text):
                    _set_status(r, "Escalated", m, "Escalation reply in")
                elif is_acknowledgement(m.text) and r["Auto Resolution Status"] == "Open":
                    _set_status(r, "In Progress", m, "Acknowledged in")
                continue
            same_csp = r["CSP ID"] != NOT_AVAILABLE and r["CSP ID"].lower() in m.text.lower()
            if (same_csp and VISIT_DONE.search(m.text) and not VISIT_FAILED.search(m.text)
                    and not re.search(r"of+line", m.text, re.I)
                    and r["Auto Resolution Status"] in ("Open", "In Progress")):
                _set_status(r, "Resolved", m, "CSP visit later reported done (verify) in")
                break
            if same_csp and is_escalation(m.text) and r["Auto Resolution Status"] == "Open":
                r["Auto Resolution Status"] = "Escalated"
                if r["Resolution Status"] == "Open":
                    r["Resolution Status"] = "Escalated"
                r["Resolution Evidence"] = (f"Escalation requested in {m.reference} by {mask_phone(m.sender or '')}: "
                                            f"\"{mask_phone(m.text)[:120]}\" - verify")
                continue
            if not is_resolution_reply(m.text):
                continue
            same_reporter = (m.sender == r["_sender_raw"] and m.timestamp - r["_ts"] <= timedelta(hours=48)
                             and bool(LOCATION_TERMS.search(m.text)))
            if same_csp or same_reporter:
                r["Auto Resolution Status"] = "Resolved"
                if r["Resolution Status"] == "Open":
                    r["Resolution Status"] = "Resolved"
                r["Resolution Evidence"] = (f"Inferred from {m.reference} by {mask_phone(m.sender or '')}: "
                                            f"\"{mask_phone(m.text)[:120]}\" - verify")
                break


def _write_outputs(issues: pd.DataFrame, ev_df: pd.DataFrame, summary: dict, s: Settings,
                   visits: pd.DataFrame | None = None) -> None:
    from .excel_report import write_report  # imported late so the pipeline can be unit-tested alone

    out = s.output_dir
    csv_issues = issues.copy()
    csv_issues["Message Date"] = csv_issues["Message Date"].astype(str)
    csv_issues["Message Time"] = csv_issues["Message Time"].astype(str)
    csv_issues.to_csv(out / "issues.csv", index=False, encoding="utf-8-sig")
    ev_df.to_csv(out / "evidence_index.csv", index=False, encoding="utf-8-sig")
    visits = visits if visits is not None else pd.DataFrame()
    visits.to_csv(out / "visits.csv", index=False, encoding="utf-8-sig")
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    write_report(issues, ev_df, summary, s.report_path, RESOLUTION_STATUSES, visits)
    log.info("Wrote %s", s.report_path)
