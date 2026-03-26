"""
Dell Med Events Calendar Generator
-----------------------------------
Combines three event sources:
  1. Public events  — scraped from dellmed.utexas.edu via RSS.app feed
  2. Internal events — extracted from RAW_EVENTS in index.html (ticker)
  3. Manual overrides — from internal-events.json (optional, for additions/edits)

Outputs:
  dell-med-events.ics  — ICS calendar for Outlook subscription
  calendar.html        — Webpage showing all upcoming events
"""

import re
import json
import uuid
import pytz
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ── Config ───────────────────────────────────────────────────────────────────
RSS_URL       = "https://rss.app/feeds/UXQywtXV9kG72UyK.xml"
ICS_FILE      = "dell-med-events.ics"
HTML_FILE     = "calendar.html"
TICKER_FILE   = "index.html"
OVERRIDE_FILE = "internal-events.json"
TIMEZONE      = "America/Chicago"
CALENDAR_NAME = "Dell Med Events"
CALENDAR_DESC = "Events from Dell Medical School at UT Austin"
GITHUB_BASE   = "https://michael-dean22.github.io/Dell_Med_Events"
# ─────────────────────────────────────────────────────────────────────────────

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
}
tz = pytz.timezone(TIMEZONE)


# ── Source 1: Public events via RSS + scraping ───────────────────────────────

def fetch_rss_links(url):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "lxml-xml")
    items = []
    for item in soup.find_all("item"):
        t = item.find("title")
        l = item.find("link")
        if t and l:
            items.append((t.get_text(strip=True), l.get_text(strip=True)))
    return items


def scrape_event_page(url):
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠ Could not fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    raw  = resp.text

    h1    = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    date_str = time_str = location_str = ""

    # Strategy 1: flat text lines — handles both "Date: value" and "Date:\nvalue"
    lines = [t.strip() for t in soup.get_text("\n").split("\n") if t.strip()]
    for i, line in enumerate(lines):
        ll  = line.lower()
        nxt = lines[i+1] if i+1 < len(lines) else ""

        if not date_str:
            if re.match(r'^date:\s+\S', line, re.I):
                date_str = re.sub(r'^date:\s*', '', line, flags=re.I).strip()
            elif ll == "date:" and re.search(r'\d{4}', nxt):
                date_str = nxt

        if not time_str:
            if re.match(r'^time:\s+\S', line, re.I):
                time_str = re.sub(r'^time:\s*', '', line, flags=re.I).strip()
            elif ll == "time:" and nxt:
                time_str = nxt

        if not location_str:
            if re.match(r'^location:\s+\S', line, re.I):
                location_str = re.sub(r'^location:\s*', '', line, flags=re.I).strip()
            elif ll == "location:" and nxt:
                location_str = nxt

    # Strategy 2: raw HTML regex fallback
    if not date_str:
        m = re.search(
            r'[Dd]ate:\s*(?:</[^>]+>\s*<[^>]+>\s*)*([A-Za-z]+,\s+[A-Za-z]+ \d{1,2},\s+\d{4})',
            raw)
        if m:
            date_str = m.group(1).strip()

    if not time_str:
        m = re.search(
            r'[Tt]ime:\s*(?:</[^>]+>\s*<[^>]+>\s*)*([\d:]+\s*[ap]\.?m\.?[^\n<]{0,20}[ap]\.?m\.?)',
            raw, re.I)
        if m:
            time_str = m.group(1).strip()

    if not location_str:
        m = re.search(
            r'[Ll]ocation:\s*(?:</[^>]+>\s*<[^>]+>\s*)*([^<\n]{2,60})', raw)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) < 80 and not candidate.lower().startswith("http"):
                location_str = candidate

    print(f"    date='{date_str}' | time='{time_str}' | loc='{location_str[:30]}'")

    # Description
    description = ""
    about = soup.find(lambda tag: tag.name in ["h2","h3"]
                      and "about" in tag.get_text(strip=True).lower())
    if about:
        parts = []
        for sib in about.find_next_siblings():
            if sib.name in ["h2","h3"]: break
            text = sib.get_text(" ", strip=True)
            if text: parts.append(text)
        description = " ".join(parts)
    else:
        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) > 60:
                description = text
                break

    # Zoom/Teams links
    meeting_links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href in seen: continue
        if any(kw in href.lower() for kw in ["zoom.us","teams.microsoft","events.teams"]):
            meeting_links.append((text or "Join Meeting", href)); seen.add(href)
        elif any(kw in text.lower() for kw in ["register","zoom","teams","join"]):
            if href.startswith("http"):
                meeting_links.append((text, href)); seen.add(href)

    desc_parts = []
    if description: desc_parts.append(description)
    if meeting_links:
        desc_parts.append("")
        for lt, lh in meeting_links:
            desc_parts.append(f"{lt}: {lh}")
    desc_parts.extend(["", f"Event page: {url}"])

    return {
        "title":       title,
        "url":         url,
        "date_str":    date_str,
        "time_str":    time_str,
        "location":    location_str,
        "description": "\\n".join(desc_parts),
        "source":      "public",
    }


def fetch_public_events():
    print("📡 Fetching RSS feed...")
    rss_items = fetch_rss_links(RSS_URL)
    print(f"   Found {len(rss_items)} events in feed")
    events = []
    for i, (title, url) in enumerate(rss_items, 1):
        print(f"   [{i}/{len(rss_items)}] Scraping: {title[:60]}...")
        ev = scrape_event_page(url)
        if ev:
            events.append(ev)
    print(f"   ✓ {len(events)} public events scraped")
    return events


# ── Source 2: Internal events from index.html ticker ────────────────────────

def extract_internal_events_from_ticker():
    """Read RAW_EVENTS from index.html. Uses bracket-depth counting to extract
    the full array, then regex-parses each event object. Respects the source
    field so public events in the ticker stay marked as public."""
    try:
        with open(TICKER_FILE, "r", encoding="utf-8") as f:
            ticker_content = f.read()
    except FileNotFoundError:
        print("  ⚠ index.html not found — skipping ticker events")
        return []

    m = re.search(r'const RAW_EVENTS\s*=\s*\[', ticker_content)
    if not m:
        print("  ⚠ RAW_EVENTS not found in index.html")
        return []

    # Bracket-depth walk to find the closing ]
    sp = m.end() - 1
    depth = 0
    ep = sp
    for i, ch in enumerate(ticker_content[sp:], sp):
        if ch == '[':  depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                ep = i + 1
                break
    raw_array = ticker_content[sp:ep]

    month_names = ["","January","February","March","April","May","June",
                   "July","August","September","October","November","December"]

    def get_field(field, text):
        # Match: field: "value"
        pat = r'["\']?' + field + r'["\']?\s*:\s*"([^"]*)"'
        hit = re.search(pat, text)
        if hit: return hit.group(1)
        # Match: field: 'value'
        pat2 = r"[\"']?" + field + r"[\"']?\s*:\s*'([^']*)'"
        hit2 = re.search(pat2, text)
        if hit2: return hit2.group(1)
        return ""

    events = []
    for obj_m in re.finditer(r'\{([^{}]+)\}', raw_array, re.DOTALL):
        obj = obj_m.group(1)
        date_ymd = get_field("date",     obj)
        if not date_ymd: continue
        title    = get_field("title",    obj).replace('\\"', '"')
        url      = get_field("url",      obj)
        time_str = get_field("time",     obj)
        location = get_field("location", obj)
        source   = get_field("source",   obj) or "internal"

        try:
            y, mo, d = date_ymd.split("-")
            date_str = f"{month_names[int(mo)]} {int(d)}, {y}"
        except Exception:
            continue

        desc = "Dell Med event\\n\\nEvent page: " + url
        events.append({
            "title":       title,
            "url":         url,
            "date_str":    date_str,
            "time_str":    time_str,
            "location":    location,
            "description": desc,
            "source":      source,
        })

    n_int = sum(1 for e in events if e["source"] == "internal")
    n_pub = sum(1 for e in events if e["source"] == "public")
    print(f"   ✓ {len(events)} events from index.html ({n_int} internal, {n_pub} public)")
    return events

def load_manual_overrides():
    """Load internal-events.json if it exists."""
    try:
        with open(OVERRIDE_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        print(f"   ✓ {len(items)} manual override events loaded from {OVERRIDE_FILE}")
        return items
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"  ⚠ Could not load {OVERRIDE_FILE}: {e}")
        return []


# ── Date/Time Parser ─────────────────────────────────────────────────────────

def parse_date_time(date_str, time_str):
    if not date_str:
        return None, None, False

    ds = re.sub(r'^[A-Za-z]+,\s*', '', date_str.strip())
    m  = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', ds)
    if not m:
        return None, None, False

    month_name, day, year = m.groups()
    month = MONTH_MAP.get(month_name.lower())
    if not month:
        return None, None, False

    date = datetime(int(year), month, int(day))
    ts   = (time_str or "").strip()

    if not ts or ts.lower() in ("all day", "all day"):
        return date.date(), (date + timedelta(days=1)).date(), True

    # Normalize time separators and common formats
    ts = ts.replace("\u2013","-").replace("\u2014","-")
    # Handle "12:00pm" style (no space before am/pm)
    ts = re.sub(r'(\d)(am|pm)', r'\1 \2', ts, flags=re.I)
    # Handle "12:00pm – 1:00pm" style
    ts = re.sub(r'(\d)(am|pm)\s*[-\u2013]\s*(\d)', r'\1 \2 - \3', ts, flags=re.I)

    time_pat = re.compile(r'(\d{1,2}(?::\d{2})?)\s*([ap]\.?m\.?)', re.I)
    times    = time_pat.findall(ts)

    def to24(t, mer):
        mer = mer.replace(".","").lower()
        h, mi = (int(t.split(":")[0]), int(t.split(":")[1])) if ":" in t else (int(t), 0)
        if mer == "pm" and h != 12: h += 12
        if mer == "am" and h == 12: h = 0
        return h, mi

    if len(times) >= 2:
        sh, sm = to24(*times[0])
        eh, em = to24(*times[1])
    elif len(times) == 1:
        sh, sm = to24(*times[0])
        eh, em = sh+1, sm
    else:
        sh, sm, eh, em = 0, 0, 1, 0

    dtstart = tz.localize(datetime(int(year), month, int(day), sh, sm))
    dtend   = tz.localize(datetime(int(year), month, int(day), eh, em))
    if dtend <= dtstart:
        dtend += timedelta(hours=1)
    return dtstart, dtend, False


# ── ICS Builder ──────────────────────────────────────────────────────────────

def ics_escape(text):
    if not text: return ""
    return text.replace("\\","\\\\").replace(";","\\;").replace(",","\\,")

def fold_line(line):
    result = []
    while len(line.encode("utf-8")) > 75:
        result.append(line[:75])
        line = " " + line[75:]
    result.append(line)
    return "\r\n".join(result)

def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//Dell Medical School//Dell Med Events//EN",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-CALDESC:{CALENDAR_DESC}",
        "X-WR-TIMEZONE:America/Chicago",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
    ]
    count = 0
    for ev in events:
        dtstart, dtend, all_day = parse_date_time(ev["date_str"], ev["time_str"])
        if dtstart is None:
            continue
        count += 1
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, ev["url"]))
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}"]
        if all_day:
            lines.append(f"DTSTART;VALUE=DATE:{dtstart.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}")
        else:
            lines.append(f"DTSTART;TZID=America/Chicago:{dtstart.strftime('%Y%m%dT%H%M%S')}")
            lines.append(f"DTEND;TZID=America/Chicago:{dtend.strftime('%Y%m%dT%H%M%S')}")
        lines.append(fold_line(f"SUMMARY:{ics_escape(ev['title'])}"))
        if ev.get("location"):
            lines.append(fold_line(f"LOCATION:{ics_escape(ev['location'])}"))
        if ev.get("description"):
            lines.append(fold_line(f"DESCRIPTION:{ics_escape(ev['description'])}"))
        lines.append(fold_line(f"URL:{ev['url']}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    print(f"  → ICS: {count} events written")
    return "\r\n".join(lines) + "\r\n"


# ── HTML Builder ─────────────────────────────────────────────────────────────

def ev_to_html_json(ev):
    """Convert a scraped event dict to a JSON-ready dict for calendar.html."""
    ds = re.sub(r'^[A-Za-z]+,\s*', '', (ev.get("date_str") or "").strip())
    m  = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', ds)
    if not m:
        return None
    month_name, day, year = m.groups()
    month = MONTH_MAP.get(month_name.lower())
    if not month:
        return None
    return {
        "date":     f"{int(year):04d}-{month:02d}-{int(day):02d}",
        "time":     ev.get("time_str",""),
        "title":    ev["title"],
        "location": ev.get("location",""),
        "url":      ev.get("url",""),
        "source":   ev.get("source","public"),
    }


def build_calendar_html(events):
    updated     = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
    ics_url     = f"{GITHUB_BASE}/dell-med-events.ics"
    ev_list     = []
    skipped     = 0

    for ev in events:
        d = ev_to_html_json(ev)
        if d:
            ev_list.append(d)
        else:
            skipped += 1

    n_pub = sum(1 for e in ev_list if e["source"] == "public")
    n_int = sum(1 for e in ev_list if e["source"] == "internal")
    print(f"  → HTML: {len(ev_list)} events ({n_pub} public, {n_int} internal), {skipped} skipped")

    events_json = json.dumps(ev_list, indent=2, ensure_ascii=False)

    # The HTML template uses {{ }} for CSS/JS braces to escape Python's f-string
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dell Med Events Calendar</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --orange:#BF5700; --od:#8C3E00; --ol:#FDF0E6;
    --cream:#FDFAF6; --ch:#1A1A1A; --gray:#6B6B6B;
    --border:#E5D8CC; --white:#FFFFFF;
    --blue:#1a73e8; --blue-light:#EBF3FE;
  }}
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:'DM Sans',sans-serif;background:var(--cream);color:var(--ch);min-height:100vh;}}
  .hdr{{background:var(--orange);position:relative;overflow:hidden;}}
  .hdr::before{{content:'';position:absolute;top:-60px;right:-60px;width:300px;height:300px;border-radius:50%;background:rgba(255,255,255,0.06);}}
  .hi{{max-width:1100px;margin:0 auto;padding:32px 24px 28px;position:relative;z-index:1;}}
  .eyebrow{{font-size:0.7rem;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.65);margin-bottom:6px;}}
  .htitle{{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,3.5vw,2.4rem);font-weight:700;color:#fff;line-height:1.15;margin-bottom:8px;}}
  .hsub{{font-size:0.88rem;color:rgba(255,255,255,0.72);font-weight:300;}}
  .sub-bar{{background:var(--od);border-bottom:3px solid rgba(0,0,0,0.15);}}
  .sub-inner{{max-width:1100px;margin:0 auto;padding:16px 24px 0;}}
  .sub-row{{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,0.12);}}
  .sub-icon{{width:32px;height:32px;background:rgba(255,255,255,0.15);border-radius:7px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
  .sub-text{{flex:1;min-width:180px;}}
  .sub-lbl{{font-size:0.67rem;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:2px;}}
  .sub-url{{font-family:monospace;font-size:0.76rem;color:#fff;word-break:break-all;}}
  .copy-btn{{background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);color:#fff;padding:7px 14px;border-radius:6px;font-size:0.75rem;font-weight:500;cursor:pointer;font-family:'DM Sans',sans-serif;transition:background 0.2s;white-space:nowrap;}}
  .copy-btn:hover{{background:rgba(255,255,255,0.25);}}
  .copy-btn.copied{{background:rgba(127,219,138,0.3);border-color:#7FDB8A;}}
  .how-row{{display:flex;gap:0;flex-wrap:wrap;padding:14px 0;}}
  .how-lbl{{font-size:0.62rem;font-weight:600;letter-spacing:0.09em;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-right:16px;align-self:center;white-space:nowrap;}}
  .how-step{{display:flex;align-items:flex-start;gap:7px;flex:1;min-width:140px;padding-right:14px;position:relative;}}
  .how-step:not(:last-child)::after{{content:'→';position:absolute;right:3px;top:1px;color:rgba(255,255,255,0.25);font-size:0.75rem;}}
  .how-num{{width:18px;height:18px;border-radius:50%;background:rgba(255,255,255,0.2);color:#fff;font-size:0.62rem;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;}}
  .how-title{{font-size:0.7rem;font-weight:600;color:#fff;margin-bottom:1px;}}
  .how-desc{{font-size:0.62rem;color:rgba(255,255,255,0.6);line-height:1.35;}}
  .toolbar{{max-width:1100px;margin:0 auto;padding:18px 24px 0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}}
  .filters{{display:flex;gap:6px;flex-wrap:wrap;}}
  .fb{{padding:5px 14px;border-radius:20px;border:1px solid var(--border);background:var(--white);font-size:0.72rem;font-weight:500;cursor:pointer;font-family:'DM Sans',sans-serif;color:var(--gray);transition:all 0.15s;}}
  .fb.on{{background:var(--orange);color:#fff;border-color:var(--orange);}}
  .fb.blue-btn.on{{background:var(--blue);border-color:var(--blue);}}
  .right-tools{{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}}
  .legend{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;}}
  .leg-item{{display:flex;align-items:center;gap:5px;font-size:0.7rem;color:var(--gray);}}
  .leg-dot{{width:10px;height:10px;border-radius:2px;flex-shrink:0;}}
  .leg-dot.internal{{background:var(--ol);border:1px solid #E5C9A8;}}
  .leg-dot.public{{background:var(--blue-light);border:1px solid #C5DCEF;}}
  .month-nav{{display:flex;align-items:center;gap:10px;}}
  .nav-btn{{background:none;border:1px solid var(--border);border-radius:6px;padding:5px 11px;cursor:pointer;font-size:0.82rem;color:var(--gray);font-family:'DM Sans',sans-serif;transition:all 0.15s;}}
  .nav-btn:hover{{background:var(--ol);border-color:var(--orange);color:var(--orange);}}
  .month-display{{font-family:'Playfair Display',serif;font-size:1.05rem;font-weight:600;color:var(--ch);min-width:150px;text-align:center;}}
  .admin-btn{{padding:5px 14px;border-radius:20px;border:1px solid var(--border);background:var(--white);font-size:0.72rem;font-weight:500;cursor:pointer;font-family:'DM Sans',sans-serif;color:var(--gray);transition:all 0.15s;}}
  .admin-btn:hover{{background:#F3F4F6;border-color:#999;}}
  .upd-note{{max-width:1100px;margin:0 auto;padding:8px 24px 0;font-size:0.65rem;color:var(--gray);font-style:italic;}}
  .cal-wrap{{max-width:1100px;margin:0 auto;padding:12px 24px 60px;}}
  .cal-grid{{display:grid;grid-template-columns:repeat(5,1fr);border-left:1px solid var(--border);border-top:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--white);box-shadow:0 2px 12px rgba(0,0,0,0.06);}}
  .day-header{{background:var(--orange);color:rgba(255,255,255,0.92);font-size:0.67rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;padding:9px 4px;text-align:center;border-right:1px solid rgba(255,255,255,0.15);border-bottom:1px solid var(--border);}}
  .cal-cell{{border-right:1px solid var(--border);border-bottom:1px solid var(--border);height:130px;padding:6px 5px 5px;background:var(--white);position:relative;overflow:hidden;}}
  .cal-cell.other-month{{background:#F9F6F3;}}
  .cal-cell.today{{background:#FFF8F4;}}
  .cal-cell.today .cell-num{{background:var(--orange);color:#fff;}}
  .cell-num{{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;font-size:0.75rem;font-weight:600;color:var(--gray);margin-bottom:3px;}}
  .cal-cell.other-month .cell-num{{color:#C8BFB7;}}
  .ev-pill{{display:block;font-size:0.6rem;font-weight:500;border-radius:3px;padding:2px 5px;margin-bottom:2px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-decoration:none;transition:opacity 0.15s;line-height:1.35;}}
  .ev-pill:hover{{opacity:0.78;}}
  .ev-pill.internal{{background:var(--ol);color:var(--od);}}
  .ev-pill.public{{background:var(--blue-light);color:#1558c0;}}
  .ev-pill.more{{background:var(--border);color:var(--gray);cursor:pointer;font-style:italic;}}
  .popover{{display:none;position:fixed;z-index:999;background:var(--white);border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,0.15);width:310px;overflow:hidden;}}
  .popover.show{{display:block;}}
  .pop-header{{padding:12px 14px 10px;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;justify-content:space-between;gap:8px;}}
  .pop-date{{font-size:0.7rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:var(--gray);}}
  .pop-close{{background:none;border:none;cursor:pointer;color:var(--gray);font-size:1.1rem;line-height:1;padding:0;flex-shrink:0;}}
  .pop-events{{padding:10px 14px 14px;max-height:280px;overflow-y:auto;}}
  .pop-ev{{display:flex;gap:8px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border);}}
  .pop-ev:last-child{{border-bottom:none;}}
  .pop-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:4px;}}
  .pop-dot.internal{{background:var(--orange);}}
  .pop-dot.public{{background:var(--blue);}}
  .pop-info{{flex:1;min-width:0;}}
  .pop-title a{{font-size:0.82rem;font-weight:600;color:var(--ch);text-decoration:none;line-height:1.3;display:block;margin-bottom:3px;}}
  .pop-title a:hover{{color:var(--orange);}}
  .pop-meta{{font-size:0.7rem;color:var(--gray);line-height:1.4;}}
  .pop-badge{{display:inline-block;font-size:0.55rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;padding:1px 5px;border-radius:3px;margin-top:3px;}}
  .pop-badge.internal{{background:var(--ol);color:var(--od);}}
  .pop-badge.public{{background:var(--blue-light);color:#1558c0;}}
  .admin-panel{{display:none;position:fixed;top:0;right:0;bottom:0;width:420px;background:var(--white);border-left:2px solid var(--border);box-shadow:-4px 0 24px rgba(0,0,0,0.12);z-index:1000;overflow-y:auto;flex-direction:column;}}
  .admin-panel.open{{display:flex;flex-direction:column;}}
  .ap-header{{background:var(--orange);padding:16px 18px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;}}
  .ap-title{{font-family:'Playfair Display',serif;font-size:1.1rem;font-weight:600;color:#fff;}}
  .ap-close{{background:none;border:none;color:rgba(255,255,255,0.8);font-size:1.3rem;cursor:pointer;line-height:1;}}
  .ap-body{{flex:1;padding:18px;overflow-y:auto;}}
  .ap-section{{margin-bottom:24px;}}
  .ap-section-title{{font-size:0.72rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:var(--orange);margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid var(--border);}}
  .form-row{{margin-bottom:12px;}}
  .form-label{{font-size:0.72rem;font-weight:600;color:var(--ch);margin-bottom:4px;display:block;}}
  .form-input,.form-select{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:0.8rem;font-family:'DM Sans',sans-serif;color:var(--ch);background:var(--white);transition:border-color 0.15s;}}
  .form-input:focus,.form-select:focus{{outline:none;border-color:var(--orange);}}
  .form-row-2{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;}}
  .btn-add{{width:100%;padding:9px;background:var(--orange);color:#fff;border:none;border-radius:6px;font-size:0.82rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;transition:background 0.15s;margin-top:4px;}}
  .btn-add:hover{{background:var(--od);}}
  .ev-list-item{{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;background:#FAFAFA;}}
  .ev-list-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:5px;}}
  .ev-list-dot.internal{{background:var(--orange);}}
  .ev-list-dot.public{{background:var(--blue);}}
  .ev-list-info{{flex:1;min-width:0;}}
  .ev-list-title{{font-size:0.78rem;font-weight:600;color:var(--ch);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .ev-list-meta{{font-size:0.68rem;color:var(--gray);}}
  .ev-list-del{{background:none;border:none;color:#CC3333;cursor:pointer;font-size:0.9rem;padding:2px 4px;border-radius:3px;flex-shrink:0;}}
  .ev-list-del:hover{{background:#FFF0F0;}}
  .ev-list-edit{{background:none;border:none;color:var(--blue);cursor:pointer;font-size:0.75rem;padding:2px 6px;border-radius:3px;flex-shrink:0;font-family:'DM Sans',sans-serif;}}
  .ev-list-edit:hover{{background:var(--blue-light);}}
  .ap-empty{{font-size:0.78rem;color:var(--gray);text-align:center;padding:20px 0;font-style:italic;}}
  .ap-notice{{font-size:0.7rem;color:var(--gray);background:#F3F4F6;border-radius:6px;padding:10px 12px;line-height:1.5;margin-top:8px;}}
  .ap-notice strong{{color:var(--ch);}}
  .month-filter-row{{display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap;}}
  .month-filter-row label{{font-size:0.72rem;font-weight:600;color:var(--ch);}}
  .month-filter-row select{{padding:4px 8px;border:1px solid var(--border);border-radius:5px;font-size:0.75rem;font-family:'DM Sans',sans-serif;}}
  footer{{border-top:1px solid var(--border);padding:16px 24px;text-align:center;font-size:0.7rem;color:var(--gray);}}
  footer a{{color:var(--orange);text-decoration:none;}}
  footer a:hover{{text-decoration:underline;}}
</style>
</head>
<body>
<header class="hdr">
  <div class="hi">
    <div class="eyebrow">Department of Medicine</div>
    <h1 class="htitle">Events Calendar</h1>
    <p class="hsub">Upcoming public and internal events. Subscribe to add all events to your Outlook calendar.</p>
  </div>
</header>
<div class="sub-bar">
  <div class="sub-inner">
    <div class="sub-row">
      <div class="sub-icon"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></div>
      <div class="sub-text">
        <div class="sub-lbl">Calendar Subscribe URL</div>
        <div class="sub-url" id="ics-url">https://michael-dean22.github.io/Dell_Med_Events/dell-med-events.ics</div>
      </div>
      <button class="copy-btn" id="copy-btn">Copy Link</button>
    </div>
    <div class="how-row">
      <span class="how-lbl">How to subscribe in Outlook</span>
      <div class="how-step"><div class="how-num">1</div><div class="how-text"><div class="how-title">Copy the URL</div><div class="how-desc">Click Copy Link above</div></div></div>
      <div class="how-step"><div class="how-num">2</div><div class="how-text"><div class="how-title">Open Account Settings</div><div class="how-desc">File → Account Settings → Account Settings</div></div></div>
      <div class="how-step"><div class="how-num">3</div><div class="how-text"><div class="how-title">Add Internet Calendar</div><div class="how-desc">Internet Calendars → New → paste URL → Add</div></div></div>
      <div class="how-step"><div class="how-num">4</div><div class="how-text"><div class="how-title">Done!</div><div class="how-desc">Events appear under Other Calendars</div></div></div>
    </div>
  </div>
</div>
<div class="toolbar">
  <div class="filters">
    <button class="fb on"          id="f-all"      onclick="setFilter('all')">All Events</button>
    <button class="fb on"          id="f-public"   onclick="setFilter('public')">🌐 Public</button>
    <button class="fb blue-btn on" id="f-internal" onclick="setFilter('internal')">🔒 Internal</button>
  </div>
  <div class="right-tools">
    <div class="legend">
      <div class="leg-item"><div class="leg-dot internal"></div> Internal</div>
      <div class="leg-item"><div class="leg-dot public"></div> Public</div>
    </div>
    <button class="admin-btn" id="admin-toggle">⚙ Edit Events</button>
    <div class="month-nav">
      <button class="nav-btn" id="prev-btn">&#8592;</button>
      <div class="month-display" id="month-display"></div>
      <button class="nav-btn" id="next-btn">&#8594;</button>
    </div>
  </div>
</div>
<div class="upd-note">Last updated: {updated}</div>
<div class="cal-wrap">
  <div class="cal-grid" id="cal-grid">
    <div class="day-header">Mon</div>
    <div class="day-header">Tue</div>
    <div class="day-header">Wed</div>
    <div class="day-header">Thu</div>
    <div class="day-header">Fri</div>
  </div>
</div>
<div class="popover" id="popover">
  <div class="pop-header">
    <div class="pop-date" id="pop-date"></div>
    <button class="pop-close" id="pop-close">✕</button>
  </div>
  <div class="pop-events" id="pop-events"></div>
</div>
<div class="admin-panel" id="admin-panel">
  <div class="ap-header">
    <div class="ap-title">Edit Events</div>
    <button class="ap-close" id="ap-close">✕</button>
  </div>
  <div class="ap-body">
    <div class="ap-section">
      <div class="ap-section-title" id="form-section-title">Add New Event</div>
      <input type="hidden" id="edit-index" value="-1">
      <div class="form-row"><label class="form-label">Event Title *</label><input class="form-input" id="f-title" type="text" placeholder="e.g. Grand Rounds: Topic Name"></div>
      <div class="form-row-2">
        <div><label class="form-label">Date *</label><input class="form-input" id="f-date" type="date"></div>
        <div><label class="form-label">Source *</label><select class="form-select" id="f-source"><option value="internal">🔒 Internal</option><option value="public">🌐 Public</option></select></div>
      </div>
      <div class="form-row"><label class="form-label">Time</label><input class="form-input" id="f-time" type="text" placeholder="e.g. 12:00pm – 1:00pm"></div>
      <div class="form-row"><label class="form-label">Location</label><input class="form-input" id="f-location" type="text" placeholder="e.g. Zoom, HDB 1.208"></div>
      <div class="form-row"><label class="form-label">URL</label><input class="form-input" id="f-url" type="url" placeholder="https://..."></div>
      <button class="btn-add" id="btn-submit">Add Event</button>
      <button class="btn-add" id="btn-cancel-edit" style="display:none;background:var(--gray);margin-top:6px;">Cancel Edit</button>
    </div>
    <div class="ap-section">
      <div class="ap-section-title">Manage Events</div>
      <div class="month-filter-row">
        <label>Filter by month:</label>
        <select id="ap-month-filter" onchange="renderAdminList()"><option value="all">All upcoming</option></select>
      </div>
      <div id="ap-ev-list"></div>
    </div>
    <div class="ap-notice"><strong>Note:</strong> Changes here update the calendar immediately on this page. To make them permanent, update the <code>EVENTS</code> array in the HTML file on GitHub and commit.</div>
  </div>
</div>
<footer>
  Department of Medicine · Dell Medical School · UT Austin &nbsp;|&nbsp;
  <a href="https://dellmed.utexas.edu/events" target="_blank">Public events →</a> &nbsp;|&nbsp;
  <a href="https://intranet.dellmed.utexas.edu/events" target="_blank">Internal events (login required) →</a>
</footer>
<script>
let EVENTS = {events_json};

const MONTHS_LONG=["January","February","March","April","May","June","July","August","September","October","November","December"];
const today=new Date();today.setHours(0,0,0,0);
let currentYear=today.getFullYear(),currentMonth=today.getMonth(),activeFilter='all';

function pd(s){{const[y,m,d]=s.split('-').map(Number);return new Date(y,m-1,d);}}
function filteredEvents(){{return EVENTS.filter(e=>activeFilter==='all'||(activeFilter==='public'&&e.source==='public')||(activeFilter==='internal'&&e.source==='internal'));}}
function eventsForDate(y,m,d){{const iso=`${{y}}-${{String(m+1).padStart(2,'0')}}-${{String(d).padStart(2,'0')}}`;return filteredEvents().filter(e=>e.date===iso).sort((a,b)=>a.time.localeCompare(b.time));}}

document.getElementById('copy-btn').addEventListener('click',function(){{
  navigator.clipboard.writeText(document.getElementById('ics-url').textContent).then(()=>{{
    this.textContent='✓ Copied!';this.classList.add('copied');
    setTimeout(()=>{{this.textContent='Copy Link';this.classList.remove('copied');}},2500);
  }});
}});

function setFilter(f){{
  activeFilter=f;
  document.getElementById('f-all').classList.toggle('on',f==='all');
  document.getElementById('f-public').classList.toggle('on',f==='all'||f==='public');
  document.getElementById('f-internal').classList.toggle('on',f==='all'||f==='internal');
  renderCalendar();
}}

document.getElementById('prev-btn').addEventListener('click',()=>{{currentMonth--;if(currentMonth<0){{currentMonth=11;currentYear--;}}renderCalendar();}});
document.getElementById('next-btn').addEventListener('click',()=>{{currentMonth++;if(currentMonth>11){{currentMonth=0;currentYear++;}}renderCalendar();}});

const popover=document.getElementById('popover');
document.getElementById('pop-close').addEventListener('click',()=>popover.classList.remove('show'));
document.addEventListener('click',e=>{{if(!popover.contains(e.target)&&!e.target.closest('.cal-cell'))popover.classList.remove('show');}});

function openPopover(cell,evs,label){{
  document.getElementById('pop-date').textContent=label;
  const pe=document.getElementById('pop-events');pe.innerHTML='';
  evs.forEach(ev=>{{
    const div=document.createElement('div');div.className='pop-ev';
    const dot=document.createElement('div');dot.className=`pop-dot ${{ev.source}}`;
    const info=document.createElement('div');info.className='pop-info';
    info.innerHTML=`<div class="pop-title"><a href="${{ev.url||'#'}}" target="_blank" rel="noopener">${{ev.title}}</a></div><div class="pop-meta">${{ev.time}}${{ev.location?' · '+ev.location:''}}</div><span class="pop-badge ${{ev.source}}">${{ev.source==='internal'?'Internal':'Public'}}</span>`;
    div.append(dot,info);pe.appendChild(div);
  }});
  popover.classList.add('show');
  const r=cell.getBoundingClientRect(),pw=popover.offsetWidth,ph=popover.offsetHeight;
  let left=r.left+window.scrollX,top=r.bottom+window.scrollY+4;
  if(left+pw>window.innerWidth-12)left=window.innerWidth-pw-12;
  if(top+ph>window.scrollY+window.innerHeight-12)top=r.top+window.scrollY-ph-4;
  popover.style.left=left+'px';popover.style.top=top+'px';
}}

function renderCalendar(){{
  const grid=document.getElementById('cal-grid');
  while(grid.children.length>5)grid.removeChild(grid.lastChild);
  document.getElementById('month-display').textContent=MONTHS_LONG[currentMonth]+' '+currentYear;

  const firstOfMonth=new Date(currentYear,currentMonth,1);
  const dow0=firstOfMonth.getDay();
  const offsetToMon=dow0===0?6:dow0-1;
  const gridStart=new Date(firstOfMonth);gridStart.setDate(gridStart.getDate()-offsetToMon);

  const lastOfMonth=new Date(currentYear,currentMonth+1,0);
  const dowLast=lastOfMonth.getDay();
  const offsetToFri=dowLast===6?6:dowLast===0?5:5-dowLast;
  const gridEnd=new Date(lastOfMonth);gridEnd.setDate(gridEnd.getDate()+offsetToFri);

  const cur=new Date(gridStart);
  while(cur<=gridEnd){{
    const dow=cur.getDay();
    if(dow!==0&&dow!==6){{
      const y=cur.getFullYear(),m=cur.getMonth(),d=cur.getDate();
      const cell=document.createElement('div');cell.className='cal-cell';
      if(m!==currentMonth||y!==currentYear)cell.classList.add('other-month');
      const cd=new Date(y,m,d);cd.setHours(0,0,0,0);
      if(cd.getTime()===today.getTime())cell.classList.add('today');
      const numEl=document.createElement('div');numEl.className='cell-num';numEl.textContent=d;cell.appendChild(numEl);
      const dayEvs=eventsForDate(y,m,d);
      dayEvs.slice(0,3).forEach(ev=>{{
        const pill=document.createElement('a');pill.className=`ev-pill ${{ev.source}}`;
        pill.href=ev.url||'#';pill.target='_blank';pill.rel='noopener noreferrer';
        pill.textContent=ev.title;pill.title=ev.title+'\n'+ev.time+(ev.location?' · '+ev.location:'');
        pill.addEventListener('click',e=>e.stopPropagation());cell.appendChild(pill);
      }});
      if(dayEvs.length>3){{
        const more=document.createElement('span');more.className='ev-pill more';
        more.textContent=`+${{dayEvs.length-3}} more`;
        more.addEventListener('click',e=>{{e.stopPropagation();const d2=new Date(y,m,d);const lbl=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][d2.getDay()]+', '+MONTHS_LONG[m]+' '+d+', '+y;openPopover(cell,dayEvs,lbl);}});
        cell.appendChild(more);
      }}
      if(dayEvs.length>0){{
        cell.style.cursor='pointer';
        cell.addEventListener('click',()=>{{const d2=new Date(y,m,d);const lbl=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][d2.getDay()]+', '+MONTHS_LONG[m]+' '+d+', '+y;openPopover(cell,dayEvs,lbl);}});
      }}
      grid.appendChild(cell);
    }}
    cur.setDate(cur.getDate()+1);
  }}
}}

const adminPanel=document.getElementById('admin-panel');
document.getElementById('admin-toggle').addEventListener('click',()=>{{adminPanel.classList.toggle('open');if(adminPanel.classList.contains('open'))populateMonthFilter();}});
document.getElementById('ap-close').addEventListener('click',()=>adminPanel.classList.remove('open'));

function populateMonthFilter(){{
  const sel=document.getElementById('ap-month-filter'),cur=sel.value;
  sel.innerHTML='<option value="all">All upcoming</option>';
  const months=new Set(),t=new Date();t.setHours(0,0,0,0);
  EVENTS.filter(e=>pd(e.date)>=t).forEach(e=>{{const[y,m]=e.date.split('-');months.add(y+'-'+m);}});
  [...months].sort().forEach(ym=>{{const[y,m]=ym.split('-');const opt=document.createElement('option');opt.value=ym;opt.textContent=MONTHS_LONG[parseInt(m)-1]+' '+y;sel.appendChild(opt);}});
  sel.value=cur;renderAdminList();
}}

function renderAdminList(){{
  const list=document.getElementById('ap-ev-list'),filter=document.getElementById('ap-month-filter').value;
  const t=new Date();t.setHours(0,0,0,0);
  let evs=EVENTS.map((e,i)=>( {{...e,_i:i}} )).filter(e=>pd(e.date)>=t).sort((a,b)=>a.date.localeCompare(b.date)||a.time.localeCompare(b.time));
  if(filter!=='all')evs=evs.filter(e=>e.date.startsWith(filter));
  list.innerHTML='';
  if(!evs.length){{list.innerHTML='<div class="ap-empty">No upcoming events found.</div>';return;}}
  evs.forEach(ev=>{{
    const item=document.createElement('div');item.className='ev-list-item';
    const dot=document.createElement('div');dot.className=`ev-list-dot ${{ev.source}}`;
    const info=document.createElement('div');info.className='ev-list-info';
    info.innerHTML=`<div class="ev-list-title">${{ev.title}}</div><div class="ev-list-meta">${{ev.date}} · ${{ev.time}}${{ev.location?' · '+ev.location:''}}</div>`;
    const editBtn=document.createElement('button');editBtn.className='ev-list-edit';editBtn.textContent='Edit';editBtn.addEventListener('click',()=>startEdit(ev._i));
    const delBtn=document.createElement('button');delBtn.className='ev-list-del';delBtn.textContent='✕';delBtn.addEventListener('click',()=>deleteEvent(ev._i));
    item.append(dot,info,editBtn,delBtn);list.appendChild(item);
  }});
}}

function startEdit(idx){{
  const ev=EVENTS[idx];
  document.getElementById('edit-index').value=idx;
  document.getElementById('f-title').value=ev.title;
  document.getElementById('f-date').value=ev.date;
  document.getElementById('f-time').value=ev.time;
  document.getElementById('f-location').value=ev.location||'';
  document.getElementById('f-url').value=ev.url||'';
  document.getElementById('f-source').value=ev.source;
  document.getElementById('form-section-title').textContent='Edit Event';
  document.getElementById('btn-submit').textContent='Save Changes';
  document.getElementById('btn-cancel-edit').style.display='block';
  adminPanel.querySelector('.ap-body').scrollTop=0;
}}

function cancelEdit(){{
  document.getElementById('edit-index').value='-1';
  ['f-title','f-date','f-time','f-location','f-url'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f-source').value='internal';
  document.getElementById('form-section-title').textContent='Add New Event';
  document.getElementById('btn-submit').textContent='Add Event';
  document.getElementById('btn-cancel-edit').style.display='none';
}}

document.getElementById('btn-cancel-edit').addEventListener('click',cancelEdit);
document.getElementById('btn-submit').addEventListener('click',()=>{{
  const title=document.getElementById('f-title').value.trim();
  const date=document.getElementById('f-date').value;
  if(!title||!date){{alert('Title and Date are required.');return;}}
  const ev={{title,date,time:document.getElementById('f-time').value.trim()||'',location:document.getElementById('f-location').value.trim()||'',url:document.getElementById('f-url').value.trim()||'',source:document.getElementById('f-source').value}};
  const idx=parseInt(document.getElementById('edit-index').value);
  if(idx>=0){{EVENTS[idx]=ev;}}else{{EVENTS.push(ev);}}
  cancelEdit();renderCalendar();populateMonthFilter();
}});

function deleteEvent(idx){{
  if(!confirm('Delete "'+EVENTS[idx].title+'"?'))return;
  EVENTS.splice(idx,1);renderCalendar();populateMonthFilter();
}}

renderCalendar();
</script>
</body>
</html>"""


def main():
    # 1. Public events from RSS scraping
    public_events = fetch_public_events()

    # 2. Internal events from index.html ticker
    print("\n📋 Extracting internal events from index.html...")
    internal_events = extract_internal_events_from_ticker()

    # 3. Manual overrides from JSON file
    print("\n📝 Loading manual overrides...")
    manual_events = load_manual_overrides()
    for ev in manual_events:
        ev.setdefault("source", "internal")

    # Combine all sources — manual overrides take priority (go last, dedup by title+date)
    all_events = public_events + internal_events + manual_events

    # Deduplicate by (title, date_str) — keep last occurrence (manual wins)
    seen = {}
    for ev in all_events:
        key = (ev["title"].strip().lower(), (ev.get("date_str") or "").strip())
        seen[key] = ev
    combined = list(seen.values())

    print(f"\n📊 Total combined events: {len(combined)}")
    print(f"   Public: {sum(1 for e in combined if e.get('source')=='public')}")
    print(f"   Internal: {sum(1 for e in combined if e.get('source')=='internal')}")

    # 4. Build ICS
    print("\n📅 Building ICS calendar...")
    with open(ICS_FILE, "w", encoding="utf-8") as f:
        f.write(build_ics(combined))
    print(f"✅ Saved {ICS_FILE}")

    # 5. Build calendar.html
    print("\n🌐 Building calendar.html...")
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(build_calendar_html(combined))
    print(f"✅ Saved {HTML_FILE}")

    print(f"\n   Live at:")
    print(f"   {GITHUB_BASE}/dell-med-events.ics")
    print(f"   {GITHUB_BASE}/calendar.html")


if __name__ == "__main__":
    main()
