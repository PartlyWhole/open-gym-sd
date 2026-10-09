"""Fetch every rec center's open-play schedule and write schedules.json for the site.

Run from the repo root:  python scraper/update.py
Needs: requests beautifulsoup4 pdfplumber icalendar recurring-ical-events, plus
poppler-utils (pdftotext, pdftoppm) and tesseract-ocr for scanned flyers.

Rules: a gym's data is only replaced when its new schedule parses cleanly. Anything
that fails keeps its last good data and is flagged, so the site never shows guesses.
"""
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))
import parse as P  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
SITE = "https://www.sandiego.gov"
PAGE = SITE + "/park-and-recreation/centers/recctr/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "schedules.json")
HEADERS = {"User-Agent": "open-gym-sd schedule reader (github.com/PartlyWhole)"}
LINK_GOOD = re.compile(r"open\s*play|open\s*gym|gym\s*schedule|openplay|gym\.pdf|gymschedule", re.I)
LINK_BAD = re.compile(r"room|mahjong|program\s*guide|movie|camp|class|rental|area\.pdf", re.I)


def get(url, binary=False):
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.content if binary else r.text


# ------------------------------------------------------------- sources
def find_sources(html, base):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    pdfs = []
    for a in main.find_all("a", href=True):
        href, text = a["href"], a.get_text(" ", strip=True)
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        if LINK_BAD.search(href + " " + text):
            continue
        if LINK_GOOD.search(href + " " + text):
            u = urljoin(base, href)
            if u not in pdfs:
                pdfs.append(u)
    cals = []
    for f in soup.find_all("iframe", src=True):
        q = parse_qs(urlparse(f["src"]).query)
        for c in q.get("src", []):
            if "@" not in c:  # newer embeds base64-encode the calendar id
                try:
                    dec = base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)).decode()
                    if "@" in dec:
                        c = dec
                except (ValueError, UnicodeDecodeError):
                    pass
            if c not in cals:
                cals.append(c)
    return main, pdfs, cals


def page_text_sections(main):
    """Text blocks on the center page that start at an open-play heading."""
    lines = [l for l in main.get_text("\n", strip=True).split("\n") if l.strip()]
    lines = [l.replace("​", "").strip() for l in lines]
    blocks, cur = [], None
    for l in lines:
        if re.search(r"(?i)open\s*(play|gym)|drop.?in", l) and len(l) < 60:
            if cur:
                blocks.append(cur)
            cur = [l]
            continue
        if cur is not None:
            if re.search(r"(?i)subject to change|^facilities$", l) or len(cur) > 40:
                blocks.append(cur)
                cur = None
            else:
                cur.append(l)
    if cur:
        blocks.append(cur)
    return blocks


# ------------------------------------------------------------- pdf reading
def pdf_words(path):
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            ws = pg.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
            pages.append([{k: w[k] for k in ("text", "x0", "x1", "top", "bottom")} for w in ws])
    return pages


def pdf_layout_text(path):
    return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True).stdout


def ocr_pages(path):
    """Rasterize and OCR a scanned PDF. Returns [(words, text)] per page."""
    out = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "300", "-png", path, os.path.join(d, "p")], check=True)
        for img in sorted(os.listdir(d)):
            ip = os.path.join(d, img)
            tsv = subprocess.run(["tesseract", ip, "-", "--psm", "11", "tsv"], capture_output=True, text=True).stdout
            words = []
            for row in tsv.splitlines()[1:]:
                c = row.split("\t")
                if len(c) == 12 and c[11].strip() and float(c[10]) > 30:
                    x, y, w, h = map(int, c[6:10])
                    s = 72 / 300
                    words.append({"text": c[11].strip(), "x0": x * s, "x1": (x + w) * s, "top": y * s, "bottom": (y + h) * s})
            text = subprocess.run(["tesseract", ip, "-", "--psm", "4"], capture_output=True, text=True).stdout
            out.append((words, text))
    return out


def parse_pdf(path, today):
    """Returns a result dict, or raises ValueError when nothing reliable was found."""
    pages = pdf_words(path)
    # skip pages about outdoor courts (some flyers add an outdoor schedule page)
    keep = []
    for ws in pages:
        flat = "".join(w["text"] for w in ws).lower()
        if "outdoor" in flat and "indoor" not in flat:
            continue
        keep.append(ws)
    if keep and len(keep) < len(pages):
        pages = keep
    layout = pdf_layout_text(path)
    scanned = sum(len(p) for p in pages) < 25
    texts = [layout]
    if scanned:
        ocr = ocr_pages(path)
        pages = [w for w, _ in ocr]
        texts = [t for _, t in ocr]
    full = "\n".join(texts)
    grids = []
    for words in pages:
        g = P.parse_grid(words, full, today)
        if g:
            grids.append(g)
    if grids:
        # several pages may hold several months; keep each
        months = {}
        for y, m, days in grids:
            months.setdefault((y, m), {}).update(days)
        res = {"kind": "dated", "months": ["%04d-%02d" % k for k in sorted(months)], "days": {}}
        for days in months.values():
            res["days"].update(days)
        res["ocr"] = scanned
        return res
    week = {}
    for words in pages:
        pw = P.parse_weekly_positional(words)
        if pw:
            for k, v in pw.items():
                week.setdefault(k, []).extend(v)
    if not P.score_week(week):
        week = {}
        for t in texts:
            for k, v in P.parse_weekly_layout(t).items():
                week.setdefault(k, []).extend(v)
    week = {k: P._dedupe(v) for k, v in week.items()}
    if P.score_week(week):
        return {"kind": "weekly", "week": week, "ocr": scanned}
    raise ValueError("no open play times found in the posted schedule" + (" (scanned image)" if scanned else ""))


# ------------------------------------------------------------- calendars
def parse_calendars(ids, today):
    import icalendar
    import recurring_ical_events
    start = dt.datetime(today.year, today.month, 1, tzinfo=TZ) - dt.timedelta(days=7)
    end = dt.datetime.combine(today, dt.time(), TZ) + dt.timedelta(days=60)
    days = {}
    n = 0
    for cid in ids:
        url = "https://calendar.google.com/calendar/ical/%s/public/basic.ics" % requests.utils.quote(cid, safe="")
        cal = icalendar.Calendar.from_ical(get(url, binary=True))
        calname = str(cal.get("X-WR-CALNAME", ""))
        for ev in recurring_ical_events.of(cal).between(start, end):
            a, b = ev["DTSTART"].dt, ev["DTEND"].dt
            if not isinstance(a, dt.datetime):
                continue
            a, b = a.astimezone(TZ), b.astimezone(TZ)
            n += 1
            summary = str(ev.get("SUMMARY", ""))
            c = P.classify(summary)
            if not c and not re.sub(r"[\d:\s\-–apmAPM.]", "", summary) and P.OPENISH.search(calname):
                c = ("open", None)  # event titled only with its time on an open-play calendar
            if not c:
                continue
            am, bm = a.hour * 60 + a.minute, b.hour * 60 + b.minute
            if not (6 * 60 <= am < bm <= 23 * 60 + 30):
                continue
            days.setdefault(a.date().isoformat(), []).append(P._sess(c, am, bm))
    if not n:
        raise ValueError("calendar feed was empty")
    d0 = start.date()
    for i in range((end.date() - d0).days):
        days.setdefault((d0 + dt.timedelta(days=i)).isoformat(), [])
    return {"kind": "dated", "through": end.date().isoformat(),
            "days": {k: P._dedupe(v) for k, v in days.items()}}


# ------------------------------------------------------------- one gym
def read_gym(g, today):
    url = PAGE + g["page"]
    html = get(url)
    main, pdfs, cals = find_sources(html, url)
    errors = []
    if cals:
        try:
            r = parse_calendars(cals, today)
            r["source"] = url
            r["source_kind"] = "calendar"
            return r
        except Exception as e:  # fall through to other sources
            errors.append("calendar: %s" % e)
    for pdf in pdfs:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(get(pdf, binary=True))
                path = f.name
            try:
                r = parse_pdf(path, today)
            finally:
                os.unlink(path)
            r["source"] = pdf
            r["source_kind"] = "pdf"
            return r
        except Exception as e:
            errors.append("%s: %s" % (pdf.rsplit("/", 1)[-1], e))
    week = {}
    for block in page_text_sections(main):
        for k, v in P.parse_weekly_lines(block).items():
            week.setdefault(k, []).extend(v)
    if P.score_week(week):
        return {"kind": "weekly", "week": {k: P._dedupe(v) for k, v in week.items()},
                "source": url, "source_kind": "page"}
    if not pdfs and not cals:
        return {"kind": "none", "source": url, "source_kind": "page"}
    raise ValueError("; ".join(errors) or "no schedule found")


def validate(r):
    """Basic sanity checks; raises ValueError if the parse looks broken."""
    if r["kind"] == "dated":
        days = r["days"]
        if r.get("source_kind") == "pdf":
            filled = sum(1 for v in days.values() if v)
            if filled < 4:
                raise ValueError("only %d days had readable open play" % filled)
    elif r["kind"] == "weekly":
        if P.score_week(r["week"]) < 1:
            raise ValueError("no sessions")
    for ss in (r.get("days") or {}).values():
        for s in ss:
            if s["a"] >= s["b"]:
                raise ValueError("bad time range %s-%s" % (s["a"], s["b"]))


# ------------------------------------------------------------- main
def main():
    now = dt.datetime.now(TZ)
    today = now.date()
    only = set(sys.argv[1:])
    meta = json.load(open(os.path.join(ROOT, "scraper", "gyms.json")))
    try:
        prev = json.load(open(OUT))
    except (OSError, ValueError):
        prev = {"gyms": []}
    prev_by = {g["id"]: g for g in prev.get("gyms", [])}
    keep_from = (today - dt.timedelta(days=7)).isoformat()
    out = []
    for g in meta:
        old = prev_by.get(g["id"], {})
        rec = dict(g)
        if only and g["id"] not in only:
            out.append({**rec, **{k: v for k, v in old.items() if k not in g}})
            continue
        try:
            r = read_gym(g, today)
            validate(r)
            rec.update({"kind": r["kind"], "source": r["source"], "source_kind": r["source_kind"],
                        "checked": now.isoformat(timespec="minutes"), "status": "ok"})
            if r["kind"] == "dated":
                days = {k: v for k, v in (old.get("days") or {}).items() if k >= keep_from}
                days.update(r["days"])
                rec["days"] = dict(sorted(days.items()))
                if "months" in r:
                    rec["months"] = r["months"]
                    latest = r["months"][-1]
                    rec["covered_until"] = _month_end(latest)
                else:
                    rec["covered_until"] = r["through"]
                if rec["covered_until"] < today.isoformat():
                    rec["status"] = "stale"
            elif r["kind"] == "weekly":
                rec["week"] = {str(k): v for k, v in sorted(r["week"].items())}
            if r.get("ocr"):
                rec["ocr"] = True
            print("ok    %-14s %s %s" % (g["id"], r["kind"], rec.get("covered_until", "")))
        except Exception as e:
            for k in ("kind", "source", "source_kind", "days", "week", "months", "covered_until", "ocr", "checked"):
                if k in old:
                    rec[k] = old[k]
            rec["status"] = "error"
            rec["error"] = str(e)[:300]
            rec["error_since"] = old.get("error_since") if old.get("status") == "error" else today.isoformat()
            print("FAIL  %-14s %s" % (g["id"], e))
            if os.environ.get("DEBUG"):
                traceback.print_exc()
        if "days" in rec:
            rec["days"] = {k: v for k, v in rec["days"].items() if k >= keep_from}
        out.append(rec)
    data = {"generated": now.isoformat(timespec="minutes"), "gyms": out}
    with open(OUT, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    bad = [g["id"] for g in out if g.get("status") == "error"]
    print("\n%d gyms, %d need attention: %s" % (len(out), len(bad), ", ".join(bad) or "none"))


def _month_end(ym):
    y, m = map(int, ym.split("-"))
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).isoformat()


if __name__ == "__main__":
    main()
