"""CSP GPS Complaint Dashboard.

    streamlit run dashboard/app.py                       # real data in output/
    streamlit run dashboard/app.py -- --data output_synthetic

Tabs: Overview (what to do first) · Complaints (sorted by priority) · App Visits (SBI CSP Visit App
report linked to complaints) · Photos.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import io
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from csp_gps.labels import PROBLEM_NAMES, STATUS_NAMES  # noqa: E402  (shared with the Excel Daily Update sheet)
from csp_gps.locations import load_locations as read_locations  # noqa: E402
from csp_gps.locations import merge_locations  # noqa: E402
from csp_gps.uploads import UploadResult, log_upload, run_report, save_upload, uploads_enabled  # noqa: E402
from csp_gps.visits import LOW_SCORE  # noqa: E402

try:  # DASHBOARD_PASSWORD etc. live in .env on the server
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env", override=False)
except ImportError:
    pass

NA = "Not Available"
NOT_MENTIONED = "Not mentioned"

# Eko Kiosk look (kiosk.eko.in): teal header, Eko blue, saffron highlight, forest green, cream background.
HEADER_BG = "#00668F"
FOREST = "#1D5E43"
RED = "#C0392B"
INK = "#0C1217"
MUTED = "#5E6B78"
BORDER = "#E0D9D1"
SAFFRON_GRADIENT = "linear-gradient(135deg, #EFB839, #E69B1A)"
STATUS_COLORS = {"Pending": "#E69B1A", "Escalated": RED, "Solved": FOREST, "Not a problem": "#9AA3AF"}

# Priority shown on every complaint, in the order the team should work through them.
PRIORITIES = [
    ("🔴 Escalate", "Pending 5+ days or escalated – raise it today", RED),
    ("🟣 Confirm & close", "Visit later recorded in the app – confirm with the DC and close", "#6C4BB4"),
    ("🟠 Follow up", "Pending 2–4 days – remind the DC / support", "#E69B1A"),
    ("🔵 New", "Just reported – take the suggested action", "#0092CC"),
    ("🟢 Solved", "Solved or not a problem", FOREST),
]
PRIORITY_RANK = {name: i for i, (name, _, _) in enumerate(PRIORITIES)}


def load_brand() -> dict:
    brand = {"company": "Eko India Financial Services", "product": "CSP GPS Complaints",
             "primary": "#0092CC", "accent": "#E69B1A", "logo": "dashboard/assets/logo.png"}
    try:
        cfg_path = next((p for p in (PROJECT / "config" / "settings.yaml", PROJECT / "config.yaml") if p.exists()), None)
        cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path else {}) or {}
        brand.update({k: v for k, v in (cfg.get("brand") or {}).items() if v})
    except Exception:
        pass
    return brand


BRAND = load_brand()
PRIMARY, ACCENT = BRAND["primary"], BRAND["accent"]

st.set_page_config(page_title="CSP GPS Complaints | Eko", page_icon="📍", layout="wide")
st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap');
  html, body, .stApp, .stApp p, .stApp span, .stApp div, .stApp label, .stApp input, .stApp textarea,
  .stApp button, .stApp li, .stApp td, .stApp th, .stApp h1, .stApp h2, .stApp h3, .stApp summary {{
      font-family: "Plus Jakarta Sans", Inter, "Segoe UI", system-ui, sans-serif !important;}}
  .stApp [data-testid="stIconMaterial"], .stApp .material-symbols-rounded, .stApp [class*="material-icons"] {{
      font-family: "Material Symbols Rounded" !important;}}
  .stApp p, .stApp li {{font-size:0.95rem;}}
  #MainMenu, footer, header [data-testid="stToolbar"] {{visibility: hidden;}}
  .stApp {{background:
      radial-gradient(1200px 500px at 100% -10%, rgba(0,146,204,0.10), transparent 60%),
      radial-gradient(900px 400px at -10% 10%, rgba(239,184,57,0.12), transparent 60%), #F6F2EC;}}
  .block-container {{padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1420px;}}
  @keyframes rise {{from {{opacity:0; transform:translateY(8px);}} to {{opacity:1; transform:none;}}}}
  @keyframes pulse {{0% {{box-shadow:0 0 0 0 rgba(74,222,128,0.7);}} 70% {{box-shadow:0 0 0 8px rgba(74,222,128,0);}}
                     100% {{box-shadow:0 0 0 0 rgba(74,222,128,0);}}}}

  .top {{position:relative; overflow:hidden; color:#fff; border-radius:22px; padding:24px 28px; display:flex;
         align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px;
         background:linear-gradient(120deg, #00557A 0%, {HEADER_BG} 35%, #0092CC 100%);
         box-shadow:0 14px 34px rgba(0,85,122,0.28); animation:rise .5s ease both;}}
  .top::before {{content:""; position:absolute; width:340px; height:340px; right:-90px; top:-160px; border-radius:50%;
                 background:radial-gradient(circle, rgba(239,184,57,0.35), transparent 65%);}}
  .top::after {{content:""; position:absolute; width:260px; height:260px; left:38%; bottom:-170px; border-radius:50%;
                background:radial-gradient(circle, rgba(255,255,255,0.14), transparent 65%);}}
  .top > * {{position:relative; z-index:1;}}
  .top .left {{display:flex; align-items:center; gap:18px;}}
  .top .logo img {{height:46px; display:block; margin:0 auto 4px;}}
  .logo {{line-height:1; text-align:center; background:rgba(255,255,255,0.10); border:1px solid rgba(255,255,255,0.18);
          border-radius:14px; padding:8px 12px;}}
  .logo .eko {{font-weight:800; font-size:30px; letter-spacing:-1px;
              background:{SAFFRON_GRADIENT}; -webkit-background-clip:text; background-clip:text; color:transparent;}}
  .logo .sub {{font-size:9px; letter-spacing:4px; color:#EFB839; font-weight:700;}}
  .divider {{display:none;}}
  .top h1 {{color:#fff; font-size:1.75rem; margin:0; padding:0; font-weight:800; letter-spacing:-0.5px;}}
  .top h1 span {{background:{SAFFRON_GRADIENT}; -webkit-background-clip:text; background-clip:text; color:transparent;}}
  .top .small {{font-size:0.9rem; opacity:0.9;}}
  .since {{display:inline-block; margin-left:6px; font-size:0.72rem; font-weight:700; letter-spacing:0.08em;
           text-transform:uppercase; color:#FBD38D; border:1px solid rgba(251,211,141,0.45); border-radius:999px;
           padding:1px 9px; vertical-align:1px;}}
  .logo svg {{display:block;}}
  .top .right {{text-align:right; font-size:0.9rem; line-height:1.9; white-space:nowrap; flex-shrink:0;}}
  .top .chip {{display:inline-block; background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.25);
              border-radius:999px; padding:4px 14px; font-weight:700; backdrop-filter:blur(4px);}}
  .live {{display:inline-flex; align-items:center; gap:8px; font-size:0.82rem; opacity:0.95;}}
  .live .dot {{width:9px; height:9px; border-radius:50%; background:#4ADE80; animation:pulse 2s infinite;}}
  .test {{background:#FFF6E0; color:#8A5A00; border:1px solid #F3D48A; padding:10px 16px; border-radius:12px;
          font-weight:600; margin-bottom:16px;}}

  .cards {{display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:14px; margin-bottom:16px;}}
  .cards.six {{grid-template-columns:repeat(6, minmax(0, 1fr));}}
  @media (max-width: 1100px) {{ .cards, .cards.six {{grid-template-columns:repeat(3, minmax(0, 1fr));}} }}
  @media (max-width: 640px) {{ .cards, .cards.six {{grid-template-columns:repeat(2, minmax(0, 1fr));}} }}
  .card {{--c:{PRIMARY}; background:#fff; border:1px solid {BORDER}; border-radius:18px; padding:16px 18px 14px;
          position:relative; overflow:hidden; box-shadow:0 2px 10px rgba(12,18,23,0.05);
          transition:transform .18s ease, box-shadow .18s ease; animation:rise .45s ease both;}}
  .card:nth-child(2) {{animation-delay:.05s;}} .card:nth-child(3) {{animation-delay:.1s;}}
  .card:nth-child(4) {{animation-delay:.15s;}} .card:nth-child(5) {{animation-delay:.2s;}} .card:nth-child(6) {{animation-delay:.25s;}}
  .card:hover {{transform:translateY(-3px); box-shadow:0 12px 26px rgba(12,18,23,0.10);}}
  .card::before {{content:""; position:absolute; left:0; right:0; top:0; height:4px; background:var(--c);}}
  .card.orange {{--c:#E69B1A;}} .card.green {{--c:{FOREST};}} .card.red {{--c:{RED};}} .card.purple {{--c:#6C4BB4;}}
  .card .head {{display:flex; align-items:center; justify-content:space-between;}}
  .card .t {{font-size:0.84rem; font-weight:700; color:{MUTED}; text-transform:uppercase; letter-spacing:0.04em;}}
  .card .ic {{width:38px; height:38px; border-radius:12px; display:flex; align-items:center; justify-content:center;
             font-size:1.15rem; background:color-mix(in srgb, var(--c) 12%, white);}}
  .card .n {{font-size:2.4rem; font-weight:800; color:{INK}; line-height:1.1; margin-top:6px; letter-spacing:-1px;}}
  .card .s {{font-size:0.82rem; color:{MUTED};}}
  div[data-testid="stExpander"] details {{border:none !important; border-bottom:1px solid #F0ECE6 !important;
      border-radius:0 !important; background:transparent !important;}}
  div[data-testid="stExpander"] summary {{padding:8px 4px !important; font-size:0.95rem;}}
  div[data-testid="stExpander"] summary:hover {{background:#FBF8F3 !important; border-radius:10px;}}
  .st-key-login_card {{background:#fff; border:1px solid {BORDER}; border-radius:24px; overflow:hidden; padding:0 0 18px;
      box-shadow:0 24px 60px rgba(0,85,122,0.18); animation:rise .5s ease both;}}
  .st-key-login_card > div {{gap:0.6rem;}}
  .login-hero {{position:relative; overflow:hidden; text-align:center; color:#fff; padding:30px 24px 26px;
      background:linear-gradient(135deg, #00557A 0%, {HEADER_BG} 45%, #0092CC 100%);}}
  .login-hero::before {{content:""; position:absolute; width:260px; height:260px; right:-90px; top:-120px; border-radius:50%;
      background:radial-gradient(circle, rgba(239,184,57,0.40), transparent 65%);}}
  .login-hero > * {{position:relative;}}
  .login-hero .logo {{display:inline-block; padding:10px 16px; margin-bottom:12px;}}
  .login-hero .logo img {{height:52px;}}
  .login-hero h1 {{color:#fff; font-size:1.7rem; font-weight:800; margin:6px 0 2px; padding:0; letter-spacing:-0.5px;}}
  .login-hero h1 span {{background:{SAFFRON_GRADIENT}; -webkit-background-clip:text; background-clip:text; color:transparent;}}
  .login-brand {{font-size:0.92rem; font-weight:600; opacity:0.95;}}
  .login-tag {{font-size:0.84rem; opacity:0.85;}}
  .st-key-login_card [data-testid="stForm"] {{padding:6px 26px 0;}}
  .login-label {{font-weight:700; color:{INK}; font-size:0.95rem; margin:6px 0 2px;}}
  .st-key-login_card input {{font-size:1rem !important;}}
  .st-key-login_card [data-baseweb="input"] {{border:1.5px solid {BORDER} !important; border-radius:12px !important;
      background:#FBF8F3 !important;}}
  .st-key-login_card [data-baseweb="input"]:focus-within {{border-color:#0092CC !important;
      box-shadow:0 0 0 3px rgba(0,146,204,0.15);}}
  .title-x {{color:#fff; font-weight:800; letter-spacing:-0.5px; line-height:1.15; font-size:1.75rem; margin:4px 0 2px;}}
  .login-hero .title-x {{font-size:1.8rem; margin:8px 0 4px;}}
  .title-x .gold {{background:{SAFFRON_GRADIENT}; -webkit-background-clip:text; background-clip:text;
      -webkit-text-fill-color:transparent; color:transparent;}}
  .st-key-login_card [data-testid="stTextInputRootElement"] {{border:1.5px solid {BORDER} !important; border-radius:12px !important;
      background:#FBF8F3 !important;}}
  .st-key-login_card [data-testid="stTextInputRootElement"]:focus-within {{border-color:#0092CC !important;
      box-shadow:0 0 0 3px rgba(0,146,204,0.15);}}
  .st-key-login_btn button {{background:linear-gradient(120deg, {HEADER_BG}, #0092CC) !important; color:#fff !important;
      border:none !important; border-radius:12px !important; min-height:46px; font-weight:700 !important;
      box-shadow:0 8px 18px rgba(0,102,143,0.25);}}
  .st-key-login_btn button p {{color:#fff !important; font-weight:700 !important; font-size:1rem !important;}}
  .st-key-login_btn button:hover {{filter:brightness(1.05);}}
  .st-key-login_card [data-testid="stAlert"] {{margin:0 26px;}}
  .login-foot {{text-align:center; font-size:0.78rem; color:{MUTED}; padding:0 20px;}}
  .signed-in {{text-align:right; white-space:nowrap; font-size:0.82rem; color:{MUTED}; font-weight:600;}}
  .st-key-logout_btn button {{border:1px solid {BORDER} !important; border-radius:10px !important; background:#fff !important;
      color:{HEADER_BG} !important; min-height:36px;}}
  .st-key-logout_btn button p {{color:{HEADER_BG} !important; font-weight:700 !important; white-space:nowrap;}}
  .st-key-logout_btn button:hover {{border-color:{HEADER_BG} !important; background:#F0F7FA !important;}}
  [class*="st-key-kpi_"] {{margin-top:-6px;}}
  [class*="st-key-kpi_"] button {{border-radius:999px !important; border:1px solid {BORDER} !important;
      background:#fff !important; color:{HEADER_BG} !important; font-weight:700 !important; min-height:34px;
      transition:all .15s ease;}}
  [class*="st-key-kpi_"] button:hover {{background:linear-gradient(120deg, {HEADER_BG}, #0092CC) !important;
      color:#fff !important; border-color:transparent !important; box-shadow:0 6px 14px rgba(0,102,143,0.25);}}
  [class*="st-key-kpi_"] button p {{font-weight:700 !important; color:inherit !important;}}
  div[data-testid="stDialog"] div[role="dialog"] {{border-radius:20px; width:min(1200px, 95vw) !important;}}

  .box-title {{font-family:"Plus Jakarta Sans", sans-serif; font-size:1.1rem; font-weight:700; color:{INK};}}
  .box-sub {{font-size:0.86rem; color:{MUTED}; margin-bottom:6px;}}
  div[data-testid="stVerticalBlockBorderWrapper"] {{background:#fff; border-color:{BORDER} !important; border-radius:18px;
                                                    box-shadow:0 2px 10px rgba(12,18,23,0.04); animation:rise .45s ease both;}}

  .stTabs [role="tablist"] {{gap:6px !important; border-bottom:none !important; margin:0 0 18px; padding:6px;
                             background:#fff; border:1px solid {BORDER}; border-radius:16px; width:100%;
                             box-shadow:0 2px 8px rgba(12,18,23,0.04);}}
  .stTabs [role="tab"] {{flex:1 1 0; justify-content:center; font-weight:700 !important; padding:11px 18px !important;
                         border-radius:12px !important; background:transparent !important; border:none !important;
                         color:{MUTED} !important;}}
  .stTabs [role="tab"]:hover {{background:#F5F1EB !important; color:{INK} !important;}}
  .stTabs [role="tab"] p {{font-size:1.02rem !important; font-weight:700 !important;}}
  .stTabs [role="tab"][aria-selected="true"] {{background:linear-gradient(120deg, {HEADER_BG}, #0092CC) !important;
                                               color:#fff !important; box-shadow:0 6px 16px rgba(0,102,143,0.25);}}
  .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{display:none;}}
  section[data-testid="stSidebar"] {{background:#fff; border-right:1px solid {BORDER};}}
  .stDownloadButton button {{background:{SAFFRON_GRADIENT}; color:#fff; border:none; border-radius:12px; font-weight:600;}}
  .stDownloadButton button:hover {{color:#fff; filter:brightness(0.97);}}

  .today {{background:#fff; border:1px solid {BORDER}; border-left:6px solid #E69B1A; border-radius:14px;
           padding:14px 18px; margin-bottom:16px;}}
  .today.quiet {{border-left-color:{FOREST};}}
  .today-h {{font-family:"Plus Jakarta Sans", sans-serif; font-weight:700; font-size:1.05rem; color:{INK}; margin-bottom:8px;}}
  .small-note {{font-size:0.85rem; color:{MUTED};}}

  .cc {{display:grid; grid-template-columns:150px 1fr 260px; gap:16px; align-items:center; background:#fff;
         border:1px solid {BORDER}; border-left:6px solid var(--c); border-radius:14px; padding:12px 16px; margin-bottom:10px;
         transition:transform .15s ease, box-shadow .15s ease; animation:rise .4s ease both;}}
  .cc:hover {{transform:translateX(3px); box-shadow:0 8px 20px rgba(12,18,23,0.08);}}
  @media (max-width: 900px) {{ .cc {{grid-template-columns:1fr;}} }}
  .cc .csp {{font-weight:800; font-size:1.05rem; color:{INK}; letter-spacing:0.02em;}}
  .cc .id {{font-size:0.78rem; color:{MUTED};}}
  .cc .prob {{display:inline-block; font-weight:700; font-size:0.86rem; border-radius:999px; padding:3px 11px;
              background:color-mix(in srgb, var(--c) 12%, white); color:var(--c);}}
  .cc .meta {{font-size:0.82rem; color:{MUTED}; margin-top:4px;}}
  .cc .act {{font-size:0.84rem; color:{INK}; line-height:1.35;}}
  .cc .days {{display:inline-block; font-weight:800; font-size:0.8rem; color:#fff; background:var(--c);
              border-radius:999px; padding:2px 10px; margin-bottom:4px;}}

  .split {{display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:6px 0 10px;}}
  .split .yes, .split .no {{border-radius:12px; padding:12px 16px;}}
  .split .yes {{background:rgba(29,94,67,0.08); border:1px solid rgba(29,94,67,0.25);}}
  .split .no {{background:rgba(192,57,43,0.06); border:1px solid rgba(192,57,43,0.22);}}
  .split .big {{font-family:"Plus Jakarta Sans",sans-serif; font-weight:800; font-size:1.8rem;}}

  .rank {{display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 4px;
           border-bottom:1px solid #F0ECE6;}}
  .rank:last-child {{border-bottom:none;}}
  .rank .l {{display:flex; align-items:center; gap:10px; color:{INK}; font-size:0.95rem; font-weight:500;}}
  .rank .i {{width:24px; height:24px; border-radius:8px; background:#F5F1EB; color:{MUTED}; display:flex;
             align-items:center; justify-content:center; font-size:0.78rem; font-weight:700;}}
  .rank .r {{display:flex; align-items:center; gap:10px;}}
  .rank .pct {{color:{MUTED}; font-size:0.82rem; min-width:38px; text-align:right;}}
  .rank .b {{min-width:44px; text-align:center; border-radius:999px; padding:3px 10px; font-weight:800;
             font-size:0.92rem; color:#fff; white-space:nowrap;}}
  .row {{display:flex; gap:12px; padding:7px 0; border-bottom:1px solid #F0ECE6;}}
  .row .k {{width:120px; color:{MUTED}; font-size:0.9rem;}} .row .v {{font-size:0.95rem; color:{INK};}}

  /* clean, quiet polish: ranked rows, upload box, buttons, tables */
  div[data-testid="stExpander"] summary {{border-radius:10px; transition:background .15s ease;}}
  div[data-testid="stExpander"] summary p {{font-size:0.97rem !important;}}
  div[data-testid="stExpander"] details[open] {{background:#FBF8F3 !important; border-radius:12px !important;
      border-bottom-color:transparent !important; margin:4px 0;}}
  div[data-testid="stExpander"] details[open] > summary {{border-bottom:1px solid #EFE9E1;}}
  [data-testid="stDataFrame"] {{border:1px solid #EFE9E1; border-radius:12px; overflow:hidden;}}
  .box-title {{padding-left:10px; border-left:4px solid #E69B1A; line-height:1.3;}}
  .box-sub {{padding-left:14px;}}
  .st-key-upload_box {{background:#fff;}}
  .st-key-upload_box [data-testid="stFileUploaderDropzone"] {{background:#F0F7FA; border:1.5px dashed #7FB8CF;
      border-radius:12px;}}
  .st-key-upload_box [data-testid="stFileUploaderDropzoneInstructions"] small {{display:none;}}
  .stApp button[kind="primary"] {{background:{HEADER_BG} !important; border:none !important; border-radius:10px !important;
      font-weight:700 !important;}}
  .stApp button[kind="primary"]:hover {{background:#00557A !important;}}
  .stApp button[kind="primary"]:disabled {{background:#C9D9E1 !important;}}
  section[data-testid="stSidebar"] h3 {{font-size:1.05rem !important; color:{INK};}}

</style>""", unsafe_allow_html=True)


# --- data --------------------------------------------------------------------------------------------
def data_dirs() -> list[Path]:
    found = [p.parent for p in PROJECT.glob("output*/issues.csv")]
    return sorted(found, key=lambda p: (p.name != "output", p.name))


def cli_data_dir() -> Path | None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    args, _ = ap.parse_known_args(sys.argv[1:])
    return (PROJECT / args.data).resolve() if args.data else None


def priority(row) -> str:
    if row["Status"] in ("Solved", "Not a problem"):
        return "🟢 Solved"
    step = str(row["Next Step"])
    if step.startswith("App visit recorded"):
        return "🟣 Confirm & close"
    if row["Status"] == "Escalated" or step.startswith("Escalate"):
        return "🔴 Escalate"
    if step.startswith("Follow up"):
        return "🟠 Follow up"
    return "🔵 New"


@st.cache_data(show_spinner=False)
def load(data_dir: str, mtime: float):
    d = Path(data_dir)
    df = pd.read_csv(d / "issues.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df["Message Date"] = pd.to_datetime(df["Message Date"], errors="coerce").dt.date
    # Blank distance = not mentioned. It is never treated as zero.
    df["Distance Mismatch (metres)"] = pd.to_numeric(df["Distance Mismatch (metres)"].replace("", None), errors="coerce")
    df["Days Pending"] = pd.to_numeric(df.get("Days Pending", "").replace("", None), errors="coerce")
    df["Problem"] = df["Issue Type"].map(PROBLEM_NAMES).fillna(df["Issue Type"])
    df["Status"] = df["Resolution Status"].map(STATUS_NAMES).fillna(df["Resolution Status"])
    df["CSP"] = df["CSP ID"].where(df["CSP ID"] != NA, NOT_MENTIONED)
    df["Area"] = df["District"].where(df["District"] != NA, NOT_MENTIONED)
    df["Added On"] = pd.to_datetime(df["Added On"], errors="coerce").dt.date
    if "App Visit After Complaint" not in df:
        df["App Visit After Complaint"] = ""
    df["App Visit"] = pd.to_datetime(df["App Visit After Complaint"].replace("", None), errors="coerce").dt.date
    df["Priority"] = df.apply(priority, axis=1)
    df["_rank"] = df["Priority"].map(PRIORITY_RANK)

    visits = pd.DataFrame()
    if (d / "visits.csv").exists() and (d / "visits.csv").stat().st_size > 5:
        visits = pd.read_csv(d / "visits.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if len(visits):
            visits["Visit Date"] = pd.to_datetime(visits["Visit Date"], errors="coerce").dt.date
            visits["Visit Score"] = pd.to_numeric(visits["Visit Score"], errors="coerce")
            if "Visit Done" not in visits.columns:  # data written before v1.7
                visits["Visit Done"] = (visits["Visit Score"].isna() | (visits["Visit Score"] > 0)).map({True: "Yes", False: "No"})
    summary = json.loads((d / "run_summary.json").read_text(encoding="utf-8"))
    brief_file = d / "agent_brief.json"
    brief = json.loads(brief_file.read_text(encoding="utf-8")) if brief_file.exists() else {}
    return df, visits, summary, brief


@st.cache_data(show_spinner=False)
def load_locations(data_dir: str, mtime: float) -> pd.DataFrame:
    """CSP locations from config/csp_locations.csv + uploaded lists, joined to the current complaints and visits.
    Built here (not only by process.py) so the tab works right after a deploy, without a report run."""
    issues, visits_, _, _ = load(data_dir, mtime)
    done = (visits_[(visits_["Duplicate Row"] == "No") & (visits_["Visit Done"] != "No")]
            if len(visits_) else pd.DataFrame(columns=["CSP Code", "Visit Date"]))
    loc = merge_locations(read_locations(PROJECT / "input" / "csp_locations", PROJECT), issues, done)
    if loc.empty:
        return loc
    loc["Last App Visit"] = loc["Last App Visit"].map(lambda d: f"{d:%d %b %Y}" if pd.notna(d) and d != "" else "")
    received = pd.to_datetime(loc["Received"].replace("", None), errors="coerce") + pd.Timedelta(hours=5, minutes=30)
    loc["Received (IST)"] = received.dt.strftime("%d %b %H:%M").fillna("")
    return loc


def location_view(loc: pd.DataFrame) -> pd.DataFrame:
    """The location columns to show, leaving out ones that are empty for every CSP (e.g. names not shipped)."""
    keep = [c for c in LOCATION_VIEW if c in ("CSP Code", "Map") or
            not loc[c].astype(str).isin(["", "nan", "None", NA]).all()]
    return loc[keep]


def evidence_file(data_dir: Path, rel: str) -> Path | None:
    """Only show files from this data folder's evidence directory."""
    if not rel:
        return None
    p = (data_dir / rel).resolve()
    return p if p.is_file() and (data_dir / "evidence").resolve() in p.parents else None


def to_excel(df: pd.DataFrame, sheet: str = "Complaints") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name=sheet, index=False)
        ws = xw.sheets[sheet]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(50, max(12, len(str(col[0].value)) + 4))
    return buf.getvalue()




def card(title: str, number, sub: str = "", tone: str = "", icon: str = "") -> str:
    return (f"<div class='card {tone}'><div class='head'><span class='t'>{html.escape(title)}</span>"
            f"<span class='ic'>{icon}</span></div>"
            f"<div class='n'>{number}</div><div class='s'>{html.escape(sub)}</div></div>")


def box_title(title: str, sub: str = "") -> None:
    st.markdown(f"<div class='box-title'>{title}</div>" + (f"<div class='box-sub'>{sub}</div>" if sub else ""),
                unsafe_allow_html=True)


DATE_COL = st.column_config.DateColumn(format="DD MMM")


def complaint_view(d: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Priority": d["Priority"], "No.": d["Record ID"], "Date": d["Message Date"], "CSP": d["CSP"],
        "Problem": d["Problem"], "Reported by": d["Reported By"], "Chat": d["Group/Chat"], "Status": d["Status"],
        "Days": d["Days Pending"], "App visit": d["App Visit"].map(lambda x: f"✅ {x:%d %b}" if pd.notna(x) else "—"),
        "What to do": d["Suggested Action"], "Message": d["Issue Description"],
    })


def csp_view(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("CSP")
    out = pd.DataFrame({
        "Complaints": g.size(),
        "Pending": g["Status"].apply(lambda x: int(x.isin(["Pending", "Escalated"]).sum())),
        "First": g["Message Date"].min(), "Latest": g["Message Date"].max(),
        "Problems": g["Problem"].apply(lambda x: ", ".join(sorted(set(x)))),
        "Reported by": g["Reported By"].apply(lambda x: ", ".join(sorted(set(x)))),
        "App visit": g["App Visit"].apply(lambda x: f"✅ {x.dropna().min():%d %b}" if x.notna().any() else "❌ Not in app"),
    }).sort_values(["Complaints", "Latest"], ascending=False).reset_index()
    return out


def visit_view(d: pd.DataFrame) -> pd.DataFrame:
    return d[["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name", "Visit Score", "Visit Done",
              "Compliance Completed", "Closures Completed"]]


def dc_view(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby(["DC User", "DC Name"])
    return pd.DataFrame({"Visits": g.size(), "CSPs": g["CSP Code"].nunique(), "Avg score": g["Visit Score"].mean().round(1),
                         f"Low score (<{LOW_SCORE})": g["Visit Score"].apply(lambda x: int((x < LOW_SCORE).sum())),
                         "Circles": g["Circle"].apply(lambda x: ", ".join(sorted(set(x))))}
                        ).sort_values("Visits", ascending=False).reset_index()


VIEWS = {"complaints": complaint_view, "csp": csp_view, "visits": visit_view, "dc": dc_view,
         "locations": lambda d: d}


@st.dialog("📂 Details", width="large")
def show_list(title: str, frame: pd.DataFrame, kind: str = "complaints") -> None:
    st.markdown(f"<div class='box-title' style='font-size:1.25rem'>{html.escape(title)}</div>", unsafe_allow_html=True)
    if frame.empty:
        st.info("Is mein abhi kuch nahi hai.")
        return
    view = VIEWS[kind](frame)
    st.caption(f"{len(view)} row(s)")
    st.dataframe(view, hide_index=True, width="stretch", height=min(560, 40 + 35 * len(view)),
                 column_config={"Date": DATE_COL, "First": DATE_COL, "Latest": DATE_COL,
                                "Visit Date": st.column_config.DateColumn(format="DD MMM YYYY"),
                                "Days": st.column_config.NumberColumn(format="%d d"),
                                "Visit Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                                "What to do": st.column_config.TextColumn(width="large"),
                                "Map": st.column_config.LinkColumn("Map", display_text="📍 Open")})
    slug = "".join(ch if ch.isalnum() else "_" for ch in title.lower()).strip("_")[:40] or "list"
    st.download_button("⬇ Download Excel", to_excel(view, "List"), f"{slug}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def drill(frame: pd.DataFrame, keys: pd.Series, color: str = "blue", view: str = "complaints", limit: int = 10,
          order: list | None = None, value: pd.Series | None = None, unit: str = "", key: str = "drill") -> None:
    """Ranked list where every row is an expander: click it to see exactly which records are counted. In a visits
    list, click a row inside to open that CSP's full history (all its app visits and complaints)."""
    keys = keys.astype(str)
    counts = keys.value_counts() if value is None else value.groupby(keys).max().sort_values(ascending=False)
    if order is not None:
        counts = counts.reindex([o for o in order if o in counts.index])
    counts = counts[counts > 0]
    if counts.empty:
        st.markdown("<div class='small-note'>Kuch nahi hai.</div>", unsafe_allow_html=True)
        return
    for i, (name, n) in enumerate(counts.head(limit).items(), start=1):
        badge = "gray" if name == NOT_MENTIONED else color
        with st.expander(f"{i}. &nbsp; **{name}** &nbsp; :{badge}-badge[{n:,.0f}{unit}]"):
            sub = frame[keys == name]
            v = VIEWS[view](sub)
            if view == "complaints":  # short table inside a list row; full detail stays in the popup / Complaints tab
                v = v[["Priority", "No.", "Date", "CSP", "Problem", "Reported by", "Days", "App visit"]]
            if view == "visits":
                st.caption(f"{len(sub)} visit · {sub['CSP Code'].nunique()} CSP · avg score "
                           f"{sub['Visit Score'].mean():.0f} · score < {LOW_SCORE}: {int((sub['Visit Score'] < LOW_SCORE).sum())}"
                           " · 👇 CSP pe click karo, uski poori history khulegi")
            picked = st.dataframe(v, hide_index=True, width="stretch", height=min(360, 40 + 35 * len(v)),
                                  key=f"{key}_{i}", on_select="rerun" if view == "visits" else "ignore",
                                  selection_mode="single-row",
                                  column_config={"Date": DATE_COL, "First": DATE_COL, "Latest": DATE_COL,
                                                 "Visit Date": st.column_config.DateColumn(format="DD MMM YYYY"),
                                                 "Days": st.column_config.NumberColumn(format="%d d"),
                                                 "Visit Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                                                format="%d")})
            if view == "visits" and picked.selection.rows:
                csp_history(str(v.iloc[picked.selection.rows[0]]["CSP Code"]))
    if len(counts) > limit:
        st.caption(f"+ {len(counts) - limit} more – use the filters on the left to see them")


@st.dialog("📍 CSP ki poori history", width="large")
def csp_history(code: str) -> None:
    st.markdown(f"<div class='box-title' style='font-size:1.25rem'>CSP {html.escape(code)}</div>", unsafe_allow_html=True)
    cv = visits[(visits["CSP Code"] == code) & (visits["Duplicate Row"] == "No")].sort_values("Visit Date")
    box_title("🗓 App visits", f"{len(cv)} visit (score 0 bhi)")
    if cv.empty:
        st.info("Is CSP ki koi app visit nahi hai.")
    else:
        st.dataframe(visit_view(cv), hide_index=True, width="stretch",
                     column_config={"Visit Date": st.column_config.DateColumn(format="DD MMM YYYY"),
                                    "Visit Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                                   format="%d")})
    cc = df[(df["CSP"] == code) & (df["Duplicate Flag"] != "Possible Duplicate")]
    box_title("📋 WhatsApp complaints", f"{len(cc)} complaint")
    if cc.empty:
        st.info("Is CSP ki koi complaint nahi aayi.")
    else:
        st.dataframe(complaint_view(cc)[["No.", "Date", "Problem", "Reported by", "Status", "App visit", "Message"]],
                     hide_index=True, width="stretch", column_config={"Date": DATE_COL})


def card_row(items: list[tuple]) -> None:
    """items: (card_html, key, popup_title, frame, kind). Each card gets a 'Dekho' button that opens the popup."""
    cols = st.columns(len(items), gap="small")
    for col, (card_html, key, title, frame, kind) in zip(cols, items, strict=True):
        with col:
            st.markdown(card_html, unsafe_allow_html=True)
            if st.button("Dekho ▸", key=key, width="stretch", help=f"Open: {title}"):
                show_list(title, frame, kind)

# --- logo ----------------------------------------------------------------------------------------------
logo = PROJECT / BRAND["logo"]
# Official Eko logo (dashboard/assets/logo.png, transparent background); plain wordmark if the file is missing.
logo_html = ("<div class='logo'><img src='data:image/png;base64,"
             f"{base64.b64encode(logo.read_bytes()).decode()}' alt='eko'/><div class='sub'>KIOSK</div></div>"
             if logo.is_file() else "<div class='logo'><div class='eko'>eko</div><div class='sub'>KIOSK</div></div>")


# --- login ---------------------------------------------------------------------------------------------
def _expected_secret() -> tuple[str, str]:
    """('sha256', hex) when DASHBOARD_PASSWORD_SHA256 is set, ('plain', pw) for DASHBOARD_PASSWORD, else ('', '')."""
    hashed = os.environ.get("DASHBOARD_PASSWORD_SHA256", "").strip().lower()
    if hashed:
        return "sha256", hashed
    plain = os.environ.get("DASHBOARD_PASSWORD", "").strip()
    return ("plain", plain) if plain else ("", "")


def require_login() -> None:
    kind, secret = _expected_secret()
    if not kind or st.session_state.get("auth_ok"):
        return  # no password configured (local use) or already signed in
    st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid, st.container(key="login_card"):
        st.markdown(
            f"<div class='login-hero'>{logo_html}"
            f"<div class='login-brand'>{html.escape(BRAND['company'])} <span class='since'>Since 2007</span></div>"
            "<div class='title-x'>CSP GPS <span class='gold'>Complaints</span></div>"
            "<div class='login-tag'>SBI CSP Visit app · failed visits, tracked and followed up</div></div>",
            unsafe_allow_html=True)
        with st.form("login", border=False):
            st.markdown("<div class='login-label'>🔒 Team login</div>", unsafe_allow_html=True)
            pw = st.text_input("Password", type="password", placeholder="Password daaliye",
                               label_visibility="collapsed")
            ok = st.form_submit_button("Open dashboard  →", width="stretch", key="login_btn")
        if ok:
            given = hashlib.sha256(pw.encode()).hexdigest() if kind == "sha256" else pw
            if hmac.compare_digest(given, secret):
                st.session_state["auth_ok"] = True
                st.rerun()
            st.error("Password galat hai. Dobara try kijiye.")
        st.markdown("<div class='login-foot'>Internal use only · phone numbers hidden · data stays on Eko systems</div>",
                    unsafe_allow_html=True)
    st.stop()


require_login()

# --- header --------------------------------------------------------------------------------------------
dirs = data_dirs()


def header(right: str) -> None:
    st.markdown(f"""<div class='top'><div class='left'>{logo_html}<div class='divider'></div><div>
        <div class='title-x'>CSP GPS <span class='gold'>Complaints</span></div>
        <div class='small'>{html.escape(BRAND['company'])} <span class='since'>Since 2007</span></div>
        <div class='small'>CSP visits that failed in the SBI CSP Visit app</div>
        </div></div><div class='right'>{right}</div></div>""", unsafe_allow_html=True)
    sign_out_bar()


def sign_out_bar() -> None:
    """'Signed in' note + Sign out button under the header (only when a dashboard password is set)."""
    if not _expected_secret()[0]:
        return
    _, note, btn = st.columns([3, 2, 1.3], vertical_alignment="center")
    note.markdown("<div class='signed-in'>🔓 Team login · signed in</div>", unsafe_allow_html=True)
    if btn.button("⎋ Sign out", key="logout_btn", width="stretch"):
        st.session_state.clear()
        st.rerun()


UPLOADS_ON = uploads_enabled(os.environ)


def _rebuild() -> tuple[bool, str]:
    with st.spinner("Report ban rahi hai… (1–2 minute)"):
        ok, tail = run_report(PROJECT)
    log_upload(PROJECT / "logs", [UploadResult("(rebuild)", ok, "report rebuilt" if ok else "report failed")])
    return ok, tail


def upload_box() -> None:
    """Sidebar box to add a WhatsApp export or SBI visit report and rebuild the report (CSP_UPLOADS=1)."""
    if not UPLOADS_ON:
        return
    with st.sidebar, st.container(border=True, key="upload_box"):
        st.markdown("### 📤 Naya data daalo")
        st.caption("WhatsApp export (.zip / .txt) ya SBI visit report (.xlsx). Max 10 MB.")
        files = st.file_uploader("Files", type=["zip", "txt", "xlsx", "csv"], accept_multiple_files=True,
                                 label_visibility="collapsed", key=f"up_{st.session_state.get('up_round', 0)}")
        if st.button("Upload & update", key="upload_btn", width="stretch", disabled=not files, type="primary"):
            results = [save_upload(f.name, f.getvalue(), PROJECT / "input") for f in files]
            log_upload(PROJECT / "logs", results)
            # rebuild for new files AND for files already there (an earlier upload may have saved them
            # without a successful report run)
            rebuild = any(r.ok or r.known for r in results)
            ok, tail = _rebuild() if rebuild else (False, "")
            st.session_state["upload_msgs"] = (
                [(r.ok or r.known, f"{r.name}: {r.message}") for r in results]
                + ([(ok, "Report update ho gayi ✅" if ok else "Report nahi bani – neeche detail")] if rebuild else []))
            st.session_state["upload_tail"] = "" if ok else tail
            st.session_state["up_round"] = st.session_state.get("up_round", 0) + 1  # clears the picker
            st.rerun()
        if st.button("🔄 Report dobara banao", key="rebuild_btn", width="stretch",
                     help="Jo bhi files server par hain, unse report fir se banao"):
            ok, tail = _rebuild()
            st.session_state["upload_msgs"] = [(ok, "Report update ho gayi ✅" if ok else "Report nahi bani – neeche detail")]
            st.session_state["upload_tail"] = "" if ok else tail
            st.rerun()
        for good, msg in st.session_state.get("upload_msgs", []):
            (st.success if good else st.warning)(msg)
        if st.session_state.get("upload_tail"):
            st.code(st.session_state["upload_tail"], language=None)


upload_box()

if not dirs:
    header("")
    st.info("Abhi data nahi hai. Left side '📤 Naya data daalo' mein WhatsApp export daaliye"
            if UPLOADS_ON else "No data yet. Put the WhatsApp export in Downloads and double-click 'Update Report.bat'.")
    st.stop()

default = cli_data_dir()
by_name = {d.name: d for d in dirs}
names = list(by_name)
chosen = by_name[names[names.index(default.name) if default and default.name in names else 0]]
if len(names) > 1:
    with st.sidebar:
        chosen = by_name[st.selectbox("Data", names, index=names.index(chosen.name),
                                      help="output = real data · output_synthetic = test data")]

loaded_mtime = (chosen / "issues.csv").stat().st_mtime
df, visits, summary, brief = load(str(chosen), loaded_mtime)
locations = load_locations(str(chosen), loaded_mtime)


@st.fragment(run_every="60s")
def _watch_for_new_data() -> None:
    """Re-draw the whole dashboard as soon as a scheduled run writes new data (checked every minute)."""
    if (chosen / "issues.csv").stat().st_mtime != loaded_mtime:
        st.rerun()


_watch_for_new_data()
is_test = summary.get("data_source") == "SYNTHETIC TEST DATA"
start, end = summary.get("period_start") or "—", summary.get("period_end") or "—"
header(f"<span class='chip'>📅 {start} → {end}</span><br><span class='live'><span class='dot'></span>"
       f"Updated {str(summary.get('generated_at', '')).replace('T', ' ')[:16]} · "
       f"{'📤 upload se update' if UPLOADS_ON else 'auto 10:00 &amp; 15:00'}</span>")
if is_test:
    st.markdown("<div class='test'>⚠ TEST DATA – these are not real complaints.</div>", unsafe_allow_html=True)
if df.empty:
    st.info("No GPS / location complaints found.")
    st.stop()

# --- filters ---------------------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🔎 Filter")
    dmin, dmax = min(df["Message Date"]), max(df["Message Date"])
    dates = st.date_input("Complaint date", (dmin, dmax), min_value=dmin, max_value=dmax)
    f_chat = st.multiselect("Chat", sorted(df["Group/Chat"].unique()), placeholder="All chats",
                            help="The WhatsApp group or a DC's one-to-one chat the complaint came from")
    f_dc = st.multiselect("Reported by", sorted(df["Reported By"].unique()), placeholder="All DCs")
    f_problem = st.multiselect("Problem", [p for p in PROBLEM_NAMES.values() if p in set(df["Problem"])],
                               placeholder="All problems")
    f_area = st.multiselect("District", sorted(df["Area"].unique()), placeholder="All districts")
    show_dupes = st.checkbox("Show repeated reports", value=False,
                             help="Same complaint forwarded or reported again. Hidden so nothing is counted twice.")
    st.markdown("<div class='small-note'>Phone numbers are hidden. Data stays on this computer.</div>",
                unsafe_allow_html=True)

f = df.copy()
if isinstance(dates, (list, tuple)) and len(dates) == 2:
    f = f[(f["Message Date"] >= dates[0]) & (f["Message Date"] <= dates[1])]
for values, col in ((f_chat, "Group/Chat"), (f_dc, "Reported By"), (f_problem, "Problem"), (f_area, "Area")):
    if values:
        f = f[f[col].isin(values)]
if not show_dupes:
    f = f[f["Duplicate Flag"] != "Possible Duplicate"]
f = f.sort_values(["_rank", "Message Date"])

open_ = f["Status"].isin(["Pending", "Escalated"])

tab_home, tab_agent, tab_list, tab_visits, tab_locations, tab_photos = st.tabs(
    ["🏠 Overview", "🤖 Agent", "📋 Complaints", "🗓 App Visits", "📍 CSP Locations", "🖼 Photos"])


# --- Overview -------------------------------------------------------------------------------------------------
with tab_home:
    # only the pending complaints: what still needs action, most urgent first
    pending = f[open_]
    solved = f[f["Status"].isin(["Solved", "Not a problem"])]
    card_row([
        (card("Total complaints", len(f), "pending + solved", "", "📨"),
         "kpi_total", f"📨 All complaints – {len(f)}", f, "complaints"),
        (card("Pending complaints", len(pending), "not solved yet", "orange", "⏳"),
         "kpi_pending", f"⏳ Pending – {len(pending)}", pending, "complaints"),
        (card("Solved", len(solved), "done / closed", "green", "✅"),
         "kpi_solved", f"✅ Solved – {len(solved)}", solved, "complaints"),
    ])
    with st.container(border=True):
        box_title(f"⏳ Pending complaints – {len(pending)}", "Sabse zaroori upar · poori detail 📋 Complaints tab mein")
        if len(pending):
            color_of = {name: color for name, _, color in PRIORITIES}
            cards_html = []
            for _, r in pending.iterrows():
                days = "" if pd.isna(r["Days Pending"]) else f"{int(r['Days Pending'])} din se pending"
                cards_html.append(
                    f"<div class='cc' style='--c:{color_of.get(r['Priority'], PRIMARY)}'>"
                    f"<div><div class='csp'>{html.escape(r['CSP'])}</div><div class='id'>{html.escape(r['Record ID'])} · "
                    f"{r['Message Date']:%d %b}</div></div>"
                    f"<div><span class='prob'>{html.escape(r['Problem'])}</span>"
                    f"<div class='meta'>👤 {html.escape(r['Reported By'])} · 💬 {html.escape(r['Group/Chat'])}</div></div>"
                    f"<div><span class='days'>{html.escape(r['Priority'])}{' · ' + days if days else ''}</span>"
                    f"<div class='act'>{html.escape(r['Suggested Action'])}</div></div></div>")
            st.markdown("".join(cards_html), unsafe_allow_html=True)
        else:
            st.success("Kuch pending nahi hai. 🎉")

# --- Complaints ------------------------------------------------------------------------------------------------
with tab_list:
    c1, c2 = st.columns([3, 2])
    q = c1.text_input("Search", placeholder="🔍 CSP ID, DC name, district ya koi bhi shabd…", label_visibility="collapsed")
    pick = c2.segmented_control("Priority", ["All"] + [n for n, _, _ in PRIORITIES], default="All",
                                label_visibility="collapsed")
    t = f
    if pick and pick != "All":
        t = t[t["Priority"] == pick]
    if q:
        t = t[t.astype(str).apply(lambda c: c.str.contains(q, case=False, regex=False)).any(axis=1)]
    view = pd.DataFrame({
        "Priority": t["Priority"], "No.": t["Record ID"], "Date": t["Message Date"], "Time": t["Message Time"].str[:5],
        "CSP": t["CSP"], "Problem": t["Problem"], "Reported by": t["Reported By"], "Chat": t["Group/Chat"],
        "Status": t["Status"], "Days": t["Days Pending"],
        "App visit": t["App Visit"].map(lambda d: f"✅ {d:%d %b}" if pd.notna(d) else "—"),
        "Next step": t["Next Step"], "What to do": t["Suggested Action"], "Message": t["Issue Description"],
        "District": t["Area"], "Distance (m)": t["Distance Mismatch (metres)"],
    })
    a, b, c = st.columns([4, 1, 1])
    a.markdown(f"**{len(view)} complaint(s)** · sorted by priority, oldest first")
    name = "TEST_csp_gps_complaints" if is_test else "csp_gps_complaints"
    b.download_button("⬇ Excel", to_excel(view), f"{name}.xlsx", width="stretch",
                      mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c.download_button("⬇ CSV", view.to_csv(index=False).encode("utf-8-sig"), f"{name}.csv", "text/csv", width="stretch")
    st.dataframe(view, hide_index=True, height=520, width="stretch",
                 column_config={"Date": DATE_COL, "Days": st.column_config.NumberColumn(format="%d d"),
                                "Distance (m)": st.column_config.NumberColumn(format="%d m"),
                                "Message": st.column_config.TextColumn(width="large"),
                                "What to do": st.column_config.TextColumn(width="medium")})
    st.caption("App visit = the same CSP later appears in the SBI CSP Visit App report. Empty distance = not mentioned.")

    check = t[t["Needs Verification"] == "Yes"]
    with st.expander(f"⚠ {len(check)} complaint(s) to double-check in WhatsApp"):
        st.dataframe(pd.DataFrame({"No.": check["Record ID"], "Date": check["Message Date"], "CSP": check["CSP"],
                                   "Reported by": check["Reported By"], "What to check": check["Verification Reasons"]}),
                     hide_index=True, width="stretch", column_config={"Date": DATE_COL})

# --- App Visits ------------------------------------------------------------------------------------------------
with tab_visits:
    if visits.empty:
        st.info("Abhi koi visit report nahi hai. SBI CSP Visit App ki report (\"Visit report ... .xlsx\") Downloads mein "
                "daal ke 'Update Report.bat' chalaiye.")
    else:
        once = visits[visits["Duplicate Row"] == "No"]
        u, zero = once[once["Visit Done"] != "No"], once[once["Visit Done"] == "No"]  # score 0 = not completed
        vmin, vmax = min(u["Visit Date"]), max(u["Visit Date"])
        files = sorted({Path(x).name for x in visits["Source File"]})
        st.markdown(
            f"<div class='today quiet'><div class='today-h'>📅 SBI CSP Visit App report: {vmin:%d %b} – {vmax:%d %b %Y}</div>"
            f"<div class='small-note'>Latest visit date in the report: <b>{vmax:%A, %d %b %Y}</b> · "
            f"{len(files)} report file(s): {html.escape(', '.join(files))} · har CSP ek hi baar gina gaya "
            f"(2 din visit hui toh bhi 1) · score 0 = visit poori nahi hui, alag dikhaya</div></div>",
            unsafe_allow_html=True)
        vdates = st.date_input("Visit date", (vmin, vmax), min_value=vmin, max_value=vmax, key="visit_dates",
                               help="Show only visits made between these dates")
        if isinstance(vdates, (list, tuple)) and len(vdates) == 2:
            u = u[(u["Visit Date"] >= vdates[0]) & (u["Visit Date"] <= vdates[1])]
            zero = zero[(zero["Visit Date"] >= vdates[0]) & (zero["Visit Date"] <= vdates[1])]
        # each CSP once (its latest completed visit); score 0 only for CSPs with no completed visit at all
        u_all = u  # every completed visit once: a DC's own work (a CSP visited by two DCs counts for both)
        u = u.sort_values("Visit Date").drop_duplicates("CSP Code", keep="last")
        zero = (zero[~zero["CSP Code"].isin(set(once.loc[once["Visit Done"] != "No", "CSP Code"]))]
                .sort_values("Visit Date").drop_duplicates("CSP Code", keep="last"))
        if u.empty:
            st.info("In tarikhon mein koi app visit nahi hai.")
            st.stop()
        period = f"{min(u['Visit Date']):%d %b} – {max(u['Visit Date']):%d %b %Y}"
        dup_rows = 0
        low = u[u["Visit Score"] < LOW_SCORE]
        card_row([
            (card("Visits done", len(u), f"CSPs · {period}", "", "📲"), "kpi_v_all", f"📲 Visits done – {len(u)} CSPs", u, "visits"),
            (card("Score 0 (not done)", len(zero), "app mein aayi, poori nahi hui", "red", "⛔"),
             "kpi_v_zero", f"⛔ Score 0 visits – {len(zero)} (counted nahi)", zero, "visits"),
            (card("DCs (app users)", u["DC User"].nunique(), "made these visits", "", "👥"),
             "kpi_v_dc", f"👥 DCs – {u['DC User'].nunique()}", u, "dc"),
            (card("Avg visit score", f"{u['Visit Score'].mean():.0f}", "out of 100", "orange", "⭐"),
             "kpi_v_score", "⭐ All visits by score (lowest first)", u.sort_values("Visit Score"), "visits"),
            (card(f"Low score (<{LOW_SCORE})", len(low), "need a look", "orange", "⚠️"),
             "kpi_v_low", f"⚠️ Low-score visits – {len(low)}", low.sort_values("Visit Score"), "visits"),
        ])
        if dup_rows:
            st.caption(f"The report had {dup_rows} repeated row(s); each visit is counted once.")

        with st.container(border=True):
            box_title("📅 Date-wise app visits", "Left: har din ka total · tarikh pe click karo, right mein us din ki visits")
            daily = (u.groupby("Visit Date")
                     .agg(Visits=("CSP Code", "count"), DCs=("DC User", "nunique"),
                          Avg_score=("Visit Score", "mean"), Low=("Visit Score", lambda x: int((x < LOW_SCORE).sum())))
                     .sort_index(ascending=False).reset_index())
            dl, dr = st.columns([5, 7], gap="medium")
            with dl:
                day_pick = st.dataframe(
                    daily.rename(columns={"Visit Date": "Date", "Avg_score": "Avg score", "Low": f"< {LOW_SCORE}"}),
                    hide_index=True, width="stretch", height=420, key="vday_table", on_select="rerun",
                    selection_mode="single-row",
                    column_config={"Date": st.column_config.DateColumn(format="ddd, DD MMM"),
                                   "Avg score": st.column_config.NumberColumn(format="%.0f")})
            rows = day_pick.selection.rows
            day = daily.iloc[rows[0] if rows else 0]["Visit Date"]
            with dr:
                dv = u[u["Visit Date"] == day]
                st.markdown(f"<div class='box-title' style='font-size:1rem'>{day:%A, %d %b %Y} · {len(dv)} visit</div>"
                            "<div class='small-note'>👇 CSP pe click karo, uski poori history khulegi</div>",
                            unsafe_allow_html=True)
                vv = visit_view(dv)[["CSP Code", "Circle", "DC User", "DC Name", "Visit Score"]]
                csp_pick = st.dataframe(vv, hide_index=True, width="stretch", height=380, key="vday_visits",
                                        on_select="rerun", selection_mode="single-row",
                                        column_config={"Visit Score": st.column_config.ProgressColumn(
                                            min_value=0, max_value=100, format="%d")})
                if csp_pick.selection.rows:
                    csp_history(str(vv.iloc[csp_pick.selection.rows[0]]["CSP Code"]))

        with st.container(border=True):
            box_title("🔗 Complaint wale CSPs – kya baad mein app se visit hua?",
                      "Matches each complaint's CSP ID with the app's visit report")
            cmp_ = f[f["CSP"] != NOT_MENTIONED].drop_duplicates("CSP")
            in_period = cmp_[cmp_["Message Date"] <= max(u["Visit Date"])]
            done = in_period[in_period["App Visit"].notna()]
            not_done = in_period[in_period["App Visit"].isna()]
            st.markdown(
                f"<div class='split'><div class='yes'>✅ <b>App visit hua</b><div class='big' style='color:{FOREST}'>"
                f"{len(done)}</div><div class='small-note'>CSPs visited in the app after the complaint</div></div>"
                f"<div class='no'>❌ <b>App visit nahi dikha</b><div class='big' style='color:{RED}'>{len(not_done)}</div>"
                f"<div class='small-note'>not in the app report till {max(u['Visit Date']):%d %b} – likely still failing</div>"
                f"</div></div>", unsafe_allow_html=True)
            link = pd.concat([not_done, done]).assign(
                Visit=lambda d: d["App Visit"].map(lambda x: f"✅ {x:%d %b}" if pd.notna(x) else "❌ Not in app"))
            st.dataframe(pd.DataFrame({
                "CSP": link["CSP"], "Complaint date": link["Message Date"], "Problem": link["Problem"],
                "Reported by": link["Reported By"], "App visit": link["Visit"], "Priority": link["Priority"],
            }), hide_index=True, width="stretch", height=min(380, 38 + 35 * len(link)),
                column_config={"Complaint date": DATE_COL})

        c1, c2, c3 = st.columns(3, gap="medium")
        with c1, st.container(border=True):
            box_title("🏢 Circle-wise visits", "SBI circle of the CSP")
            drill(u, u["Circle"], "blue", view="visits", key="vcircle")
        with c2, st.container(border=True):
            box_title("👤 DC-wise visits", "Har DC ki apni poori visits (score > 0) · naam: config/dc_users.csv")
            dc_label = u_all["DC User"] + u_all["DC Name"].map(lambda n: f" · {n}" if n != NA else " · naam nahi mila")
            drill(u_all, dc_label, "orange", view="visits", limit=20, key="vdc")
        with c3, st.container(border=True):
            box_title("⭐ Visit score", "Visits in each score band")
            bands = pd.cut(u["Visit Score"], [-1, 49, LOW_SCORE - 1, 89, 100],
                           labels=["0–49", f"50–{LOW_SCORE - 1}", f"{LOW_SCORE}–89", "90–100"])
            drill(u, bands.astype(str), "green", view="visits", key="vband",
                  order=["90–100", f"{LOW_SCORE}–89", f"50–{LOW_SCORE - 1}", "0–49"])
            if len(low):
                st.caption(f"Low-score visits (<{LOW_SCORE}): " + ", ".join(sorted(low["CSP Code"].astype(str))[:12])
                           + (" …" if len(low) > 12 else ""))

        with st.container(border=True):
            box_title("📋 Saare app visits", "From the SBI CSP Visit App report")
            vq = st.text_input("Search visits", placeholder="🔍 CSP code, circle, DC user…", label_visibility="collapsed")
            vt = visits if not vq else visits[visits.astype(str).apply(
                lambda c: c.str.contains(vq, case=False, regex=False)).any(axis=1)]
            vview = vt[["CSP Code", "Visit Date", "Circle", "Branch Code", "DC User", "DC Name", "Visit Score",
                        "Visit Done", "Compliance Completed", "Closures Completed", "Duplicate Row"]]
            st.dataframe(vview, hide_index=True, width="stretch", height=420,
                         column_config={"Visit Date": st.column_config.DateColumn(format="DD MMM YYYY"),
                                        "Visit Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                                       format="%d")})
            st.download_button("⬇ Visits Excel", to_excel(vview, "App Visits"), "csp_app_visits.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --- CSP Locations ----------------------------------------------------------------------------------------------
LOCATION_VIEW = ["CSP Code", "CSP Name", "Number", "Latitude", "Longitude", "Map", "Received (IST)", "Complaints",
                 "Pending Complaints", "Last App Visit", "Location Check", "Reply"]
LOCATION_COLS = {"Map": st.column_config.LinkColumn("Map", display_text="📍 Open"),
                 "Latitude": st.column_config.NumberColumn(format="%.6f"),
                 "Longitude": st.column_config.NumberColumn(format="%.6f")}

with tab_locations:
    if locations.empty:
        st.info("Abhi CSP location list nahi hai. Left side '📤 Naya data daalo' mein CSP location wali Excel / CSV daaliye "
                "(columns: CSP Code, Latitude, Longitude).")
    else:
        st.markdown(
            "<div class='today quiet'><div class='today-h'>📍 CSPs ne apni location (lat, long) bheji</div>"
            "<div class='small-note'>Har CSP ek baar (latest) · complaint aur SBI app visit ke saath jodi gayi · "
            "📍 Open se Google Maps khulega · phone numbers hidden</div></div>", unsafe_allow_html=True)
        check = locations[locations["Location Check"] != "OK"]
        with_complaint = locations[locations["Complaints"] > 0]
        visited = locations[locations["Last App Visit"] != ""]
        card_row([
            (card("CSPs with location", len(locations), "lat / long mili", "", "📍"),
             "kpi_l_all", f"📍 CSP locations – {len(locations)}", location_view(locations), "locations"),
            (card("Complaint wale", len(with_complaint), "in CSPs ki complaint hai", "orange", "📨"),
             "kpi_l_cmp", f"📨 Complaint wale CSPs – {len(with_complaint)}", location_view(with_complaint), "locations"),
            (card("App visit hui", len(visited), "SBI app mein visit dikhi", "green", "✅"),
             "kpi_l_vis", f"✅ App visit hui – {len(visited)}", location_view(visited), "locations"),
            (card("Check karo", len(check), "location India ke bahar / missing", "red", "⚠️"),
             "kpi_l_chk", f"⚠️ Location check – {len(check)}", location_view(check), "locations"),
        ])
        with st.container(border=True):
            box_title("📋 Saari CSP locations", "Search: CSP code, naam ya jagah")
            lq = st.text_input("Search locations", placeholder="🔍 CSP code, naam…", label_visibility="collapsed")
            lt = locations if not lq else locations[locations.astype(str).apply(
                lambda c: c.str.contains(lq, case=False, regex=False)).any(axis=1)]
            st.dataframe(location_view(lt), hide_index=True, width="stretch", height=min(620, 40 + 35 * len(lt)),
                         column_config=LOCATION_COLS)
            st.download_button("⬇ Locations Excel", to_excel(location_view(lt).drop(columns="Map"), "CSP Locations"),
                               "csp_locations.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --- Agent ------------------------------------------------------------------------------------------------------
with tab_agent:
    if not brief:
        st.info("Agent brief abhi nahi bana. 'Update Report.bat' chalaiye ya 10:00 / 15:00 ke run ka intezaar kijiye.")
    else:
        c = brief["counts"]
        st.markdown(
            f"<div class='today quiet'><div class='today-h'>🤖 Agent ne {brief['generated_at'].replace('T', ' ')[:16]} "
            f"pe kaam kiya</div><div class='small-note'>WhatsApp aur app visit report padhi, complaints update ki, "
            f"jo band ho chuki thi unhe band kiya, aur kisko kya bhejna hai uske messages taiyaar kiye. "
            f"<b>Agent khud kuch nahi bhejta</b> – messages check karke aap bhejiye.</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='cards'>" + "".join([
            card("Auto-closed", c["auto_closed"], "app visit ho gaya, isliye band", "green", "🤖"),
            card("Band hui", c["closed_since_last_run"], "pichhli update ke baad", "green", "✅"),
            card("Nayi complaints", c["new"], "pichhli update ke baad", "orange", "🆕"),
            card("Ab escalate", c["newly_escalate"], "abhi-abhi 5 din paar", "red", "🚨"),
            card("Messages ready", c["drafts"], "copy karke bhejo", "purple", "📨"),
        ]) + "</div>", unsafe_allow_html=True)

        def brief_table(rows: list[dict], empty: str) -> None:
            if not rows:
                st.markdown(f"<div class='small-note'>{empty}</div>", unsafe_allow_html=True)
                return
            t = pd.DataFrame(rows).rename(columns={"id": "No.", "date": "Date", "csp": "CSP", "problem": "Problem",
                                                   "by": "Reported by", "evidence": "Why / evidence"})
            st.dataframe(t, hide_index=True, width="stretch", height=min(320, 40 + 35 * len(t)),
                         column_config={"Why / evidence": st.column_config.TextColumn(width="large")})

        a1, a2 = st.columns(2, gap="medium")
        with a1, st.container(border=True):
            box_title("🤖 Agent ne khud band kiya", "SBI CSP Visit App mein complaint ke baad visit dikha")
            brief_table(brief["auto_closed"], "Abhi koi complaint auto-close nahi hui.")
        with a2, st.container(border=True):
            box_title("🆕 Nayi complaints", "Pichhli update ke baad judi")
            brief_table(brief["new"], "Pichhli update ke baad koi nayi complaint nahi aayi.")

        with st.container(border=True):
            box_title("📨 Bhejne ke liye taiyaar messages",
                      "Har message ke kone mein copy button hai – WhatsApp mein paste karke bhejiye")
            for d in brief["drafts"]:
                st.markdown(f"<div style='margin-top:10px'><b>To {html.escape(d['to'])}</b> "
                            f"<span class='small-note'>· {html.escape(d['why'])}</span></div>", unsafe_allow_html=True)
                st.code(d["message"], language=None, wrap_lines=True)

# --- Photos ----------------------------------------------------------------------------------------------------
with tab_photos:
    with_photo = f[f["Evidence Paths"] != ""]
    if with_photo.empty:
        st.info("In complaints ke saath photos nahi hain. WhatsApp export 'Include media' ke saath karein to yahan dikhengi.")
    else:
        options = (with_photo["Record ID"] + " · " + with_photo["CSP"] + " · " + with_photo["Problem"]).tolist()
        rec = with_photo.iloc[options.index(st.selectbox("Complaint chuno", options))]
        c1, c2 = st.columns([1, 1.2], gap="large")
        with c1:
            for rel in rec["Evidence Paths"].split("; "):
                p = evidence_file(chosen, rel)
                if p:
                    st.image(str(p), caption=Path(rel).name, width="stretch")
                else:
                    st.warning(f"Photo not found: {Path(rel).name}")
        with c2, st.container(border=True):
            dist = rec["Distance Mismatch (metres)"]
            rows = [("Priority", rec["Priority"]), ("Date", f"{rec['Message Date']:%d %b %Y}, {rec['Message Time'][:5]}"),
                    ("Chat", rec["Group/Chat"]), ("Reported by", rec["Reported By"]),
                    ("CSP", f"{rec['CSP']} – {rec['CSP Name'].replace(NA, 'name not mentioned')}"),
                    ("Problem", rec["Problem"]), ("Distance", "not mentioned" if pd.isna(dist) else f"{dist:,.0f} m"),
                    ("Status", rec["Status"]), ("What to do", rec["Suggested Action"])]
            st.markdown("".join(f"<div class='row'><div class='k'>{k}</div><div class='v'>{html.escape(str(v))}</div></div>"
                                for k, v in rows), unsafe_allow_html=True)
            st.markdown("**Message**")
            st.info(rec["Issue Description"])

with st.expander("ℹ About this data"):
    st.write(f"WhatsApp messages read: {summary.get('messages_parsed', 0)} · App visit rows: {summary.get('visit_rows', 0)} · "
             f"Files that could not be read: {len(summary.get('files_failed', []))}")
    for fail in summary.get("files_failed", []):
        st.write(f"⚠ {fail['file']}: {fail['error']}")
    st.write("Phone numbers are hidden. All data stays on this computer. The Excel report has every detail.")
