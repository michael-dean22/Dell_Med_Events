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
GITHUB_BASE   = "https://michael-dean22.github.io/Dell_Med_Internal_Events"
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
    """Read RAW_EVENTS from index.html and convert to standard event dicts."""
    try:
        with open(TICKER_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print("  ⚠ index.html not found — skipping internal events")
        return []

    # Extract the RAW_EVENTS array from the JS
    m = re.search(r'const RAW_EVENTS\s*=\s*(\[[\s\S]*?\]);', content)
    if not m:
        print("  ⚠ Could not find RAW_EVENTS in index.html")
        return []

    raw_js = m.group(1)

    # Convert JS object literals to JSON:
    # 1. Add quotes around unquoted keys
    raw_js = re.sub(r'(\{|,)\s*(\w+)\s*:', r'\1"\2":', raw_js)
    # 2. Replace single quotes with double quotes for values
    raw_js = re.sub(r"'([^']*)'", r'"\1"', raw_js)
    # 3. Remove trailing commas before ] or }
    raw_js = re.sub(r',\s*([\]}])', r'\1', raw_js)

    try:
        items = json.loads(raw_js)
    except json.JSONDecodeError as e:
        print(f"  ⚠ Could not parse RAW_EVENTS JSON: {e}")
        return []

    events = []
    for item in items:
        # Convert ticker format {date, time, title, url, location}
        # to standard format {date_str, time_str, title, url, location, source}
        date_val = item.get("date","")
        # date is already YYYY-MM-DD — convert to "Month DD, YYYY" for parse_date_time
        try:
            y, mo, d = date_val.split("-")
            month_names = ["","January","February","March","April","May","June",
                           "July","August","September","October","November","December"]
            date_str = f"{month_names[int(mo)]} {int(d)}, {y}"
        except Exception:
            date_str = date_val

        events.append({
            "title":       item.get("title",""),
            "url":         item.get("url",""),
            "date_str":    date_str,
            "time_str":    item.get("time",""),
            "location":    item.get("location",""),
            "description": f"Internal Dell Med event\\n\\nEvent page: {item.get('url','')}",
            "source":      "internal",
        })

    print(f"   ✓ {len(events)} internal events extracted from index.html")
    return events


# ── Source 3: Manual override file ──────────────────────────────────────────

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
    date_iso = f"{int(year):04d}-{month:02d}-{int(day):02d}"
    return {
        "date":     date_iso,
        "time":     ev.get("time_str",""),
        "title":    ev["title"],
        "location": ev.get("location",""),
        "url":      ev["url"],
        "source":   ev.get("source","public"),
    }


def build_calendar_html(events):
    updated    = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
    ics_url    = f"{GITHUB_BASE}/dell-med-events.ics"
    ev_list    = []
    skipped    = 0

    for ev in events:
        d = ev_to_html_json(ev)
        if d:
            ev_list.append(d)
        else:
            skipped += 1

    print(f"  → HTML: {len(ev_list)} events included, {skipped} skipped (no date)")
    events_json = json.dumps(ev_list, indent=2, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dell Med Events Calendar</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{--orange:#BF5700;--orange-dark:#8C3E00;--orange-light:#FDF0E6;--cream:#FDFAF6;--charcoal:#1A1A1A;--gray:#6B6B6B;--border:#E5D8CC;--white:#FFFFFF;--internal:#E8F4FD;--internal-accent:#1a73e8;}}
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:'DM Sans',sans-serif;background:var(--cream);color:var(--charcoal);min-height:100vh;}}
  .site-header{{background:var(--orange);position:relative;overflow:hidden;}}
  .site-header::before{{content:'';position:absolute;top:-60px;right:-60px;width:300px;height:300px;border-radius:50%;background:rgba(255,255,255,0.06);}}
  .header-inner{{max-width:960px;margin:0 auto;padding:36px 24px 32px;position:relative;z-index:1;}}
  .header-eyebrow{{font-size:0.7rem;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:rgba(255,255,255,0.65);margin-bottom:8px;}}
  .header-title{{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,2.6rem);font-weight:700;color:#fff;line-height:1.15;margin-bottom:10px;}}
  .header-sub{{font-size:0.9rem;color:rgba(255,255,255,0.75);font-weight:300;max-width:520px;}}
  .subscribe-section{{background:var(--orange-dark);border-bottom:3px solid rgba(0,0,0,0.15);}}
  .subscribe-inner{{max-width:960px;margin:0 auto;padding:20px 24px 0;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
  .subscribe-icon{{width:36px;height:36px;background:rgba(255,255,255,0.15);border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}}
  .subscribe-text{{flex:1;min-width:200px;}}
  .subscribe-label{{font-size:0.7rem;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.6);margin-bottom:2px;}}
  .subscribe-url{{font-family:monospace;font-size:0.78rem;color:#fff;word-break:break-all;}}
  .copy-btn{{background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);color:#fff;padding:8px 16px;border-radius:6px;font-size:0.78rem;font-weight:500;cursor:pointer;font-family:'DM Sans',sans-serif;transition:background 0.2s;white-space:nowrap;flex-shrink:0;}}
  .copy-btn:hover{{background:rgba(255,255,255,0.25);}}
  .copy-btn.copied{{background:rgba(127,219,138,0.3);border-color:#7FDB8A;}}
  .how-to{{max-width:960px;margin:0 auto;padding:18px 24px 20px;}}
  .how-to-title{{font-size:0.68rem;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:rgba(255,255,255,0.55);margin-bottom:12px;}}
  .how-to-steps{{display:flex;gap:0;flex-wrap:wrap;}}
  .how-step{{display:flex;align-items:flex-start;gap:8px;flex:1;min-width:160px;padding:0 16px 0 0;position:relative;}}
  .how-step:not(:last-child)::after{{content:'→';position:absolute;right:4px;top:2px;color:rgba(255,255,255,0.3);font-size:0.8rem;}}
  .how-step-num{{width:20px;height:20px;border-radius:50%;background:rgba(255,255,255,0.2);color:#fff;font-size:0.65rem;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;}}
  .how-step-text{{flex:1;}}
  .how-step-title{{font-size:0.72rem;font-weight:600;color:#fff;margin-bottom:2px;}}
  .how-step-desc{{font-size:0.65rem;color:rgba(255,255,255,0.65);line-height:1.4;}}
  .filter-bar{{max-width:960px;margin:0 auto;padding:16px 24px 0;display:flex;gap:8px;flex-wrap:wrap;}}
  .filter-btn{{padding:5px 14px;border-radius:20px;border:1px solid var(--border);background:var(--white);font-size:0.72rem;font-weight:500;cursor:pointer;font-family:'DM Sans',sans-serif;color:var(--gray);transition:all 0.15s;}}
  .filter-btn.active{{background:var(--orange);color:#fff;border-color:var(--orange);}}
  .filter-btn.internal-btn.active{{background:var(--internal-accent);border-color:var(--internal-accent);}}
  .main{{max-width:960px;margin:0 auto;padding:24px 24px 60px;display:grid;grid-template-columns:1fr 320px;gap:36px;align-items:start;}}
  @media(max-width:720px){{.main{{grid-template-columns:1fr;}}.sidebar{{order:-1;}}}}
  .events-header{{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:20px;border-bottom:2px solid var(--orange);padding-bottom:10px;}}
  .events-title{{font-family:'Playfair Display',serif;font-size:1.3rem;font-weight:600;}}
  .events-count{{font-size:0.72rem;color:var(--gray);}}
  .updated-note{{font-size:0.65rem;color:var(--gray);margin-bottom:16px;font-style:italic;}}
  .month-group{{margin-bottom:32px;}}
  .month-label{{font-size:0.68rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--orange);margin-bottom:10px;padding-left:4px;}}
  .event-card{{display:flex;background:var(--white);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:10px;text-decoration:none;color:inherit;transition:box-shadow 0.2s,transform 0.2s;}}
  .event-card.internal{{background:#F0F7FF;border-color:#C5DCEF;}}
  .event-card:hover{{box-shadow:0 4px 20px rgba(191,87,0,0.12);transform:translateY(-1px);}}
  .event-card.internal:hover{{box-shadow:0 4px 20px rgba(26,115,232,0.12);}}
  .event-date-col{{width:56px;flex-shrink:0;background:var(--orange-light);border-right:1px solid var(--border);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:12px 6px;text-align:center;}}
  .event-card.internal .event-date-col{{background:#D6EAFF;border-right-color:#C5DCEF;}}
  .event-date-col.today-col{{background:var(--orange)!important;}}
  .ev-month{{font-size:0.6rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:var(--orange);line-height:1;display:block;}}
  .event-card.internal .ev-month{{color:var(--internal-accent);}}
  .today-col .ev-month{{color:rgba(255,255,255,0.8)!important;}}
  .ev-day{{font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:700;color:var(--charcoal);line-height:1.1;display:block;}}
  .today-col .ev-day{{color:#fff;}}
  .ev-dow{{font-size:0.55rem;color:var(--gray);text-transform:uppercase;letter-spacing:0.06em;display:block;}}
  .today-col .ev-dow{{color:rgba(255,255,255,0.7);}}
  .event-body{{flex:1;padding:12px 14px;min-width:0;}}
  .ev-title{{font-size:0.88rem;font-weight:600;color:var(--charcoal);line-height:1.35;margin-bottom:5px;}}
  .event-card:hover .ev-title{{color:var(--orange);}}
  .event-card.internal:hover .ev-title{{color:var(--internal-accent);}}
  .ev-meta{{font-size:0.72rem;color:var(--gray);display:flex;flex-wrap:wrap;gap:8px;}}
  .ev-meta-item{{display:flex;align-items:center;gap:4px;}}
  .source-badge{{display:inline-block;font-size:0.55rem;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;padding:2px 6px;border-radius:3px;margin-left:6px;vertical-align:middle;}}
  .badge-internal{{background:#1a73e8;color:#fff;}}
  .badge-today{{background:var(--orange);color:#fff;}}
  .ev-arrow{{display:flex;align-items:center;padding:0 12px;color:var(--border);flex-shrink:0;transition:color 0.2s;}}
  .event-card:hover .ev-arrow{{color:var(--orange);}}
  .event-card.internal:hover .ev-arrow{{color:var(--internal-accent);}}
  .sidebar-card{{background:var(--white);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:20px;}}
  .sidebar-card-header{{background:var(--orange-light);border-bottom:1px solid var(--border);padding:14px 18px;display:flex;align-items:center;gap:8px;}}
  .sidebar-card-title{{font-family:'Playfair Display',serif;font-size:1rem;font-weight:600;color:var(--orange-dark);}}
  .sidebar-card-body{{padding:18px;}}
  .step{{display:flex;gap:12px;margin-bottom:18px;}}
  .step:last-child{{margin-bottom:0;}}
  .step-num{{width:24px;height:24px;border-radius:50%;background:var(--orange);color:#fff;font-size:0.7rem;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;}}
  .step-content{{flex:1;}}
  .step-title{{font-size:0.82rem;font-weight:600;color:var(--charcoal);margin-bottom:3px;}}
  .step-desc{{font-size:0.75rem;color:var(--gray);line-height:1.5;}}
  .step-desc code{{background:var(--orange-light);color:var(--orange-dark);padding:1px 5px;border-radius:3px;font-size:0.7rem;font-family:monospace;}}
  .platform-tab{{display:flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;margin-bottom:14px;}}
  .tab-btn{{flex:1;padding:7px 6px;font-size:0.68rem;font-weight:500;background:var(--white);border:none;cursor:pointer;font-family:'DM Sans',sans-serif;color:var(--gray);border-right:1px solid var(--border);}}
  .tab-btn:last-child{{border-right:none;}}
  .tab-btn.active{{background:var(--orange);color:#fff;}}
  .tab-panel{{display:none;}}
  .tab-panel.active{{display:block;}}
  .info-note{{background:var(--orange-light);border-left:3px solid var(--orange);border-radius:0 6px 6px 0;padding:10px 12px;font-size:0.74rem;color:var(--orange-dark);line-height:1.5;margin-top:14px;}}
  .divider{{height:1px;background:var(--border);margin:14px 0;}}
  .site-footer{{border-top:1px solid var(--border);padding:20px 24px;text-align:center;font-size:0.72rem;color:var(--gray);}}
  .site-footer a{{color:var(--orange);text-decoration:none;}}
  .site-footer a:hover{{text-decoration:underline;}}
  .no-events{{text-align:center;padding:40px;color:var(--gray);font-size:0.9rem;}}
</style>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <div class="header-eyebrow">Department of Medicine</div>
    <h1 class="header-title">Events Calendar</h1>
    <p class="header-sub">Upcoming public and internal events from Dell Med. Subscribe to add all events to your Outlook calendar.</p>
  </div>
</header>
<div class="subscribe-section">
  <div class="subscribe-inner">
    <div class="subscribe-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></div>
    <div class="subscribe-text">
      <div class="subscribe-label">Calendar Subscribe URL</div>
      <div class="subscribe-url" id="ics-url">{ics_url}</div>
    </div>
    <button class="copy-btn" id="copy-btn">Copy Link</button>
  </div>
  <div class="how-to">
    <div class="how-to-title">How to subscribe in Outlook</div>
    <div class="how-to-steps">
      <div class="how-step"><div class="how-step-num">1</div><div class="how-step-text"><div class="how-step-title">Copy the URL</div><div class="how-step-desc">Click Copy Link above</div></div></div>
      <div class="how-step"><div class="how-step-num">2</div><div class="how-step-text"><div class="how-step-title">Open Account Settings</div><div class="how-step-desc">File → Account Settings → Account Settings</div></div></div>
      <div class="how-step"><div class="how-step-num">3</div><div class="how-step-text"><div class="how-step-title">Add Internet Calendar</div><div class="how-step-desc">Internet Calendars tab → New → paste URL → Add</div></div></div>
      <div class="how-step"><div class="how-step-num">4</div><div class="how-step-text"><div class="how-step-title">Done!</div><div class="how-step-desc">Events appear under Other Calendars and sync daily</div></div></div>
    </div>
  </div>
</div>
<div class="filter-bar">
  <button class="filter-btn active" onclick="setFilter('all')" id="filter-all">All Events</button>
  <button class="filter-btn active" onclick="setFilter('public')" id="filter-public">🌐 Public</button>
  <button class="filter-btn internal-btn active" onclick="setFilter('internal')" id="filter-internal">🔒 Internal</button>
</div>
<div class="main">
  <div class="events-col">
    <div class="events-header">
      <h2 class="events-title">Upcoming Events</h2>
      <span class="events-count" id="events-count"></span>
    </div>
    <div class="updated-note">Last updated: {updated}</div>
    <div id="events-container"></div>
  </div>
  <aside class="sidebar">
    <div class="sidebar-card">
      <div class="sidebar-card-header"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8C3E00" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg><div class="sidebar-card-title">Event Sources</div></div>
      <div class="sidebar-card-body">
        <div style="font-size:0.78rem;color:var(--gray);line-height:1.6;">
          <div style="margin-bottom:8px;display:flex;gap:8px;align-items:flex-start;"><span style="color:var(--orange);font-weight:700;flex-shrink:0;">🌐</span><span><strong style="color:var(--charcoal);">Public Events</strong><br>dellmed.utexas.edu/events — auto-updated daily</span></div>
          <div class="divider"></div>
          <div style="display:flex;gap:8px;align-items:flex-start;"><span style="color:#1a73e8;font-weight:700;flex-shrink:0;">🔒</span><span><strong style="color:var(--charcoal);">Internal Events</strong><br>Cerebrum intranet — updated when ticker is refreshed</span></div>
        </div>
      </div>
    </div>
  </aside>
</div>
<footer class="site-footer">
  <p>Dell Medical School · The University of Texas at Austin &nbsp;|&nbsp;
  <a href="https://dellmed.utexas.edu/events" target="_blank">Public events →</a> &nbsp;|&nbsp;
  <a href="https://intranet.dellmed.utexas.edu/events" target="_blank">Internal events (login required) →</a></p>
</footer>
<script>
const EVENTS = {events_json};

const DAYS  = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MS    = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const ML    = ["January","February","March","April","May","June","July","August","September","October","November","December"];

let activeFilter = 'all';

document.getElementById('copy-btn').addEventListener('click', function(){{
  navigator.clipboard.writeText(document.getElementById('ics-url').textContent).then(()=>{{
    this.textContent='✓ Copied!'; this.classList.add('copied');
    setTimeout(()=>{{ this.textContent='Copy Link'; this.classList.remove('copied'); }}, 2500);
  }});
}});

function setFilter(f){{
  activeFilter = f;
  ['all','public','internal'].forEach(id=>{{
    document.getElementById('filter-'+id).classList.toggle('active', f===id||f==='all'||(f!=='all'&&id==='all'&&false));
  }});
  document.getElementById('filter-all').classList.toggle('active', f==='all');
  document.getElementById('filter-public').classList.toggle('active', f==='all'||f==='public');
  document.getElementById('filter-internal').classList.toggle('active', f==='all'||f==='internal');
  render();
}}

function render(){{
  const today = new Date(); today.setHours(0,0,0,0);
  const todayStr = today.toDateString();

  function pd(s){{ const[y,m,d]=s.split('-').map(Number); return new Date(y,m-1,d); }}

  const upcoming = EVENTS
    .map(e=>( {{...e, dt:pd(e.date)}} ))
    .filter(e=>{{
      if (e.dt < today) return false;
      if (activeFilter === 'public')   return e.source !== 'internal';
      if (activeFilter === 'internal') return e.source === 'internal';
      return true;
    }})
    .sort((a,b)=>a.dt-b.dt);

  document.getElementById('events-count').textContent =
    upcoming.length + ' upcoming event' + (upcoming.length!==1?'s':'');

  const groups={{}};
  for(const ev of upcoming){{
    const k=ev.dt.getFullYear()+'-'+ev.dt.getMonth();
    if(!groups[k]) groups[k]={{label:ML[ev.dt.getMonth()]+' '+ev.dt.getFullYear(),events:[]}};
    groups[k].events.push(ev);
  }}

  const c = document.getElementById('events-container');
  c.innerHTML = '';
  if(!upcoming.length){{c.innerHTML='<div class="no-events">No upcoming events found.</div>';return;}}

  const arrowSVG='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
  const clockSVG='<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
  const pinSVG='<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';

  for(const g of Object.values(groups)){{
    const ge=document.createElement('div'); ge.className='month-group';
    const le=document.createElement('div'); le.className='month-label'; le.textContent=g.label;
    ge.appendChild(le);

    for(const ev of g.events){{
      const isToday=ev.dt.toDateString()===todayStr;
      const isInternal=ev.source==='internal';

      const card=document.createElement('a');
      card.className='event-card'+(isInternal?' internal':'');
      card.href=ev.url; card.target='_blank'; card.rel='noopener noreferrer';

      const dc=document.createElement('div');
      dc.className='event-date-col'+(isToday?' today-col':'');
      const mo=document.createElement('span'); mo.className='ev-month'; mo.textContent=MS[ev.dt.getMonth()];
      const da=document.createElement('span'); da.className='ev-day';   da.textContent=ev.dt.getDate();
      const dw=document.createElement('span'); dw.className='ev-dow';   dw.textContent=DAYS[ev.dt.getDay()];
      dc.append(mo,da,dw);

      const body=document.createElement('div'); body.className='event-body';
      const title=document.createElement('div'); title.className='ev-title'; title.textContent=ev.title;
      if(isInternal){{ const b=document.createElement('span'); b.className='source-badge badge-internal'; b.textContent='Internal'; title.appendChild(b); }}
      if(isToday){{    const b=document.createElement('span'); b.className='source-badge badge-today';    b.textContent='Today';    title.appendChild(b); }}

      const meta=document.createElement('div'); meta.className='ev-meta';
      if(ev.time){{    const t=document.createElement('span'); t.className='ev-meta-item'; t.innerHTML=clockSVG+' '+ev.time;     meta.appendChild(t); }}
      if(ev.location){{const l=document.createElement('span'); l.className='ev-meta-item'; l.innerHTML=pinSVG+' '+ev.location;  meta.appendChild(l); }}

      body.append(title, meta);
      const arrow=document.createElement('div'); arrow.className='ev-arrow'; arrow.innerHTML=arrowSVG;
      card.append(dc, body, arrow);
      ge.appendChild(card);
    }}
    c.appendChild(ge);
  }}
}}

render();
</script>
</body>
</html>"""


# ── Main ─────────────────────────────────────────────────────────────────────

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
