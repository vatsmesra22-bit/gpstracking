"""Rule-based relevance, categorisation and field extraction (English, Hinglish, Hindi).

Every extracted value records where it came from ("Message" or "Screenshot OCR") so the
report can separate observed facts from inferred classifications. Nothing is guessed:
fields without explicit evidence stay "Not Available".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import NOT_AVAILABLE

# --- relevance ---------------------------------------------------------------------------------
LOCATION_TERMS = re.compile(
    r"\bgps\b|\blocation\b|\blocatio?n\b|\bloc\b|\blat(?:itude)?\b|\blong(?:itude)?\b|\bco-?ordinates?\b|"
    r"\bgeo\s?(?:tag|fenc\w*|location)\b|\bradius\b|\bdistance\b|"
    r"\d\s*(?:m|mtrs?|meters?|metres?|km|kms)\s+(?:away|door|dur|far)\b|\baway from\b|\bout of range\b|"
    r"लोकेशन|लोकेसन|जीपीएस|दूरी|अक्षांश|देशांतर|स्थान", re.I)
# Words that point to GPS/geo-fencing on their own. A bare "location" (e.g. "Location. Siswa awwal" in a form,
# or a photo caption "CSP location") only counts together with a problem word.
STRONG_LOCATION = re.compile(r"\bgps\b|\blat(?:itude)?\b|\blong(?:itude)?\b|\bco-?ordinates\b|\bgeo\s?(?:tag|fenc\w*)\b|"
                             r"\bradius\b|\bdistance\b|\d\s*(?:m|mtrs?|meters?|metres?|km|kms|miters?)\s+(?:away|door|dur|far)\b|"
                             r"\bout of range\b|जीपीएस|दूरी|अक्षांश|देशांतर", re.I)
VISIT_TERMS = re.compile(r"\bvisit\w*\b|विजिट", re.I)
ERROR_TERMS = re.compile(r"\berror\b|\bserver\b|\bcrash\w*\b|\bnot working\b|\bnahi chal\w*\b|\bband ho\w*\b|"
                         r"\b(?:nahi|nhi|not)\s+(?:open|khul)\w*\b|"
                         r"\btime\s?out\b|\bfailed\b|\bfailure\b|\bsomething went wrong\b|\bunable\b|त्रुटि", re.I)

# --- categories (first match in this order becomes the primary Issue Type) -----------------------
CATEGORIES: list[tuple[str, re.Pattern]] = [
    ("Distance Exceeds Radius", re.compile(
        r"\d[\d,.]*\s*(?:m|mtrs?|meters?|metres?|km|kms)\b\s*(?:away|door|dur|far|se\s+(?:door|dur|zyada|jyada))|"
        r"\bradius\b|\bout of range\b|\boutside\b.*\b(?:range|radius|area)\b|\bdistance\b|\bdoor\s+(?:hai|bata)|"
        r"\bdur\s+(?:hai|bata)|दूरी|मीटर", re.I)),
    ("Location Mismatch (Actual vs Planned)", re.compile(
        r"\bmismatch\b|\bnot match\w*\b|\bmatch nahi\b|\bmatch nhi\b|\bdifferent location\b|\bwrong location\b|"
        r"\blocation (?:galat|alag|different|wrong)\b|\b(?:galat|alag) location\b|\bplanned location\b|"
        r"\bactual location\b|\bcsp location\b.*\b(?:differ|alag|galat)|\blocation\s+(?:is\s+)?(?:not\s+correct|incorrect)\b|"
        r"मेल नहीं|गलत लोकेशन", re.I)),
    ("Location Error in Visit App", re.compile(
        r"\blocation\s+(?:visit\s+)?(?:error|issue|problem|prob)\b|\bvisit\s+location\s+(?:error|issue)\b|"
        r"\blocation\s+ki\s+(?:dikkat|problem)\b", re.I)),
    ("GPS Not Detected", re.compile(
        r"\bgps\b.{0,25}\b(?:not|nahi|nhi|na|off|issue|problem|fail\w*|weak|signal)\b|\b(?:no|weak)\s+gps\b|"
        r"\blocation\b.{0,25}\b(?:not (?:detect\w*|found|fetch\w*|available|coming|showing)|nahi a|nhi a|"
        r"nahi aa|nhi aa|nahi le|nhi le|nahi mil|nhi mil|fetch\w* nahi)|\bunable to (?:fetch|get|detect) (?:the )?location\b|"
        r"\bfetching location\b|लोकेशन नहीं", re.I)),
    ("CSP Location Needs Update", re.compile(
        r"\bupdate\s+(?:the\s+|csp\s+)?(?:location|loc|lat\s*long|lat|coordinates?)\b|\blocation\s+(?:update|add)\b|"
        r"\b(?:location|lat\s*long)\s+(?:add|update)\s+karna\b|\bupdate\s+(?:correct|exact)\s+coordinates?\b|"
        r"\bgps camera\b", re.I)),
    ("Lat/Long Incorrect or Unavailable", re.compile(
        r"\blat(?:itude)?\b|\blong(?:itude)?\b|\bco-?ordinates\b|\blat\s*/\s*long\b|अक्षांश|देशांतर", re.I)),
    ("Location Refresh/Update Failure", re.compile(
        r"\brefresh\w*\b|\bupdate location\b|\blocation update\w*\b|\bupdate nahi\b|\bupdate nhi\b|\bnot updat\w*\b|"
        r"\bunable to update\b", re.I)),
    ("Visit Not Completed/Updated", re.compile(
        r"\bvisit\b.{0,30}\b(?:not|nahi|nhi|na|fail\w*|pending|unable|incomplete|cannot|can't|stuck)\b|"
        r"\b(?:unable|cannot|can't|not able) to (?:do|complete|submit|mark|update) (?:the )?visit\b|"
        r"\boffline visit\b|\(\s*of+line\b|\bvisit\b.{0,20}\bof+line\b|\bsubmit nahi\b|\bsubmit nhi\b|विजिट नहीं", re.I)),
    ("CSP Not in Visit App", re.compile(r"\bnot\s+map\w*\s+in\s+(?:the\s+)?(?:csp\s+)?visit\s+app\b|\bmap\s+nahi\b|"
                                        r"\bnot\s+(?:showing|visible)\s+in\s+(?:the\s+)?(?:csp\s+)?visit\s+app\b|"
                                        r"\bcan'?t\s+show\s+csp\s+id\s+in\s+app\b|\bmapping\s+issue\b|\bre-?mapping\b|"
                                        r"\blocation\s+map\b", re.I)),
    ("App/Server Error", ERROR_TERMS),
]
# The DC wanted to do the visit through the app and could not ("not map in visit app", "App nahi open ho raha").
APP_VISIT_FAILED = re.compile(r"\bnot\s+map\w*\s+in\s+(?:the\s+)?(?:csp\s+)?visit\s+app\b|\bcan'?t\s+show\s+csp\s+id\s+in\s+app\b|"
                              r"\bmapping\s+issue\b|\bre-?mapping\b|\blocation\s+map\b|"
                              r"\b(?:mobile\s+|visit\s+)?app\b.{0,20}\b(?:not\s+working|not\s+open\w*|(?:nahi|nhi)\s+(?:open|khul|chal)\w*)", re.I)
OTHER = "Other Location Issue"

# --- field patterns --------------------------------------------------------------------------
DISTANCE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(km|kms|kilomet\w*|m|mtrs?|meters?|metres?|"
                      r"miters?|mitars?|मीटर)\b(?P<tail>.{0,40})", re.I)
# Eko CSP codes look like 1A850339 and are usually written without a label.
CSP_CODE = re.compile(r"(?<![A-Za-z0-9])(\d[A-Z]\d{6})(?![A-Za-z0-9])")
CSP_ID = re.compile(r"\b(?:csp|bc|kiosk|agent|outlet)\s*(?:id|code|no\.?|number|#)\s*[:#\-=.]?\s*([A-Z0-9]*\d[A-Z0-9\-/]{3,19})\b"
                    r"|\b(?:csp|kiosk)\s*[:#\-]\s*([A-Z]*\d[A-Z0-9\-/]{3,19})\b"
                    r"|(?<![A-Za-z0-9])(\d[A-Z]\d{6})(?![A-Za-z0-9])"
                    r"|\bcode\s*[:\-=.]\s*-?\s*([A-Z0-9]*\d[A-Z0-9]{5,9})\b", re.I)
CSP_NAME = re.compile(r"\b(?:csp|kiosk|bc|agent)\s*name\s*[:\-=]?\s*([^\n,;|]{2,60})", re.I)
LABELLED = {
    "State": re.compile(r"\bstate\s*[:\-=]\s*([^\n,;|]{2,40})", re.I),
    "District": re.compile(r"(?:\b(?:district|distt?|zila)\b\.?|जिला)\s*[:\-=]\s*([A-Za-zऀ-ॿ][^\n,;|:]{1,40})", re.I),
    "Village/Branch": re.compile(r"\b(?:village|vill|gram|gaon|branch|br\.?|block|गांव|शाखा)\s*(?:name)?\s*[:\-=]\s*([^\n,;|]{2,50})", re.I),
}
COORD = re.compile(r"(-?\d{1,2}\.\d{3,})\s*[,/ ]\s*(-?\d{2,3}\.\d{3,})")
ACTUAL_LABEL = re.compile(r"\b(?:current|your|actual|my|device|present)\s*(?:location|loc|position|lat)|\bcurrent\b|वर्तमान", re.I)
PLANNED_LABEL = re.compile(r"\b(?:planned|plan|csp|registered|outlet|kiosk|target|branch|shop|expected)\s*(?:location|loc|position|lat)|\bplanned\b", re.I)
VISIT_FAILED = re.compile(r"\bvisit\b.{0,30}\b(?:not (?:complet\w*|done|submit\w*|updat\w*)|nahi ho|nhi ho|fail\w*|pending|"
                          r"incomplete|stuck)\b|\b(?:unable|cannot|can't|not able) to (?:do|complete|submit|mark|update) (?:the )?visit\b|"
                          r"\boffline visit\b|\(\s*of+line\b", re.I)
ACKNOWLEDGED = re.compile(r"^\s*(?:ok+|okay|ok sir|ji|ji sir|noted|done|checking|check kar\w*|dekh\w*|let me check)\b", re.I)
VISIT_DONE = re.compile(r"\bvisit\b.{0,20}\b(?:complet\w*|done|ho gaya|ho gya|submitted|successful\w*)\b", re.I)
ESCALATE = re.compile(r"\bescalat\w*\b|\bhigher authority\b|\bIT team\b.*\b(?:raise|forward)", re.I)
RESOLVED = re.compile(r"\b(?:resolved|fixed|solved|ho gaya|ho gya|ho gyi|ho gayi|theek ho|thik ho|working now|"
                      r"now working|done now|issue closed|chal raha hai)\b|हो गया|ठीक हो", re.I)
ROLE_HINTS = [
    (re.compile(r"\bD\.?C\.?\b|district coordinator", re.I), "District Coordinator"),
    (re.compile(r"\bF\.?O\.?\b|field officer|field exec\w*|\bFE\b", re.I), "Field Officer"),
    (re.compile(r"\bB\.?M\.?\b|branch manager", re.I), "Branch Manager"),
    (re.compile(r"\bRM\b|regional manager|\bASM\b|\bTL\b|team lead", re.I), "Manager"),
    (re.compile(r"\bCSP\b|\bBC\b", re.I), "CSP Operator"),
]
PHONE = re.compile(r"(?<!\d)(\+?91[\s\-]?)?([6-9]\d{2})[\s\-]?(\d{2})[\s\-]?(\d{2})[\s\-]?(\d{3})(?!\d)")


AADHAAR = re.compile(r"(?<![\d.])\d{4}[ \-]?\d{4}[ \-]?\d{4}(?![\d.])")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
LONG_NUMBER = re.compile(r"(?<![\d.])\d{9,18}(?![\d.])")   # bank account numbers and similar IDs
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9-]+\.[A-Za-z0-9.-]+)\b")


def mask_phone(text: str) -> str:
    """Hide personal numbers before anything is written out.

    Phone +91 98765 43210 -> +91 98XXX XX210 (enough to tell numbers apart, not to dial them).
    Aadhaar, PAN, bank account numbers and e-mail addresses that DCs paste from CSP forms are masked too.
    """
    def repl(m: re.Match) -> str:
        prefix = (m.group(1) or "").strip()
        return f"{prefix + ' ' if prefix else ''}{m.group(2)[:2]}XXX XX{m.group(5)}"
    text = PHONE.sub(repl, text or "")
    text = AADHAAR.sub(lambda m: "XXXX XXXX " + re.sub(r"\D", "", m.group())[-4:], text)
    text = PAN.sub(lambda m: "XXXXXXXXX" + m.group()[-1], text)
    text = LONG_NUMBER.sub(lambda m: "X" * (len(m.group()) - 4) + m.group()[-4:], text)
    return EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


@dataclass
class Extraction:
    relevant: bool
    categories: list[str] = field(default_factory=list)
    issue_type: str = OTHER
    csp_id: str = NOT_AVAILABLE
    csp_name: str = NOT_AVAILABLE
    state: str = NOT_AVAILABLE
    district: str = NOT_AVAILABLE
    village_branch: str = NOT_AVAILABLE
    actual_location: str = NOT_AVAILABLE
    planned_location: str = NOT_AVAILABLE
    other_coordinates: list[str] = field(default_factory=list)
    distance_m: float | None = None
    distance_text: str = ""
    visit_status: str = NOT_AVAILABLE
    sources: dict[str, str] = field(default_factory=dict)   # field -> "Message" | "Screenshot OCR"


INSTRUCTION = re.compile(r"^\s*(?:please|pls|plz|kindly|sabhi|all\s+(?:dc|team|members)|dear\s+(?:all|team))\b"
                         r".{0,120}\b(?:share|send|bhej\w*|submit|mention|ensure)\b", re.I | re.S)


# "Leaving location for CSP visit ..." / "Live location ..." are DCs' travel check-ins, not complaints.
CHECKIN = re.compile(r"\b(?:leaving|live|current|camp|reached|reaching|my)\s+location\b", re.I)
PROBLEM_WORDS = re.compile(r"\b(?:issue|problem|not|nahi|nhi|unable|error|update|galat|wrong|mismatch|away|distance|"
                           r"radius|fail\w*|offline|map|mapping|remapping)\b|\blocation\s+add\b|\badd\s+location\b|नहीं", re.I)
MENTION = re.compile(r"@[~\w]")
# Office notices, @all instructions and app adverts mention location but report no problem.
NOTICE = re.compile(r"\bregarding the location issue\b|\bplease ensure\b|\bkindly ensure\b|\bwithout fail\b|\blatest by\b|"
                    r"@all\b|\bplease refer\b|\bkindly refer\b|\bvia mail\b|play\.google\.com|https?://", re.I)


# CSP onboarding / replacement forms ("Aadhaar Number ... PAN Number ... Latitude ...") carry coordinates but
# report no visit problem.
FORM_FIELDS = re.compile(r"\b(?:aadhaar\s+number|pan\s+number|date\s+of\s+birth|commission\s+a/?c|national\s+bc\s+(?:code|name)|"
                         r"code\s+of\s+replacement|gram\s+panchayat|population\s+category|vtc\s+code|pin\s+code)\b", re.I)


def is_relevant(text: str) -> bool:
    if len(set(m.lower() for m in FORM_FIELDS.findall(text))) >= 3:
        return False
    if INSTRUCTION.search(text) and not DISTANCE.search(text) and not COORD.search(text):
        return False  # team instructions ("please share CSP ID with location issues") are not complaints
    if NOTICE.search(text) and not CSP_CODE.search(text):
        return False
    if (CHECKIN.search(text) and not PROBLEM_WORDS.search(CHECKIN.sub("", text))
            and not COORD.search(text) and not DISTANCE.search(text)):
        return False
    if RESOLVED.search(text) and not re.search(r"\b(?:still|abhi bhi|phir se|again|not|nahi|nhi)\b", text, re.I):
        return False  # "location issue resolved, visit ho gaya" closes an issue, it is not a new one
    if len(MENTION.findall(text)) >= 3 and not CSP_CODE.search(text):
        return False  # a broadcast to many people ("@A @B @C share your offline visit photos") is an instruction
    if VISIT_FAILED.search(text) or STRONG_LOCATION.search(text) or COORD.search(text):
        return True
    if APP_VISIT_FAILED.search(text) and (VISIT_TERMS.search(text) or CSP_CODE.search(text)):
        return True
    if LOCATION_TERMS.search(text):
        return bool(PROBLEM_WORDS.search(text))
    # App/server errors count only when tied to a CSP visit, otherwise they are unrelated chatter.
    return bool(ERROR_TERMS.search(text) and VISIT_TERMS.search(text))


def _to_metres(value: str, unit: str) -> float:
    v = float(value.replace(",", ""))
    return v * 1000 if unit.lower().startswith("k") else v


def _first(pattern: re.Pattern, *texts: tuple[str, str]) -> tuple[str, str] | None:
    for source, text in texts:
        m = pattern.search(text)
        if m:
            value = next(g for g in m.groups() if g)
            return re.sub(r"\s+", " ", value).strip(" .-:"), source
    return None


def _distance(texts: list[tuple[str, str]]) -> tuple[float, str, str] | None:
    best = None
    for source, text in texts:
        for m in DISTANCE.finditer(text):
            metres = _to_metres(m.group(1), m.group(2))
            if not (1 <= metres <= 500_000):
                continue
            tail = m.group("tail").lower()
            near_away = bool(re.search(r"away|door|dur|far|from|se\b|radius|distance|दूर", tail + text[max(0, m.start() - 30):m.start()].lower()))
            score = (near_away, source == "Screenshot OCR")
            if best is None or score > best[0]:
                best = (score, metres, m.group(0).split("\n")[0].strip()[:60], source)
    return (best[1], best[2], best[3]) if best else None


def _locations(texts: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, tuple[str, str] | None, list[str]]:
    actual = planned = None
    unlabelled: list[str] = []
    for source, text in texts:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            for m in COORD.finditer(line):
                lat, lon = float(m.group(1)), float(m.group(2))
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                coord = f"{m.group(1)}, {m.group(2)}"
                context = line[:m.start()] + " " + (lines[i - 1] if i else "")
                if PLANNED_LABEL.search(context) and planned is None:
                    planned = (coord, source)
                elif ACTUAL_LABEL.search(context) and actual is None:
                    actual = (coord, source)
                else:
                    unlabelled.append(coord)
    return actual, planned, unlabelled


def extract(message_text: str, ocr_text: str) -> Extraction:
    texts = [("Message", message_text or ""), ("Screenshot OCR", ocr_text or "")]
    combined = "\n".join(t for _, t in texts)
    ex = Extraction(relevant=is_relevant(combined))
    if not ex.relevant:
        return ex

    ex.categories = [name for name, pat in CATEGORIES if pat.search(combined)]
    if "Visit Not Completed/Updated" in ex.categories and VISIT_DONE.search(combined) and not VISIT_FAILED.search(combined):
        ex.categories.remove("Visit Not Completed/Updated")  # "visit done, mobile app not working" is not a failed visit
    ex.issue_type = ex.categories[0] if ex.categories else OTHER

    if (hit := _first(CSP_ID, *texts)):
        ex.csp_id, ex.sources["CSP ID"] = hit[0].upper(), hit[1]
    if (hit := _first(CSP_NAME, *texts)):
        ex.csp_name, ex.sources["CSP Name"] = hit
    for name, pat in LABELLED.items():
        if (hit := _first(pat, *texts)):
            setattr(ex, name.lower().replace("/", "_"), hit[0].title() if name != "Village/Branch" else hit[0])
            ex.sources[name] = hit[1]

    if (d := _distance(texts)):
        ex.distance_m, ex.distance_text, ex.sources["Distance"] = d
    actual, planned, unlabelled = _locations(texts)
    if actual:
        ex.actual_location, ex.sources["Actual Location"] = actual
    if planned:
        ex.planned_location, ex.sources["Planned Location"] = planned
    ex.other_coordinates = unlabelled

    if VISIT_FAILED.search(combined):
        ex.visit_status, ex.sources["Visit Status"] = "Not Completed / Not Updated", "Message"
    elif VISIT_DONE.search(combined):
        ex.visit_status, ex.sources["Visit Status"] = "Completed", "Message"
    return ex


def infer_role(sender: str, directory: dict[str, dict]) -> str:
    if sender in directory and directory[sender].get("role"):
        return directory[sender]["role"]
    for pat, role in ROLE_HINTS:
        if pat.search(sender):
            return role
    return NOT_AVAILABLE


def is_resolution_reply(text: str) -> bool:
    return bool(RESOLVED.search(text or ""))


def is_escalation(text: str) -> bool:
    return bool(ESCALATE.search(text or ""))


def is_acknowledgement(text: str) -> bool:
    return bool(ACKNOWLEDGED.search(text or "")) and len(text or "") <= 40


def csp_codes(text: str) -> set[str]:
    return {c.upper() for c in CSP_CODE.findall(text or "")}
