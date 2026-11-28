#!/usr/bin/env python3
"""Refresh schedule.json / schedule.js from the four station websites.

Each station is parsed into weekly shows in the STATION'S OWN time zone
(the web app converts to UK time). If a station fails or looks wrong, its
previous data is kept and the problem is printed, so one broken site never
wipes the others.

Usage:  python scraper/scrape.py            # all stations
        python scraper/scrape.py rockabilly # one station
Needs:  pip install requests beautifulsoup4 playwright && playwright install chromium
"""
import datetime, json, re, sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
D3 = {d[:3]: i for i, d in enumerate(DAYS)}
MIN_SHOWS = 25  # fewer than this = something is wrong with the parse


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def hhmm(s):
    """'9:30 pm' / '21:30' / '9pm' -> '21:30'"""
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*$", s.strip(), re.I)
    if not m:
        raise ValueError("bad time: %r" % s)
    h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
    if ap == "pm" and h < 12: h += 12
    if ap == "am" and h == 12: h = 0
    return "%02d:%02d" % (h, mi)


def show(dow, start, end, title, dj):
    return {"dow": dow, "start": start, "end": end, "show": title.strip(), "dj": dj.strip()}


def strip_dj(s):
    return re.sub(r"^\s*DJ\s+", "", s.strip(), flags=re.I)


# ---------------- Rockabilly Radio: one <table> per day, UK time ----------------
def parse_rockabilly(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tb in soup.find_all("table"):
        head = tb.get_text(" ", strip=True).split(" ")[0].lower()
        if head not in DAYS:
            continue
        dow = DAYS.index(head)
        for tr in tb.find_all("tr"):
            tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(tds) < 3:
                continue
            m = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", tds[2])
            if m:
                out.append(show(dow, m[1], m[2], tds[1], strip_dj(tds[0])))
    return out


# ---------------- SplinterWood: one long table, days not labelled, UK time ----------------
def parse_splinterwood(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        m = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", tds[1].get_text(" ", strip=True))
        if m:
            rows.append((m[1], m[2], tds[0].get_text(" ", strip=True), tds[2].get_text(" ", strip=True)))
    # The site lists Sunday..Saturday; a new day starts at 00:00 after a block that ended at 00:00.
    out, dow, prev_end = [], -1, "00:00"
    for s, e, title, dj in rows:
        if s == "00:00" and prev_end == "00:00":
            dow += 1
        if dow < 0:
            dow = 0
        if dow > 6:
            raise ValueError("SplinterWood: more than 7 days found")
        if re.fullmatch(r"auto\s*dj", title, re.I):
            dj = "Auto DJ"
        out.append(show(dow, s, e, title, dj))
        prev_end = e
    if dow != 6:
        raise ValueError("SplinterWood: expected 7 days, found %d" % (dow + 1))
    return out


# ---------------- Rockin' Rhythm & Blues: JS-built weekly grid, US Eastern ----------------
def render(url, wait_selector="body"):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA["User-Agent"], locale="en-GB")
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_selector(wait_selector, timeout=30000)
        pg.wait_for_timeout(4000)          # let script-built content appear
        html = pg.content()
        b.close()
    return html


def parse_rrb(html):
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if t.find("a", href=re.compile(r"/show/"))), None)
    if table is None:
        raise ValueError("RRB: schedule grid not found")
    rows = table.find_all("tr")
    # header: Mon..Sun columns after the first (hour) column
    dows = []
    for th in rows[0].find_all(["th", "td"])[1:]:
        m = re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)", th.get_text(" ", strip=True), re.I)
        dows.append(D3[m[1].lower()] if m else None)
    seen, out = set(), []
    for tr in rows[1:]:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        hm = re.match(r"(\d{1,2})\s*(am|pm)", cells[0].get_text(" ", strip=True), re.I)
        if not hm:
            continue
        row_h = int(hhmm(hm[0])[:2])
        for i, td in enumerate(cells[1:]):
            dow = dows[i] if i < len(dows) else None
            if dow is None:
                continue
            for a in td.find_all("a", href=re.compile(r"/show/")):
                txt = td.get_text("\n", strip=True)
                times = re.search(r"(\d{1,2}(?::\d{2})?\s*[ap]m)\s*to\s*(\d{1,2}(?::\d{2})?\s*[ap]m)", txt, re.I)
                if not times:
                    continue
                s, e = hhmm(times[1]), hhmm(times[2])
                d = dow
                if int(s[:2]) > row_h:      # e.g. an 11pm show shown in the 12am row = previous day
                    d = (dow - 1) % 7
                title = a.get_text(" ", strip=True)
                dj, sep, name = title.partition(" - ")
                if not sep:
                    dj, name = "", title
                if re.search(r"stream", name, re.I) and not sep:
                    dj = "Auto DJ"
                key = (d, s, e, name)
                if key not in seen:
                    seen.add(key)
                    out.append(show(d, s, e, name, dj))
    return out


# ---------------- Rockin 247 Radio: radio.co embed (JavaScript-built), US Pacific ----------------
EMBED = "https://embed.radio.co/embeds/schedule/es8a3fb53.html"
R247_TZ = "America/Los_Angeles"
START_KEYS = ("start", "start_time", "starts", "starts_at", "begin", "from")
END_KEYS = ("end", "end_time", "ends", "ends_at", "finish", "to")
TITLE_KEYS = ("name", "title", "show", "show_name", "programme", "program")
DJ_KEYS = ("presenter", "dj", "host", "presenter_name", "artist")


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _to_dt(v, tz):
    from zoneinfo import ZoneInfo
    if isinstance(v, (int, float)):
        v = v / 1000 if v > 1e11 else v
        return datetime.datetime.fromtimestamp(v, datetime.timezone.utc).astimezone(ZoneInfo(tz))
    s = str(v).strip().replace("Z", "+00:00")
    d = datetime.datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s)
    return d.astimezone(ZoneInfo(tz)) if d.tzinfo else d.replace(tzinfo=ZoneInfo(tz))


def shows_from_json(blobs, tz):
    """Find objects that look like schedule entries (a title plus start and end
    date-times) anywhere in captured JSON, and turn them into weekly shows."""
    seen, out = set(), []
    for blob in blobs:
        for d in _walk(blob):
            title, st, en = _first(d, TITLE_KEYS), _first(d, START_KEYS), _first(d, END_KEYS)
            if not (isinstance(title, str) and st is not None and en is not None):
                continue
            try:
                a, b = _to_dt(st, tz), _to_dt(en, tz)
            except Exception:
                continue
            dj = _first(d, DJ_KEYS)
            dj = dj if isinstance(dj, str) else ""
            dow = (a.weekday() + 1) % 7            # python Mon=0 -> our Sun=0
            key = (dow, a.strftime("%H:%M"), b.strftime("%H:%M"), title)
            if key not in seen:
                seen.add(key)
                out.append(show(dow, key[1], key[2], title, dj))
    return out


def radio_co_shows(blobs):
    """radio.co schedule JSON: {"data": [{"start": ISO-UTC, "end": ISO-UTC,
    "playlist": {"name", "artist", "title"}}, ...]} covering ~2 weeks.
    Uses the first 7 days as the repeating weekly pattern. Times stay in UTC."""
    events = []
    for blob in blobs:
        for d in _walk(blob):
            pl = d.get("playlist")
            if isinstance(pl, dict) and d.get("start") and d.get("end"):
                try:
                    events.append((_to_dt(d["start"], "UTC"), _to_dt(d["end"], "UTC"), pl))
                except Exception:
                    pass
    if not events:
        return []
    first = min(e[0] for e in events).replace(hour=0, minute=0, second=0, microsecond=0)
    last = first + datetime.timedelta(days=7)
    seen, out = set(), []
    for a, b, pl in events:
        if not (first <= a < last):
            continue
        name = (pl.get("name") or pl.get("title") or "").strip()
        artist = (pl.get("artist") or "").strip()
        if artist.lower() in ("auto player", "auto dj") or name.lower() == "default":
            name, dj = "Auto Player", "Auto DJ"
        elif not artist or "rockin 247" in artist.lower():
            dj = name            # these playlists are named after the DJ
        else:
            dj = artist
        dow = (a.weekday() + 1) % 7            # python Mon=0 -> our Sun=0
        key = (dow, a.strftime("%H:%M"), b.strftime("%H:%M"), name)
        if key not in seen:
            seen.add(key)
            out.append(show(dow, key[1], key[2], name, dj))
    return out


def parse_rockin247(_unused=None):
    """1) manual/rockin247.json if it has shows (always wins)
       2) radio.co's schedule JSON, fetched directly
       3) open the widget in a browser, capture its JSON, parse that
    Whatever the browser sees is saved to debug/rockin247.txt."""
    manual = ROOT / "manual" / "rockin247.json"
    if manual.exists():
        data = json.loads(manual.read_text())
        if data.get("shows"):
            return data["shows"]

    # 2) direct
    try:
        hdr = dict(UA, Referer="https://embed.radio.co/", Origin="https://embed.radio.co")
        w = requests.get("https://public.radio.co/embeds/schedule/es8a3fb53", headers=hdr, timeout=30).json()
        station = w["data"]["station"]
    except Exception:
        station = "s0f3a44a66"
    try:
        r = requests.get("https://public.radio.co/stations/%s/embed/schedule" % station,
                         headers=dict(UA, Referer="https://embed.radio.co/", Origin="https://embed.radio.co"), timeout=30)
        r.raise_for_status()
        out = radio_co_shows([r.json()])
        if len(out) >= MIN_SHOWS:
            return out
    except Exception as e:
        print("Rockin247: direct fetch failed (%s); trying browser" % e, file=sys.stderr)

    # 3) browser
    from playwright.sync_api import sync_playwright
    blobs, log = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()

        def on_resp(r):
            try:
                if "json" in r.headers.get("content-type", ""):
                    body = r.json()
                    blobs.append(body)
                    log.append("JSON %s\n%s\n" % (r.url, json.dumps(body)[:4000]))
                else:
                    log.append("RESP %s %s" % (r.status, r.url))
            except Exception as e:
                log.append("RESP-ERR %s %s" % (r.url, e))

        pg.on("response", on_resp)
        pg.goto(EMBED, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(3000)
        text = pg.inner_text("body")
        b.close()
    dbg = ROOT / "debug"
    dbg.mkdir(exist_ok=True)
    (dbg / "rockin247.txt").write_text("\n".join(log) + "\n\n=== VISIBLE TEXT ===\n" + text[:8000])
    out = radio_co_shows(blobs)
    if len(out) < MIN_SHOWS:
        raise ValueError("could not read the radio.co widget; see debug/rockin247.txt")
    return out


# ---------------- SplinterWood (new home-page design): day tabs (#tabs) fill a slot list (#slots) via JavaScript ----------------
RANGE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap]m)?\s*(?:[^\d:.\w]{1,6}|to)\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap]m)?", re.I)
BADGES = {"on air", "live", "now", "on air now", "next", "up next", "now playing", "on now"}


def _h24(h, m, ap):
    h, m = int(h), int(m or 0)
    if ap:
        ap = ap.lower()
        if ap == "pm" and h < 12: h += 12
        if ap == "am" and h == 12: h = 0
    return "%02d:%02d" % (h % 24, m)


def splinterwood_fetch(url):
    """Open the page in a browser, click each day tab, and collect the text of each slot shown."""
    from playwright.sync_api import sync_playwright
    problems, data = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA["User-Agent"], locale="en-GB", viewport={"width": 1280, "height": 900})
        pg.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: problems.append("pageerror: %s" % e))
        pg.on("requestfailed", lambda r: problems.append("requestfailed: " + r.url))
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_selector("#tabs button, #tabs [role='tab']", timeout=30000)
        except Exception as e:
            b.close()
            raise RuntimeError("day tabs never appeared (%s). Page problems: %s" % (str(e).splitlines()[0], problems[:8]))
        tabs = pg.locator("#tabs button, #tabs [role='tab']")
        for i in range(tabs.count()):
            tab = tabs.nth(i)
            label = tab.inner_text().strip()
            tab.click()
            pg.wait_for_timeout(300)
            items = pg.eval_on_selector_all("#slots > li", """els => els.map(li => {
                const out = [], w = document.createTreeWalker(li, NodeFilter.SHOW_TEXT);
                while (w.nextNode()) { const t = w.currentNode.textContent.replace(/\\s+/g, ' ').trim(); if (t) out.push(t); }
                return out.join('\\n'); })""")
            data.append({"tab": label, "items": items})
        b.close()
    # a small sample so the layout can be checked by eye after a run
    d = ROOT / "debug"
    d.mkdir(exist_ok=True)
    sample = "\n\n".join("TAB %r: %d slots\n%s" % (x["tab"], len(x["items"]), "\n---\n".join(x["items"][:3])) for x in data)
    (d / "splinterwood.txt").write_text("Sample of what the scraper read (first 3 slots per day):\n\n" + sample + "\n")
    return json.dumps(data)


def parse_splinterwood_slots(raw):
    out = []
    for day in json.loads(raw):
        m = re.match(r"\s*(mon|tue|wed|thu|fri|sat|sun)", day["tab"], re.I)
        if not m:
            raise ValueError("SplinterWood: cannot read day tab label %r" % day["tab"])
        dow = D3[m[1].lower()]
        for text in day["items"]:
            r = RANGE.search(text)
            if not r:
                continue
            ap1 = r[3] or r[6]
            start, end = _h24(r[1], r[2], ap1), _h24(r[4], r[5], r[6])
            rest = (text[:r.start()] + "\n" + text[r.end():]).splitlines()
            lines = [l.strip(" \t\u00b7|-\u2013\u2014,") for l in rest]
            lines = [l for l in lines if l and l.lower() not in BADGES]
            # each slot has an avatar circle holding the presenter's initials ("PH", "K"): not part of the name
            if len(lines) >= 2 and re.fullmatch(r"[A-Z]{1,3}", lines[0]) and len(lines[1]) > 3:
                lines = lines[1:]
            title = lines[0] if lines else "Show"
            dj = lines[1] if len(lines) > 1 else ""
            if re.match(r"auto\s*dj", title, re.I):
                dj = "Auto DJ"      # the line under it is a description ("Non-stop rock'n'roll, no presenter")
            out.append(show(dow, start, end, title, dj))
    return out


# ---------------- Rockin' 50s: plain text, day headings, "8.00-10.00 Show name", UK time (assumed) ----------------
def parse_rockin50s(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav"]):
        t.decompose()
    out, dow = [], None
    pat = re.compile(r"^\**\s*(\d{1,2})[.:](\d{2})\s*-\s*(\d{1,2})[.:](\d{2})\s*[-:–]?\s*(.*?)\**\s*$")
    for line in soup.get_text("\n").splitlines():
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)   # [Monday](#) -> Monday
        line = line.strip().strip("*# ").strip()
        if not line:
            continue
        if line.lower() in DAYS:
            dow = DAYS.index(line.lower())
            continue
        m = pat.match(line)
        if m and dow is not None:
            start, end = "%02d:%s" % (int(m[1]), m[2]), "%02d:%s" % (int(m[3]), m[4])
            out.append(show(dow, start, end, m[5].strip() or "Show", ""))
    return out


# ---------------- WWOZ (New Orleans): dated entries "Title ... Saturday Nov 28th, 2026, 6:00 am - 8:00 am" ----------------
WWOZ_ENTRY = re.compile(
    r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?,?\s*"
    r"(\d{1,2}(?::\d{2})?\s*[ap]m)\s*[-\u2013\u2014to]+\s*(\d{1,2}(?::\d{2})?\s*[ap]m)", re.I)


WWOZ_DATE = re.compile(r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\s*$", re.I)
WWOZ_TIMES = re.compile(r"^(\d{1,2}(?::\d{2})?\s*[ap]m)\s*[-\u2013\u2014]+\s*(\d{1,2}(?::\d{2})?\s*[ap]m)$", re.I)


def parse_wwoz(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    found, seen = [], set()
    for i, line in enumerate(lines):
        m = WWOZ_ENTRY.search(line)            # layout 1: title, date and times on one line
        if m:
            title = line[:m.start()].strip(" :-\u2013|,")
            if not title and i > 0:
                title = lines[i - 1]
            mon, dnum, yr, t1, t2 = m[1], m[2], m[3], m[4], m[5]
        else:                                    # layout 2 (the live page): title / date / time on separate lines
            d = WWOZ_DATE.match(line)
            tm = None
            if d and i + 1 < len(lines):
                tm = WWOZ_TIMES.match(lines[i + 1])                              # "6:00 am - 8:00 am" on one line
                if not tm:                                                       # live page: "6:00 am", "-", "8:00 am" on three lines
                    tm = WWOZ_TIMES.match(" ".join(lines[i + 1:i + 4]))
            if not (d and tm and i > 0):
                continue
            title = lines[i - 1]
            mon, dnum, yr, t1, t2 = d[1], d[2], d[3], tm[1], tm[2]
        if not title:
            continue
        try:
            month = datetime.datetime.strptime(mon[:3].title(), "%b").month
            year = int(yr) if yr else datetime.date.today().year
            day = datetime.date(year, month, int(dnum))
            start, end = hhmm(t1), hhmm(t2)
        except ValueError:
            continue
        key = (day, start, end, title)
        if key not in seen:
            seen.add(key)
            found.append(key)
    if not found:
        return []
    first = min(k[0] for k in found)
    out = []
    for day, start, end, title in found:
        if (day - first).days >= 7:      # the page also lists the start of the following week
            continue
        dow = (day.weekday() + 1) % 7    # python Mon=0 -> our Sun=0
        name, _, dj = re.split(r"(\s+with\s+)", title, maxsplit=1, flags=re.I) if re.search(r"\s+with\s+", title, re.I) else (title, "", "")
        out.append(show(dow, start, end, name, dj))
    return out


def wwoz_fetch(url):
    html = get(url)
    if "/programs/" in html:
        return html
    return render(url, "a[href*='/programs/']")      # fall back to a real browser if the list is script-built


# ---------------- Doowop Radio: Tockify calendar (events carry UTC millisecond timestamps) ----------------
def tockify_events(blobs):
    """Pull (start_ms, end_ms, title) out of Tockify JSON, wherever it sits in the structure."""
    def txt(x):
        if isinstance(x, dict):
            return str(x.get("text") or "")
        return str(x or "")
    out = []
    for d in _walk(blobs):
        when = d.get("when")
        if not (isinstance(when, dict) and isinstance(when.get("start"), dict) and "millis" in when["start"]):
            continue
        end = when.get("end") if isinstance(when.get("end"), dict) else {}
        content = d.get("content") if isinstance(d.get("content"), dict) else {}
        title = txt(content.get("summary")) or txt(d.get("summary")) or txt(d.get("title"))
        if title and end.get("millis"):
            out.append((int(when["start"]["millis"]), int(end["millis"]), title.strip()))
    return out


def doowop_fetch(url):
    import time
    start_ms = int(time.time() // 86400 * 86400 * 1000)            # midnight UTC today, so today's earlier slots are included
    try:
        r = requests.get("https://tockify.com/api/ngevent", headers=UA, timeout=30,
                         params={"calname": "doowop", "max": 300, "start-ms": start_ms})
        r.raise_for_status()
        data = r.json()
        if tockify_events(data):
            return json.dumps(data)
    except Exception as e:
        print("Doowop: direct feed failed (%s); trying browser" % e, file=sys.stderr)
    from playwright.sync_api import sync_playwright
    blobs = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA["User-Agent"], locale="en-GB")

        def on_resp(r):
            try:
                if "json" in r.headers.get("content-type", ""):
                    blobs.append(r.json())
            except Exception:
                pass

        pg.on("response", on_resp)
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(8000)
        b.close()
    return json.dumps(blobs)


def parse_doowop(raw):
    events = tockify_events(json.loads(raw))
    if not events:
        return []
    utc = datetime.timezone.utc
    first = min(e[0] for e in events) // 86400000                  # day number of the earliest event
    seen, out, sample = set(), [], []
    for start, end, title in sorted(events):
        if (start // 86400000) - first >= 7:                       # first 7 days = the repeating weekly pattern
            continue
        a = datetime.datetime.fromtimestamp(start / 1000, utc)
        b = datetime.datetime.fromtimestamp(end / 1000, utc)
        name, _, host = re.split(r"(\s+with\s+)", title, maxsplit=1, flags=re.I) if re.search(r"\s+with\s+", title, re.I) else (title, "", "")
        key = ((a.weekday() + 1) % 7, a.strftime("%H:%M"), b.strftime("%H:%M"), name)
        if key not in seen:
            seen.add(key)
            out.append(show(key[0], key[1], key[2], name, host))
        sample.append("%s -> %s  %s" % (a.strftime("%a %d %b %H:%M"), b.strftime("%H:%M"), title))
    d = ROOT / "debug"
    d.mkdir(exist_ok=True)
    (d / "doowop.txt").write_text("Events read from Tockify (UTC), first 7 days:\n" + "\n".join(sample[:60]) + "\n")
    return out


# ---------------- Stations with no machine-readable schedule (e.g. a picture): manual/<id>.json ----------------
def manual_parser(sid):
    def parse(_unused=None):
        path = ROOT / "manual" / (sid + ".json")
        data = json.loads(path.read_text())
        shows = data.get("shows", [])
        if not shows:
            raise ValueError("manual/%s.json has no shows" % sid)
        return shows
    return parse


STATIONS = {
    "rockabilly":   dict(name="Rockabilly Radio", tz="Europe/London", url="https://www.rockabillyradio.net/schedule/",
                         streams=["https://rradio.fastserv.com/hifi"],
                         fetch=lambda u: get(u), parse=parse_rockabilly),
    "rockin247":    dict(name="Rockin 247 Radio", tz="UTC", url="https://rockin247radio.com/schedule/",
                         streams=["https://s4.radio.co/s0f3a44a66/listen"],
                         fetch=lambda u: "", parse=parse_rockin247),
    "splinterwood": dict(name="SplinterWood Radio", tz="Europe/London", url="https://www.splinterwoodradio.uk/",   # the #schedule section of the home page
                         site="https://www.splinterwoodradio.uk/index.html",
                         streams=["https://securestreams6.autopo.st:2302/listen.mp3?t=1795719521325"],
                         fetch=splinterwood_fetch, parse=parse_splinterwood_slots),
    "rockin50s":    dict(browser_fallback=True, name="Rockin' 50s", tz="Europe/London", url="https://www.rockin50s.uk/schedule.php",
                         streams=["https://eu11.fastcast4u.com:1090/;?type=http&nocache=1778480495"],
                         fetch=lambda u: get(u), parse=parse_rockin50s),
    "doowop":       dict(name="Doowop Radio", tz="UTC", url="https://tockify.com/doowop/pinboard",
                         site="https://tockify.com/doowop/pinboard", min_shows=3,
                         # our Cloudflare Worker relays the station's http stream as https; the plain address is a fallback
                         streams=["https://rrb-stream.air2810.workers.dev/doowop", "http://66.59.109.208:8000/;stream.mp3?sid=2"],
                         fetch=doowop_fetch, parse=parse_doowop),
    "wwoz":         dict(browser_fallback=True, name="WWOZ New Orleans", tz="America/Chicago", url="https://www.wwoz.org/schedule",
                         site="https://www.wwoz.org",
                         streams=["https://wwoz-sc.streamguys1.com/wwoz-hi.mp3"],
                         fetch=wwoz_fetch, parse=parse_wwoz),
    "zombiedog":    dict(name="Zombie Dog Radio", tz="Europe/London", url="https://zombiedogradio.com",
                         min_shows=1,       # timetable is a picture; typed into manual/zombiedog.json
                         streams=["https://fast.citrus3.com:8556/stream"],
                         fetch=lambda u: "", parse=manual_parser("zombiedog")),
    "rrb":          dict(name="Rockin' Rhythm & Blues", tz="America/New_York", url="https://rockinrhythmandbluesradio.com/schedule-usa-eastern/",
                         # tried in order: our https relay (Cloudflare Worker), then the station's own address.
                         # If the station provides an https address, put it first and the Worker second.
                         streams=["https://rrb-stream.air2810.workers.dev", "http://s6.voscast.com:11632/live"],
                         fetch=lambda u: render(u, "a[href*='/show/']"), parse=parse_rrb),
}


def dump_debug(sid, raw, err):
    """When a station fails, save what we received so the cause can be diagnosed."""
    d = ROOT / "debug"
    d.mkdir(exist_ok=True)
    body = ""
    if isinstance(raw, str) and raw:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            # scripts often hold the data on pages that build their schedule with JavaScript
            srcs = [t.get("src") for t in soup.find_all("script") if t.get("src")]
            inline = [t.get_text() for t in soup.find_all("script") if not t.get("src") and t.get_text().strip()]
            hits = [x for x in inline if re.search(r"schedule|presenter|auto\s*dj|monday|sunday", x, re.I)]
            script_part = "\n\n=== SCRIPT SRC ===\n%s\n\n=== INLINE SCRIPTS mentioning schedule/presenters (%d of %d) ===\n%s" % (
                "\n".join(srcs), len(hits), len(inline), "\n\n----\n\n".join(h.strip()[:7000] for h in hits[:4]))
            for t in soup(["script", "style"]):
                t.decompose()
            lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
            body = "HTML length: %d\ntables: %d\ncontains '/programs/': %s\n\n=== VISIBLE TEXT (first 250 lines) ===\n%s\n\n=== BODY MARKUP (first 5000 chars) ===\n%s" % (
                len(raw), len(soup.find_all("table")), "/programs/" in raw, "\n".join(lines[:250]), str(soup.body or soup)[:5000]) + script_part
        except Exception as e:
            body = "could not summarise: %s" % e
    (d / (sid + ".txt")).write_text("ERROR: %s\n\n%s\n" % (err, body))


def main(only):
    path = ROOT / "schedule.json"
    old = json.loads(path.read_text()) if path.exists() else {"stations": []}
    old_by_id = {s["id"]: s for s in old["stations"]}
    result, problems = [], []
    for sid, cfg in STATIONS.items():
        prev = old_by_id.get(sid, {})
        entry = {"id": sid, "name": cfg["name"], "tz": cfg["tz"], "url": cfg["url"],
                 "site": cfg.get("site", cfg["url"]), "streams": cfg.get("streams", []),
                 "shows": prev.get("shows", [])}
        if prev.get("note"): entry["note"] = prev["note"]
        if only and sid not in only:
            result.append(entry); continue
        try:
            raw, shows, err = "", [], None
            need = cfg.get("min_shows", MIN_SHOWS)
            try:
                raw = cfg["fetch"](cfg["url"])
                shows = cfg["parse"](raw)
            except Exception as e:
                err = repr(e)
            if len(shows) < need and cfg.get("browser_fallback"):
                try:
                    raw2 = render(cfg["url"])
                    shows2 = cfg["parse"](raw2)
                    raw = raw2                      # keep the browser's view for the diagnostic file
                    if len(shows2) > len(shows):
                        shows, err = shows2, None
                except Exception as e2:
                    err = "%s | browser retry: %r" % (err or "only %d shows parsed" % len(shows), e2)
            if len(shows) < need:
                msg = err or "only %d shows parsed (expected %d+)" % (len(shows), need)
                dump_debug(sid, raw, msg)
                raise ValueError(msg)
            for s in shows:
                s["auto"] = bool(re.match(r"auto\s*dj", s["dj"], re.I))
            shows.sort(key=lambda s: (s["dow"], s["start"], s["end"]))
            entry["shows"] = shows
            entry.pop("note", None)
            print("ok   %-13s %d shows" % (sid, len(shows)))
        except Exception as e:
            problems.append("%s: %s" % (sid, e))
            print("FAIL %-13s %s (kept previous data)" % (sid, e), file=sys.stderr)
        result.append(entry)
    out = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "scraper", "stations": result}
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (ROOT / "schedule.js").write_text("window.SCHEDULE_SEED = " + json.dumps(out, ensure_ascii=False) + ";\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(set(sys.argv[1:])))
