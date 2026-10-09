"""Parsers that turn City of San Diego rec center open-play schedules into sessions.

A session is a dict: {"s": sport code, "a": "HH:MM", "b": "HH:MM", "l": optional label}.
Sport codes: bb basketball, pb pickleball, vb volleyball, bad badminton, pp ping pong, open open gym.

Three source shapes are handled:
  * monthly calendar grids (most PDFs): weekday header row, date-number rows, text in cells
  * weekly lists/posters (PDFs or page text): day names, sport headings, time ranges
  * Google Calendar ICS feeds (handled in update.py, classified here)
Everything that cannot be read with confidence is dropped rather than guessed.
"""
import calendar
import datetime as dt
import re

# ---------------------------------------------------------------- times
_T = r"(\d{1,2}:\d{2}|\d{3,4}|\d{1,2})"
_M = r"(?:\s*([ap])\.?\s*m?\.?(?![a-z]))?"
_DASH = r"\s*(?:-+|–|—|\bto\b)\s*"
RANGE_RE = re.compile(r"(?<![\d/:.])" + _T + _M + _DASH + _T + _M + r"(?![\d/])", re.I)


def _hm(tok):
    if ":" in tok:
        h, m = tok.split(":")
        return int(h), int(m)
    if len(tok) >= 3:
        return int(tok[:-2]), int(tok[-2:])
    return int(tok), 0


def _to24(h, m, mer):
    h = h % 12
    if mer == "p":
        h += 12
    return h * 60 + m


def parse_range(m):
    """Return (start_min, end_min) for a RANGE_RE match, or None if implausible."""
    t1, m1, t2, m2 = m.group(1), m.group(2), m.group(3), m.group(4)
    m1 = m1.lower() if m1 else None
    m2 = m2.lower() if m2 else None
    if not (m1 or m2 or ":" in t1 or ":" in t2):
        return None  # bare "858-552" style numbers
    (h1, n1), (h2, n2) = _hm(t1), _hm(t2)
    if not (1 <= h1 <= 12 and 1 <= h2 <= 12 and n1 < 60 and n2 < 60):
        return None
    if n1 % 5 or n2 % 5:
        return None
    if m2 and not m1:
        a = _to24(h1, n1, m2)
        b = _to24(h2, n2, m2)
        if a >= b and m2 == "p":
            a = _to24(h1, n1, "a")
    elif m1 and not m2:
        a = _to24(h1, n1, m1)
        b = _to24(h2, n2, m1)
        if b <= a:
            b = _to24(h2, n2, "p")
    elif m1 and m2:
        a, b = _to24(h1, n1, m1), _to24(h2, n2, m2)
    else:
        a = _to24(h1, n1, "a" if 7 <= h1 <= 11 else "p")
        b = _to24(h2, n2, "a" if 7 <= h2 <= 11 else "p")
    # common typos: "10:15am-12:45am", "11:15am-1:15am", "1:30am-3:30pm"
    if b <= a and b < 12 * 60:
        b += 12 * 60
    if a < 6 * 60 and a + 12 * 60 < b:
        a += 12 * 60
    if not (6 * 60 <= a < b <= 23 * 60 + 30) or b - a > 12 * 60:
        return None
    return a, b


_GLYPH = re.compile(r"(?<![A-Za-z])([0-9oOiIl]{1,2}:[0-9oOiIl]{2}|[0-9][0-9oOiIl]{1,3}|[iIl](?=[ap]m?\b|:))(?=\s*[apAP-]|:|\s*$|\b)")


def fix_glyphs(text):
    """Undo broken PDF font mappings in times: '2:oop-4:3op' -> 2:00p-4:30p, 'i:oop' -> 1:00p.
    Glyphs whose digit is ambiguous are left alone, so those times are dropped, not guessed."""
    def rep(m):
        t = m.group(1)
        if not re.search(r"\d|:", t) and not re.fullmatch(r"[iIl]", t):
            return t
        t = t.replace("o", "0").replace("O", "0")
        t = re.sub(r"[iIl]", "1", t)
        return t
    return _GLYPH.sub(rep, text)


def fmt(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


# ---------------------------------------------------------------- labels
EXCLUDE = re.compile(
    r"rental|permit|reserved|league|clinic|practice|\bclass|camp\b|karate|martial|academy|"
    r"sharks|\bteam\b|lesson|tournament|closed|no open|\bno\b|program|handb|tryout|assessment|"
    r"\bgames?\b|private|instruction|training|\bevent|rising stars|monstarz|smrc|cancel|"
    r"special olympics|wheelchair|rugby|\bnho\b|school|meeting|movie|staff|holiday",
    re.I)
SPORTS = [
    ("bb", re.compile(r"basket|b-?\s?ball|\bbb\b|hoops", re.I)),
    ("pb", re.compile(r"pickle|pickel|\bpb\b", re.I)),
    ("vb", re.compile(r"volley|\bvb\b", re.I)),
    ("bad", re.compile(r"badminton", re.I)),
    ("pp", re.compile(r"ping\s*-?pong|table tennis", re.I)),
]
OPENISH = re.compile(r"open\s*(gym|play|court)|drop[\s-]*in|pick[\s-]*up|open\b", re.I)
FILLER = re.compile(
    r"\b(open|play|gym|gymnasium|drop|in|pick|up|pickup|adults?|only|youth|family|families|half|"
    r"courts?|east|west|north|south|side|all|the|and|or|for|ages?|co-?ed|men'?s|women'?s|seniors?|"
    r"sessions?|hours?|schedule|basketball|b-?\s?ball|bb|pickleball|pb|volleyball|vb|badminton|"
    r"pickle|pickel|competitive|advanced?|social|beginners?|intermediate|recreational|ping|pong|table|tennis|time|times|dates?|available|indoor|full|of|with|daily|level|"
    r"all-?ages|teens?|kids|juniors?|general|free|ball|[a-z]|\d+)\b|[½&+*():;,./\-–—!#\[\]]",
    re.I)
NOTE_WORDS = re.compile(r"adult|youth|family|families|court|senior|women|men'?s|teen|kids|junior|ages", re.I)


def classify(label):
    """Return (sport, display_label|None) if `label` describes free drop-in play, else None."""
    if not label:
        return None
    text = " ".join(label.split())
    if EXCLUDE.search(text):
        return None
    if re.search(r"(?i)youth|kids|teens?\b|junior|ages? \d", text) and not re.search(r"(?i)adult|all ages|famil", text):
        return None  # youth-only sessions aren't open to adults
    found = [code for code, rx in SPORTS if rx.search(text)]
    openish = bool(OPENISH.search(text))
    leftover = FILLER.sub(" ", text).strip()
    if not found and not openish:
        return None
    if leftover and not openish:
        return None  # e.g. "San Diego Sharks Basketball": a named group, not open play
    if leftover and len(leftover.split()) > 3:
        return None
    if len(set(found)) > 1:
        return ("open", text)  # "Pickleball or badminton": rotating, show the words
    sport = found[0] if found else "open"
    note = None
    if NOTE_WORDS.search(text):
        note = text
        for _, rx in SPORTS:
            note = rx.sub(" ", note)
        note = re.sub(r"(?i)\b(open|play|gym|drop[\s-]*in|pick[\s-]*up|pickup|ball)\b|\b[NSEW](?:/[NSEW])?\b|[()*:]", " ", note)
        note = " ".join(note.split()).strip(" -–—,&/").lower()
        note = note[:1].upper() + note[1:] if note else None
    return (sport, note)


def _is_exclusion(text):
    return bool(text and EXCLUDE.search(text))


COURT_TAG = re.compile(r"(?i)\b(?:all\s+)?courts?\s*(?:[\d/&,]+\s*)?:")


def sessions_from_text(text, inherit=True):
    """Parse one calendar cell. Labels normally precede their times; cells that tag
    each line with a court ("All Courts: 11:30-3pm Pickle Ball") are read chunk by chunk."""
    text = fix_glyphs(" ".join(text.split()))
    if len(COURT_TAG.findall(text)) >= 2 or (COURT_TAG.match(text) and len(RANGE_RE.findall(text)) == 1):
        out = []
        for chunk in COURT_TAG.split(text):
            ms = [m for m in RANGE_RE.finditer(chunk) if parse_range(m)]
            if len(ms) != 1:
                continue
            m = ms[0]
            label = (chunk[:m.start()] + " " + chunk[m.end():]).strip(" :-–—,;*")
            c = classify(label)
            if c:
                a, b = parse_range(m)
                out.append(_sess(c, a, b))
        return out
    out = []
    matches = [m for m in RANGE_RE.finditer(text) if parse_range(m)]
    prev_label = None
    pos = 0
    pending_skip = False
    for i, m in enumerate(matches):
        label = text[pos:m.start()].strip(" :-–—,;*")
        nxt_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        following = text[m.end():nxt_end].strip()
        if not label:
            # "12PM-2PM GYM RENTAL": the label trails the time
            lead = re.match(r"\W*(?:gym\s+|city\s+)?(?:rental|reserved|permit|closed)\b\)?", text[m.end():nxt_end], re.I)
            if lead:
                pos = m.end() + lead.end()
                continue
            label = prev_label if inherit else None
        pos = m.end()
        if label is None:
            continue
        prev_label = label
        c = classify(label)
        if c:
            a, b = parse_range(m)
            out.append(_sess(c, a, b))
    return out


def _sess(c, a, b):
    s = {"s": c[0], "a": fmt(a), "b": fmt(b)}
    if c[1]:
        s["l"] = c[1]
    return s


# ---------------------------------------------------------------- days
DAY_NAMES = {
    0: r"sun(?:day)?s?", 1: r"mon(?:day)?s?", 2: r"tue(?:s|sday)?s?", 3: r"wed(?:nesday)?s?",
    4: r"thu(?:r|rs|rsday)?s?", 5: r"fri(?:day)?s?", 6: r"sat(?:urday)?s?",
}
DAY_TOKEN = re.compile(r"\b(" + "|".join(DAY_NAMES.values()) + r")\.?(?![a-z])", re.I)


def _day_index(tok):
    for k, rx in DAY_NAMES.items():
        if re.fullmatch(rx, tok, re.I):
            return k
    return None


def days_in(text):
    """Day-of-week indexes (0=Sun) mentioned in text, expanding ranges like 'Mon - Fri'."""
    toks = [(m.start(), m.end(), _day_index(m.group(1))) for m in DAY_TOKEN.finditer(text)]
    toks = [t for t in toks if t[2] is not None]
    days = []
    i = 0
    while i < len(toks):
        s, e, d = toks[i]
        if i + 1 < len(toks) and re.fullmatch(r"\s*(?:-|–|—|to|through|thru)\s*", text[e:toks[i + 1][0]], re.I):
            d2 = toks[i + 1][2]
            k = d
            while True:
                days.append(k)
                if k == d2:
                    break
                k = (k + 1) % 7
            i += 2
            continue
        days.append(d)
        i += 1
    return sorted(set(days)), toks


# ---------------------------------------------------------------- weekly lists
def _strip(text, spans):
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + " " + text[e:]
    text = re.sub(r"\b(dates?|times?|days?)\s*:", " ", text, flags=re.I)
    return " ".join(text.split()).strip(" :-–—,;*&|")


def parse_weekly_lines(lines):
    """Weekly schedule from lines of text. Returns {dow: [sessions]}."""
    week = {}
    days, heading = [], None
    emitted_since_days = False
    for raw in lines:
        line = fix_glyphs(" ".join(raw.split()))
        if not line:
            continue
        ranges = [m for m in RANGE_RE.finditer(line) if parse_range(m)]
        d, toks = days_in(line)
        rest = _strip(line, [(m.start(), m.end()) for m in ranges] + [(t[0], t[1]) for t in toks])
        if not ranges:
            if rest and (classify(rest) or _is_exclusion(rest)) and not re.fullmatch(r"(?i)closed", rest):
                heading = rest
                if not d:
                    days = []
            if d:
                if re.search(r"(?i)\bclosed\b", rest):
                    days = []
                else:
                    days = d if emitted_since_days or not days else sorted(set(days) | set(d)) if not rest else d
                    emitted_since_days = False
            continue
        use = d or days
        if d:
            days = d
            emitted_since_days = True
        if rest and _is_exclusion(rest):
            label = rest
        elif rest and SPORTS_IN(rest) and classify(rest):
            label = rest
        elif rest and heading and FILLER.sub(" ", rest).strip() == "":
            label = rest + " " + heading  # "Youth" + "Volleyball", "3 courts" + "Pickleball"
        elif rest and classify(rest) and not heading:
            label = rest
        else:
            label = heading
        c = classify(label) if label else None
        for m in ranges:
            if not c:
                break
            a, b = parse_range(m)
            for k in use:
                week.setdefault(k, []).append(_sess(c, a, b))
        if ranges:
            emitted_since_days = True
    return {k: _dedupe(v) for k, v in week.items()}


def SPORTS_IN(text):
    return any(rx.search(text) for _, rx in SPORTS)


def _dedupe(ss):
    seen, out = set(), []
    for s in sorted(ss, key=lambda x: (x["a"], x["b"], x["s"])):
        k = (s["s"], s["a"], s["b"])
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def split_columns(layout_text, gap=12):
    """Split `pdftotext -layout` output into reading columns; returns list of line lists."""
    segs = []
    for li, line in enumerate(layout_text.splitlines()):
        for m in re.finditer(r"\S+(?: {1,2}\S+)*", line):
            segs.append((m.start(), li, m.group(0)))
    if not segs:
        return []
    # column starts are learned only from lines that visibly hold several columns,
    # so centred titles don't bridge the gap between columns
    per_line = {}
    for x, li, t in segs:
        per_line.setdefault(li, []).append(x)
    starts = sorted(set(x for xs in per_line.values() if len(xs) > 1 for x in xs))
    if not starts:
        return [[t for _, _, t in segs]]
    groups, cur = [], [starts[0]]
    for x in starts[1:]:
        if x - cur[-1] > gap:
            groups.append(cur)
            cur = [x]
        else:
            cur.append(x)
    groups.append(cur)
    centers = [sum(g) / len(g) for g in groups]
    cols = [[] for _ in groups]
    for x, li, t in segs:
        ci = min(range(len(centers)), key=lambda i: abs(centers[i] - x))
        cols[ci].append((li, t))
    return [[t for _, t in sorted(c)] for c in cols]


def score_week(week):
    """Sessions with a definite sport; mixed-up headings ('Basketball Pickleball') don't count."""
    return sum(1 for v in week.values() for s in v
               if not (s["s"] == "open" and s.get("l") and SPORTS_IN(s["l"])))


def parse_weekly_layout(layout_text):
    """Try the page as one stream and as columns; keep whichever reads more sessions."""
    whole = parse_weekly_lines(layout_text.splitlines())
    best = whole
    for g in (12, 20):
        cols = split_columns(layout_text, gap=g)
        if len(cols) > 1:
            w = {}
            for c in cols:
                for k, v in parse_weekly_lines(c).items():
                    w.setdefault(k, []).extend(v)
            w = {k: _dedupe(v) for k, v in w.items()}
            if score_week(w) > score_week(best) or (score_week(w) == score_week(best) and _ambiguous(best)):
                best = w
    return best


def _ambiguous(week):
    return any(s["s"] == "open" and s.get("l") for v in week.values() for s in v)


# ---------------------------------------------------------------- monthly grids
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?\s*(\d{4})?", re.I)
DATE_TOK = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\.?([A-Z][a-z]+)?$")
HEAD_TOK = re.compile(r"^(sun|mon|tue|wed|thu|fri|sat)[a-z]*\.?$", re.I)


def _lines_of(words, ytol=3.0):
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) <= ytol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [(y, sorted(ws, key=lambda w: w["x0"])) for y, ws in lines]


def _merge_letters(words):
    """Join letter-spaced headings ('S U N' -> 'SUN') that PDFs split into single glyphs."""
    out = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        p = out[-1] if out else None
        if (p and len(w["text"]) == 1 and w["text"].isalpha() and p["text"].isalpha() and p.get("_sp")
                and abs(p["top"] - w["top"]) < 2 and 0 <= w["x0"] - p["x1"] < 14):
            p["text"] += w["text"]
            p["x1"] = w["x1"]
            continue
        w = dict(w)
        w["_sp"] = len(w["text"]) == 1 and w["text"].isalpha()
        out.append(w)
    return out


def parse_grid(words, full_text, today):
    """Parse a monthly calendar grid from positioned words.

    words: dicts with text, x0, x1, top, bottom. Returns (year, month, {iso: [sessions]}) or None.
    """
    if not words:
        return None
    words = _merge_letters(words)
    lines = _lines_of(words)
    # weekday header: a line with >= 6 distinct weekday tokens in order
    header = None
    for y, ws in lines:
        hs = [(w, _day_index(re.sub(r"[^a-z]", "", w["text"].lower())[:3] + "")) for w in ws if HEAD_TOK.match(w["text"])]
        idx = []
        for w, _ in hs:
            k = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}[w["text"][:3].lower()]
            idx.append((k, (w["x0"] + w["x1"]) / 2))
        ks = [k for k, _ in idx]
        if len(set(ks)) >= 6 and ks == sorted(ks):
            header = (y, dict(idx))
            break
    if not header:
        return None
    hy, centers = header
    if len(centers) == 6:
        missing = [k for k in range(7) if k not in centers][0]
        xs = sorted(centers.items())
        step = (xs[-1][1] - xs[0][1]) / (xs[-1][0] - xs[0][0])
        centers[missing] = xs[0][1] + (missing - xs[0][0]) * step
    cx = [centers[k] for k in range(7)]
    step = (cx[6] - cx[0]) / 6
    bounds = [(cx[k] - step / 2, cx[k] + step / 2) for k in range(7)]
    bounds[0] = (cx[0] - step * 0.75, bounds[0][1])

    named = []
    for m in MONTH_RE.finditer(full_text):
        mo = MONTHS[m.group(1).lower().rstrip(".")]
        yr = int(m.group(2)) if m.group(2) else None
        named.append((mo, yr))

    def find_rows(shift):
        """Date-number rows, assuming numbers sit `shift` cell-widths left of the header centre."""
        def num_col(x0, x1):
            xc = (x0 + x1) / 2 + step * shift
            k = min(range(7), key=lambda i: abs(xc - cx[i]))
            return k if abs(xc - cx[k]) < step * 0.5 else None
        rows = []
        for y, ws in lines:
            if y <= hy + 1:
                continue
            nums = []
            for w in ws:
                m = DATE_TOK.match(w["text"])
                if m and 1 <= int(m.group(1)) <= 31:
                    c = num_col(w["x0"], w["x1"])
                    if c is not None:
                        nums.append((c, int(m.group(1)), w, m.group(2)))
            if len(nums) < 3:
                continue
            nums.sort(key=lambda t: t[0])
            ok = 0
            for (c1, d1, _, _), (c2, d2, _, _) in zip(nums, nums[1:]):
                if d2 - d1 == c2 - c1 or (d2 < d1 and d2 <= c2 - c1):
                    ok += 1
            if ok >= max(2, len(nums) - 2):
                rows.append((y, nums))
        merged = []
        for y, nums in rows:
            if merged and y - merged[-1][0] < 8:
                merged[-1][1].extend(nums)
            else:
                merged.append([y, list(nums)])
        return merged

    def best_month(rows):
        best = None
        for off in range(-4, 4):
            y0, m0 = today.year, today.month + off
            while m0 < 1:
                m0 += 12; y0 -= 1
            while m0 > 12:
                m0 -= 12; y0 += 1
            score = 0
            prev_m = (y0 - (m0 == 1), (m0 - 2) % 12 + 1)
            next_m = (y0 + (m0 == 12), m0 % 12 + 1)
            for _, nums in rows:
                for c, d, _, _ in nums:
                    tries = [(y0, m0)] + ([prev_m] if d >= 20 else []) + ([next_m] if d <= 10 else [])
                    for yy, mm in tries:
                        try:
                            if dt.date(yy, mm, d).isoweekday() % 7 == c:
                                score += 1
                                break
                        except ValueError:
                            pass
            name_bonus = 3 if (m0, y0) in named else (2 if (m0, None) in named else 0)
            cand = (score, name_bonus, -abs(off), y0, m0)
            if best is None or cand > best:
                best = cand
        return best

    choice = None
    for shift in (0.35, 0.0, -0.35):
        rows = find_rows(shift)
        if len(rows) < 3:
            continue
        total = sum(len(n) for _, n in rows)
        score, bonus, near, y0, m0 = best_month(rows)
        frac = score / total
        key = (frac >= 0.9, bonus, frac, near)
        if choice is None or key > choice[0]:
            choice = (key, rows, y0, m0, frac)
    if choice is None or choice[4] < 0.6:
        return None
    _, rows, year, month, _ = choice

    # cell edges: when date numbers sit at the left of each cell, cells run from one
    # column's numbers to the next; otherwise split halfway between header centres
    lefts = {}
    for _, nums in rows:
        for c, _, w, _ in nums:
            lefts.setdefault(c, []).append(w["x0"])
    med = {c: sorted(v)[len(v) // 2] for c, v in lefts.items()}
    offs = [med[c] - cx[c] for c in med]
    if len(med) == 7 and sorted(offs)[3] < -step * 0.2:
        edges = [med[c] - 4 for c in range(7)]
        bounds = [(edges[k], edges[k + 1] if k < 6 else edges[6] + step * 1.2) for k in range(7)]
        bounds[0] = (edges[0] - step * 0.4, bounds[0][1])

    # build the date for every column of every row
    first = dt.date(year, month, 1)
    first_col = first.isoweekday() % 7
    days = {}
    page_bottom = max(w["bottom"] for w in words) + 1
    for ri, (y, nums) in enumerate(rows):
        y_next = rows[ri + 1][0] if ri + 1 < len(rows) else page_bottom
        # anchor the row on its first in-month number
        anchor = None
        for c, d, _, _ in sorted(nums):
            try:
                if dt.date(year, month, d).isoweekday() % 7 == c:
                    anchor = dt.date(year, month, d) - dt.timedelta(days=c)
                    break
            except ValueError:
                continue
        if anchor is None:
            continue
        num_words = {id(w) for _, _, w, _ in nums}
        tail = {id(w): rest for _, _, w, rest in nums if rest}
        for c in range(7):
            date = anchor + dt.timedelta(days=c)
            lo, hi = bounds[c]
            cell = [w for w in words
                    if lo <= (w["x0"] + w["x1"]) / 2 < hi and y - 2 <= w["top"] < y_next - 2]
            parts = []
            for _, ws in _lines_of(cell):
                toks = []
                for w in ws:
                    if id(w) in num_words:
                        if id(w) in tail:
                            toks.append(tail[id(w)])
                        continue
                    toks.append(w["text"])
                if toks:
                    parts.append(" ".join(toks))
            text = " ".join(parts)
            days[date.isoformat()] = (date, text)
    out = {}
    for iso, (date, text) in days.items():
        out[iso] = sessions_from_text(text)
    # keep only days of the target month (neighbour-month cells are often partial)
    out = {k: v for k, v in out.items() if k.startswith("%04d-%02d" % (year, month))}
    return year, month, out


# ---------------------------------------------------------------- posters with a day column
FULL_DAY = re.compile(r"^(sun|mon|tue|wed|thu|fri|sat)[a-z]*day(s)?:?$", re.I)  # tolerates "WEDENESDAY"


def parse_weekly_positional(words):
    """Posters with day names in a left column, each centred beside its sessions.

    Returns {dow: [sessions]} or None when the page isn't laid out that way."""
    days = []
    for w in words:
        if FULL_DAY.match(w["text"]):
            k = _day_index(w["text"].rstrip(":s").lower()[:3]) if False else \
                ["sun", "mon", "tue", "wed", "thu", "fri", "sat"].index(w["text"][:3].lower())
            yc = (w["top"] + w["bottom"]) / 2
            if not any(d[0] == k and abs(d[1] - yc) < 4 for d in days):
                days.append((k, yc, w["x1"]))
    if len(set(d[0] for d in days)) < 4:
        return None
    xs = sorted(d[2] for d in days)
    col_right = xs[len(xs) // 2]
    if xs[-1] - xs[0] > 60:
        return None  # day names aren't stacked in one column
    week = {}
    rest = [w for w in words if not FULL_DAY.match(w["text"]) and w["x0"] > col_right - 2]
    seen = set()
    for y, ws in _lines_of(rest):
        line = fix_glyphs(" ".join(w["text"] for w in ws))
        key = (round(y / 3), line)
        if key in seen:
            continue
        seen.add(key)
        ms = [m for m in RANGE_RE.finditer(line) if parse_range(m)]
        if len(ms) != 1:
            continue
        m = ms[0]
        c = classify((line[:m.start()] + " " + line[m.end():]).strip())
        if not c:
            continue
        yc = sum((w["top"] + w["bottom"]) / 2 for w in ws) / len(ws)
        k, dy, _ = min(days, key=lambda d: abs(d[1] - yc))
        a, b = parse_range(m)
        week.setdefault(k, []).append(_sess(c, a, b))
    return {k: _dedupe(v) for k, v in week.items()}
