"""Agent brief: what changed since the last run, what the agent did, and ready-to-send WhatsApp drafts.

The agent never sends anything itself. Drafts are written to output/agent_brief.md / .json and shown in the
dashboard's Agent tab so a person can copy, check and send them.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import NOT_AVAILABLE
from .labels import problem_name

SUPPORT_TYPES = {"GPS Not Detected", "Location Error in Visit App", "Location Refresh/Update Failure",
                 "App/Server Error", "Visit Not Completed/Updated", "Distance Exceeds Radius",
                 "Lat/Long Incorrect or Unavailable", "Other Location Issue"}
COORDINATOR_TYPES = {"CSP Not in Visit App", "CSP Location Needs Update", "Location Mismatch (Actual vs Planned)"}
OPEN = ("Open", "In Progress", "Escalated")
DEFAULT_CONTACTS = {"tech_support": "Amit singh", "sbi_coordinator": "Ganesh", "manager": "Ma'am"}


def _numbered(rows: pd.DataFrame, limit: int = 15, with_dc: bool = True) -> str:
    """One line per CSP (several complaints about the same CSP are merged), oldest first."""
    merged = []
    for csp, g in rows.groupby("CSP ID", sort=False):
        problems = ", ".join(dict.fromkeys(problem_name(t) for t in g["Issue Type"]))
        days = pd.to_numeric(g["Days Pending"], errors="coerce").max()
        days_txt = "" if pd.isna(days) else f" – {int(days)} din se pending"
        dcs = ", ".join(dict.fromkeys(g["Reported By"]))
        merged.append(f"{csp} – {problems}{days_txt}" + (f" ({dcs})" if with_dc else ""))
    lines = [f"{i}. {m}" for i, m in enumerate(merged[:limit], start=1)]
    if len(merged) > limit:
        lines.append(f"(+{len(merged) - limit} aur – poori list dashboard pe)")
    return "\n".join(lines)


def build_brief(issues: pd.DataFrame, prev: pd.DataFrame | None, contacts: dict | None = None,
                today: date | None = None) -> dict:
    contacts = {**DEFAULT_CONTACTS, **(contacts or {})}
    today = today or date.today()
    cur = issues[issues["Duplicate Flag"] != "Possible Duplicate"].copy()
    open_ = cur[cur["Resolution Status"].isin(OPEN)].sort_values("Message Date")
    escalate = open_[open_["Next Step"].astype(str).str.startswith(("Escalate", "Escalated"))]

    prev_status = {}
    if prev is not None and len(prev):
        prev_status = dict(zip(prev["Record ID"], prev["Resolution Status"], strict=False))
        prev_steps = dict(zip(prev["Record ID"], prev["Next Step"].astype(str), strict=False)) if "Next Step" in prev else {}
    else:
        prev_steps = {}
    first_run = not prev_status
    new = cur[~cur["Record ID"].isin(prev_status)] if not first_run else cur.iloc[0:0]
    closed_now = cur[(cur["Resolution Status"] == "Resolved")
                     & cur["Record ID"].map(lambda i: prev_status.get(i) in OPEN)]
    auto_closed = cur[cur["Resolution Evidence"].astype(str).str.startswith("Auto-closed by agent")]
    newly_escalate = escalate[escalate["Record ID"].map(lambda i: not str(prev_steps.get(i, "")).startswith("Escalate"))]

    drafts: list[dict] = []
    support = escalate[escalate["Issue Type"].isin(SUPPORT_TYPES) & (escalate["CSP ID"] != NOT_AVAILABLE)]
    if len(support):
        drafts.append({
            "to": f"@{contacts['tech_support']} (tech support)",
            "why": f"{support['CSP ID'].nunique()} CSPs have had no app visit for 5+ days",
            "message": (f"@{contacts['tech_support']} ji, in CSPs pe SBI CSP Visit app se visit nahi ho pa raha "
                        f"(GPS / location / app error). Please check karke update dijiye:\n" + _numbered(support)),
        })
    coord = open_[open_["Issue Type"].isin(COORDINATOR_TYPES) & (open_["CSP ID"] != NOT_AVAILABLE)]
    if len(coord):
        drafts.append({
            "to": f"@{contacts['sbi_coordinator']} (SBI coordinator)",
            "why": f"{coord['CSP ID'].nunique()} CSPs need mapping or lat-long updated on the portal",
            "message": (f"@{contacts['sbi_coordinator']} ji, in CSPs ki Visit App mapping / exact lat-long portal pe "
                        f"update karni hai, tabhi app se visit ho payega:\n" + _numbered(coord)),
        })
    no_id = open_[(open_["CSP ID"] == NOT_AVAILABLE) & (open_["Reported By"] != "You")]
    for dc, g in no_id.groupby("Reported By"):
        items = "\n".join(f"• {r['Message Date']:%d %b}: \"{str(r['Issue Description'])[:70]}\"" for _, r in g.iterrows())
        drafts.append({
            "to": f"@{dc}",
            "why": f"{len(g)} complaint(s) missing a CSP ID",
            "message": f"@{dc} ji, in complaints ka CSP ID bhej dijiye taaki report mein sahi CSP ke saath jud sake:\n{items}",
        })
    total, pend = len(cur), len(open_)
    summary_msg = (
        f"{contacts['manager']}, CSP GPS complaints – {today:%d %b} update:\n"
        f"• Kul complaints: {total} · Pending: {pend} · Escalate: {len(escalate)} · "
        f"Solved: {int((cur['Resolution Status'] == 'Resolved').sum())}\n"
        f"• Pichhli update ke baad: {len(new)} nayi complaint, {len(closed_now)} band hui\n"
        f"• Agent ne {len(auto_closed)} complaint auto-close ki (baad mein app se visit ho gaya)\n"
        + (f"• Sabse purani pending: {open_.iloc[0]['CSP ID']} ({open_.iloc[0]['Message Date']:%d %b}, "
           f"{problem_name(open_.iloc[0]['Issue Type'])})" if len(open_) else "• Koi complaint pending nahi")
    )
    drafts.insert(0, {"to": contacts["manager"], "why": "Daily quick update", "message": summary_msg})

    def rows(d: pd.DataFrame) -> list[dict]:
        return [{"id": r["Record ID"], "date": f"{r['Message Date']}", "csp": r["CSP ID"],
                 "problem": problem_name(r["Issue Type"]), "by": r["Reported By"],
                 "evidence": str(r.get("Resolution Evidence", ""))} for _, r in d.iterrows()]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "first_run": first_run,
        "counts": {"total": total, "pending": pend, "escalate": len(escalate), "new": len(new),
                   "closed_since_last_run": len(closed_now), "auto_closed": len(auto_closed),
                   "newly_escalate": len(newly_escalate), "drafts": len(drafts)},
        "new": rows(new), "closed": rows(closed_now), "auto_closed": rows(auto_closed),
        "newly_escalate": rows(newly_escalate), "drafts": drafts,
    }


def write_brief(brief: dict, out_dir: Path) -> None:
    (out_dir / "agent_brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
    c = brief["counts"]
    md = [f"# Agent brief – {brief['generated_at'].replace('T', ' ')}", "",
          f"Total {c['total']} · pending {c['pending']} · escalate {c['escalate']} · "
          f"new {c['new']} · closed since last run {c['closed_since_last_run']} · auto-closed {c['auto_closed']}", ""]
    for title, key in (("New complaints", "new"), ("Closed since last run", "closed"),
                       ("Newly need escalation", "newly_escalate")):
        md.append(f"## {title} ({len(brief[key])})")
        md += [f"- {r['id']} · {r['date']} · {r['csp']} · {r['problem']} · {r['by']}" for r in brief[key]] or ["- none"]
        md.append("")
    md.append("## Ready-to-send WhatsApp drafts (check before sending – the agent never sends)")
    for d in brief["drafts"]:
        md += ["", f"### To {d['to']} – {d['why']}", "```", d["message"], "```"]
    (out_dir / "agent_brief.md").write_text("\n".join(md) + "\n", encoding="utf-8")
