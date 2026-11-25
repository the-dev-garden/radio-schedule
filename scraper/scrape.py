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
UA = {"User-Agent": "Mozilla/5.0 (personal radio schedule viewer)"}
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
def render(url, wait_selector):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(url, wait_until="networkidle", timeout=60000)
        pg.wait_for_selector(wait_selector, timeout=30000)
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


def parse_rockin247(_unused=None):
    """1) manual/rockin247.json if it has shows (always wins)
       2) open the embed in a browser, capture its JSON responses, parse them
       3) parse a rendered table
    Whatever it sees is saved to debug/rockin247.txt so a failure can be diagnosed."""
    manual = ROOT / "manual" / "rockin247.json"
    if manual.exists():
        data = json.loads(manual.read_text())
        if data.get("shows"):
            return data["shows"]

    from playwright.sync_api import sync_playwright
    blobs, log = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(timezone_id=R247_TZ)

        def on_resp(r):
            try:
                ct = r.headers.get("content-type", "")
                if "json" in ct or r.url.endswith(".json"):
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
        html = pg.content()
        text = pg.inner_text("body")
        b.close()
    dbg = ROOT / "debug"
    dbg.mkdir(exist_ok=True)
    (dbg / "rockin247.txt").write_text("\n".join(log) + "\n\n=== VISIBLE TEXT ===\n" + text[:8000]
                                       + "\n\n=== HTML (start) ===\n" + html[:8000])

    out = shows_from_json(blobs, R247_TZ)
    if len(out) >= MIN_SHOWS:
        return out
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tb in soup.find_all("table"):
        head = tb.get_text(" ", strip=True).lower()
        for d in DAYS:
            if head.startswith(d):
                dow = DAYS.index(d)
                for tr in tb.find_all("tr"):
                    tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    m = next((re.search(r"(\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)\s*(?:-|to)\s*(\d{1,2}(?::\d{2})?\s*(?:[ap]m)?)", x, re.I) for x in tds if re.search(r"\d", x)), None)
                    names = [x for x in tds if x and not re.search(r"\d\s*(?:-|to)\s*\d", x)]
                    if m and names:
                        out.append(show(dow, hhmm(m[1]), hhmm(m[2]), names[-1], names[0] if len(names) > 1 else ""))
    if not out:
        raise ValueError("could not read the radio.co widget; see debug/rockin247.txt")
    return out


STATIONS = {
    "rockabilly":   dict(name="Rockabilly Radio", tz="Europe/London", url="https://www.rockabillyradio.net/schedule/",
                         fetch=lambda u: get(u), parse=parse_rockabilly),
    "rockin247":    dict(name="Rockin 247 Radio", tz="America/Los_Angeles", url="https://rockin247radio.com/schedule/",
                         fetch=lambda u: "", parse=parse_rockin247),
    "splinterwood": dict(name="SplinterWood Radio", tz="Europe/London", url="https://www.splinterwoodradio.uk/presenter-schedule/",
                         fetch=lambda u: get(u), parse=parse_splinterwood),
    "rrb":          dict(name="Rockin' Rhythm & Blues", tz="America/New_York", url="https://rockinrhythmandbluesradio.com/schedule-usa-eastern/",
                         fetch=lambda u: render(u, "a[href*='/show/']"), parse=parse_rrb),
}


def main(only):
    path = ROOT / "schedule.json"
    old = json.loads(path.read_text()) if path.exists() else {"stations": []}
    old_by_id = {s["id"]: s for s in old["stations"]}
    result, problems = [], []
    for sid, cfg in STATIONS.items():
        prev = old_by_id.get(sid, {})
        entry = {"id": sid, "name": cfg["name"], "tz": cfg["tz"], "url": cfg["url"], "shows": prev.get("shows", [])}
        if prev.get("note"): entry["note"] = prev["note"]
        if only and sid not in only:
            result.append(entry); continue
        try:
            shows = cfg["parse"](cfg["fetch"](cfg["url"]))
            for s in shows:
                s["auto"] = bool(re.match(r"auto\s*dj", s["dj"], re.I))
            shows.sort(key=lambda s: (s["dow"], s["start"], s["end"]))
            if len(shows) < MIN_SHOWS:
                raise ValueError("only %d shows parsed (expected %d+)" % (len(shows), MIN_SHOWS))
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
