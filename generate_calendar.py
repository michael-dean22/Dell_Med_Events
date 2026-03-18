"""
Dell Med Events Calendar Generator
-----------------------------------
Fetches the RSS.app feed, visits each event page to scrape full details
(date, time, location, description, Zoom/Teams links), then outputs
a standards-compliant ICS file that Outlook and other calendar apps
can subscribe to.
"""

import re
import uuid
import pytz
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
RSS_URL      = "https://rss.app/feeds/UXQywtXV9kG72UyK.xml"
OUTPUT_FILE  = "dell-med-events.ics"
TIMEZONE     = "America/Chicago"
CALENDAR_NAME = "Dell Med Events"
CALENDAR_DESC = "Events from Dell Medical School at UT Austin"
# ─────────────────────────────────────────────────────────────────────────────

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12
}

tz = pytz.timezone(TIMEZONE)


def fetch_rss_links(url):
    """Fetch RSS feed and return list of (title, link) tuples."""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "lxml-xml")
    items = []
    for item in soup.find_all("item"):
        title = item.find("title")
        link  = item.find("link")
        if title and link:
            items.append((title.get_text(strip=True), link.get_text(strip=True)))
    return items


def parse_date_time(date_str, time_str):
    """
    Parse date like 'Tuesday, April 14, 2026'
    and time like '12–1 p.m.' or '8–9 a.m.' or 'All Day'
    Returns (dtstart, dtend, all_day: bool)
    """
    # Clean up date string
    date_str = date_str.strip()
    # Remove day-of-week if present
    if "," in date_str:
        parts = date_str.split(",")
        if len(parts) == 3:
            # "Tuesday, April 14, 2026"
            date_str = (parts[1] + "," + parts[2]).strip()
        else:
            date_str = (parts[0] + "," + parts[1]).strip()

    # Parse month day year
    match = re.match(r'(\w+)\s+(\d+),?\s+(\d{4})', date_str)
    if not match:
        return None, None, False
    month_name, day, year = match.groups()
    month = MONTH_MAP.get(month_name.lower())
    if not month:
        return None, None, False
    date = datetime(int(year), month, int(day))

    # Handle All Day
    time_str = time_str.strip() if time_str else ""
    if not time_str or time_str.lower() == "all day":
        return date.date(), (date + timedelta(days=1)).date(), True

    # Normalize time string: replace en-dash, em-dash with hyphen
    time_str = time_str.replace("–", "-").replace("—", "-")

    # Parse time range like "12-1 p.m." or "8-9 a.m." or "8 a.m.-5 p.m."
    # Extract all time-like tokens
    time_pattern = re.compile(
        r'(\d{1,2}(?::\d{2})?)\s*([ap]\.?m\.?)',
        re.IGNORECASE
    )
    times = time_pattern.findall(time_str)

    def to_24h(t, meridiem):
        meridiem = meridiem.replace(".", "").lower()
        if ":" in t:
            h, m = map(int, t.split(":"))
        else:
            h, m = int(t), 0
        if meridiem == "pm" and h != 12:
            h += 12
        if meridiem == "am" and h == 12:
            h = 0
        return h, m

    if len(times) >= 2:
        sh, sm = to_24h(*times[0])
        eh, em = to_24h(*times[1])
    elif len(times) == 1:
        # Only start time found — assume 1 hour duration
        sh, sm = to_24h(*times[0])
        eh, em = sh + 1, sm
    else:
        # Can't parse — use midnight + 1 hour
        sh, sm, eh, em = 0, 0, 1, 0

    # Handle case where only end meridiem is given e.g. "12-1 p.m."
    # (already handled above since regex finds both numbers)

    dtstart = tz.localize(datetime(int(year), month, int(day), sh, sm))
    dtend   = tz.localize(datetime(int(year), month, int(day), eh, em))
    # If end is before start (e.g. 11pm-1am), add a day to end
    if dtend <= dtstart:
        dtend += timedelta(hours=1)
    return dtstart, dtend, False


def find_zoom_teams_links(soup):
    """Extract any Zoom or Teams registration/join links from the page."""
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if any(kw in href.lower() for kw in ["zoom.us", "teams.microsoft", "events.teams"]):
            links.append((text or "Join Meeting", href))
        elif any(kw in text.lower() for kw in ["register", "zoom", "teams", "join"]):
            if href.startswith("http"):
                links.append((text, href))
    # Deduplicate
    seen = set()
    unique = []
    for text, href in links:
        if href not in seen:
            seen.add(href)
            unique.append((text, href))
    return unique


def scrape_event_page(url):
    """Scrape a single event page and return a dict of event details."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠ Could not fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    # Find the main content area
    # Date, Time, Location are in plain text near the top of the event body
    date_str     = ""
    time_str     = ""
    location_str = ""

    # Look for labeled fields
    page_text = soup.get_text("\n")
    for line in page_text.split("\n"):
        line = line.strip()
        if line.lower().startswith("date:"):
            date_str = line[5:].strip()
        elif line.lower().startswith("time:"):
            time_str = line[5:].strip()
        elif line.lower().startswith("location:"):
            location_str = line[9:].strip()

    # Description — grab the "About the Event" section
    description = ""
    about_header = soup.find(lambda tag: tag.name in ["h2","h3"] and
                             "about" in tag.get_text(strip=True).lower())
    if about_header:
        # Collect text from siblings until next header
        parts = []
        for sib in about_header.find_next_siblings():
            if sib.name in ["h2", "h3"]:
                break
            text = sib.get_text(" ", strip=True)
            if text:
                parts.append(text)
        description = " ".join(parts)
    else:
        # Fallback: grab first substantial paragraph
        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) > 60:
                description = text
                break

    # Zoom/Teams links
    meeting_links = find_zoom_teams_links(soup)

    # Build description field for ICS
    ics_description_parts = []
    if description:
        ics_description_parts.append(description)
    if meeting_links:
        ics_description_parts.append("")
        for link_text, link_href in meeting_links:
            ics_description_parts.append(f"{link_text}: {link_href}")
    ics_description_parts.append("")
    ics_description_parts.append(f"Event page: {url}")
    ics_description = "\\n".join(ics_description_parts)

    return {
        "title":       title,
        "url":         url,
        "date_str":    date_str,
        "time_str":    time_str,
        "location":    location_str,
        "description": ics_description,
    }


def ics_escape(text):
    """Escape special characters for ICS text fields."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    # Newlines already encoded as \\n above
    return text


def fold_line(line):
    """Fold long ICS lines at 75 characters per RFC 5545."""
    result = []
    while len(line.encode("utf-8")) > 75:
        result.append(line[:75])
        line = " " + line[75:]
    result.append(line)
    return "\r\n".join(result)


def build_ics(events):
    """Build a complete ICS file string from a list of event dicts."""
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Dell Medical School//Dell Med Events//EN",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-CALDESC:{CALENDAR_DESC}",
        "X-WR-TIMEZONE:America/Chicago",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for ev in events:
        dtstart, dtend, all_day = parse_date_time(ev["date_str"], ev["time_str"])
        if dtstart is None:
            print(f"  ⚠ Could not parse date for: {ev['title']} — skipping")
            continue

        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, ev["url"]))

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now}")

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
    return "\r\n".join(lines) + "\r\n"


def build_calendar_html(events):
    """Generate calendar.html with events baked in as JSON — no fetch required."""
    import json

    DAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
    MONTHS_SHORT = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    MONTHS_LONG  = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"]

    # Build JSON-serializable event list
    ev_list = []
    for ev in events:
        dtstart, dtend, all_day = parse_date_time(ev["date_str"], ev["time_str"])
        if dtstart is None:
            continue
        if all_day:
            # Use noon UTC to avoid any timezone-shifting the date backward
            start_iso = f"{dtstart.year}-{dtstart.month:02d}-{dtstart.day:02d}T12:00:00"
            end_iso   = f"{dtend.year}-{dtend.month:02d}-{dtend.day:02d}T12:00:00"
        else:
            start_iso = dtstart.strftime("%Y-%m-%dT%H:%M:%S")
            end_iso   = dtend.strftime("%Y-%m-%dT%H:%M:%S") if dtend else None

        # Extract clean description (first paragraph only for display)
        desc_lines = ev.get("description","").replace("\\n","\n").split("\n")
        desc_short = next((l for l in desc_lines if len(l) > 40), "")

        ev_list.append({
            "title":    ev["title"],
            "url":      ev["url"],
            "location": ev.get("location",""),
            "start":    start_iso,
            "end":      end_iso,
            "allDay":   all_day,
            "desc":     desc_short[:200],
        })

    events_json = json.dumps(ev_list, indent=2)
    updated = datetime.utcnow().strftime("%B %d, %Y at %I:%M %p UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dell Med Events Calendar</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --orange: #BF5700;
    --orange-dark: #8C3E00;
    --orange-light: #FDF0E6;
    --orange-mid: #F5D9C0;
    --cream: #FDFAF6;
    --charcoal: #1A1A1A;
    --gray: #6B6B6B;
    --border: #E5D8CC;
    --white: #FFFFFF;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'DM Sans', sans-serif; background: var(--cream); color: var(--charcoal); min-height: 100vh; }}

  .site-header {{ background: var(--orange); padding: 0; position: relative; overflow: hidden; }}
  .site-header::before {{ content: ''; position: absolute; top: -60px; right: -60px; width: 300px; height: 300px; border-radius: 50%; background: rgba(255,255,255,0.06); }}
  .header-inner {{ max-width: 960px; margin: 0 auto; padding: 36px 24px 32px; position: relative; z-index: 1; }}
  .header-eyebrow {{ font-size: 0.7rem; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(255,255,255,0.65); margin-bottom: 8px; }}
  .header-title {{ font-family: 'Playfair Display', serif; font-size: clamp(1.8rem, 4vw, 2.6rem); font-weight: 700; color: #fff; line-height: 1.15; margin-bottom: 10px; }}
  .header-sub {{ font-size: 0.9rem; color: rgba(255,255,255,0.75); font-weight: 300; max-width: 520px; }}

  .subscribe-section {{ background: var(--orange-dark); border-bottom: 3px solid rgba(0,0,0,0.15); }}
  .subscribe-inner {{ max-width: 960px; margin: 0 auto; padding: 20px 24px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
  .subscribe-icon {{ width: 36px; height: 36px; background: rgba(255,255,255,0.15); border-radius: 8px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
  .subscribe-text {{ flex: 1; min-width: 200px; }}
  .subscribe-label {{ font-size: 0.7rem; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: rgba(255,255,255,0.6); margin-bottom: 2px; }}
  .subscribe-url {{ font-family: monospace; font-size: 0.8rem; color: #fff; word-break: break-all; }}
  .copy-btn {{ background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3); color: #fff; padding: 8px 16px; border-radius: 6px; font-size: 0.78rem; font-weight: 500; cursor: pointer; font-family: 'DM Sans', sans-serif; transition: background 0.2s; white-space: nowrap; flex-shrink: 0; }}
  .copy-btn:hover {{ background: rgba(255,255,255,0.25); }}
  .copy-btn.copied {{ background: rgba(127,219,138,0.3); border-color: #7FDB8A; }}

  .main {{ max-width: 960px; margin: 0 auto; padding: 40px 24px 60px; display: grid; grid-template-columns: 1fr 320px; gap: 36px; align-items: start; }}
  @media (max-width: 720px) {{ .main {{ grid-template-columns: 1fr; }} .sidebar {{ order: -1; }} }}

  .events-header {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 20px; border-bottom: 2px solid var(--orange); padding-bottom: 10px; }}
  .events-title {{ font-family: 'Playfair Display', serif; font-size: 1.3rem; font-weight: 600; }}
  .events-count {{ font-size: 0.72rem; color: var(--gray); }}
  .updated-note {{ font-size: 0.65rem; color: var(--gray); margin-bottom: 16px; font-style: italic; }}

  .month-group {{ margin-bottom: 32px; }}
  .month-label {{ font-size: 0.68rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--orange); margin-bottom: 10px; padding-left: 4px; }}

  .event-card {{ display: flex; background: var(--white); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 10px; text-decoration: none; color: inherit; transition: box-shadow 0.2s, transform 0.2s; }}
  .event-card:hover {{ box-shadow: 0 4px 20px rgba(191,87,0,0.12); transform: translateY(-1px); }}

  .event-date-col {{ width: 56px; flex-shrink: 0; background: var(--orange-light); border-right: 1px solid var(--border); display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 12px 6px; }}
  .event-date-col.today-col {{ background: var(--orange); }}
  .ev-month {{ font-size: 0.6rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--orange); line-height: 1; }}
  .today-col .ev-month {{ color: rgba(255,255,255,0.8); }}
  .ev-day {{ font-family: 'Playfair Display', serif; font-size: 1.4rem; font-weight: 700; color: var(--charcoal); line-height: 1.1; }}
  .today-col .ev-day {{ color: #fff; }}
  .ev-dow {{ font-size: 0.55rem; color: var(--gray); text-transform: uppercase; letter-spacing: 0.06em; }}
  .today-col .ev-dow {{ color: rgba(255,255,255,0.7); }}

  .event-body {{ flex: 1; padding: 12px 14px; min-width: 0; }}
  .ev-title {{ font-size: 0.88rem; font-weight: 600; color: var(--charcoal); line-height: 1.35; margin-bottom: 5px; }}
  .event-card:hover .ev-title {{ color: var(--orange); }}
  .ev-meta {{ font-size: 0.72rem; color: var(--gray); display: flex; flex-wrap: wrap; gap: 8px; }}
  .ev-meta-item {{ display: flex; align-items: center; gap: 3px; }}
  .badge-today {{ display: inline-block; background: var(--orange); color: #fff; font-size: 0.55rem; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; padding: 2px 6px; border-radius: 3px; margin-left: 6px; vertical-align: middle; }}
  .ev-arrow {{ display: flex; align-items: center; padding: 0 12px; color: var(--border); flex-shrink: 0; transition: color 0.2s; }}
  .event-card:hover .ev-arrow {{ color: var(--orange); }}

  .sidebar-card {{ background: var(--white); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 20px; }}
  .sidebar-card-header {{ background: var(--orange-light); border-bottom: 1px solid var(--border); padding: 14px 18px; display: flex; align-items: center; gap: 8px; }}
  .sidebar-card-title {{ font-family: 'Playfair Display', serif; font-size: 1rem; font-weight: 600; color: var(--orange-dark); }}
  .sidebar-card-body {{ padding: 18px; }}

  .step {{ display: flex; gap: 12px; margin-bottom: 18px; }}
  .step:last-child {{ margin-bottom: 0; }}
  .step-num {{ width: 24px; height: 24px; border-radius: 50%; background: var(--orange); color: #fff; font-size: 0.7rem; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; margin-top: 1px; }}
  .step-content {{ flex: 1; }}
  .step-title {{ font-size: 0.82rem; font-weight: 600; color: var(--charcoal); margin-bottom: 3px; }}
  .step-desc {{ font-size: 0.75rem; color: var(--gray); line-height: 1.5; }}
  .step-desc code {{ background: var(--orange-light); color: var(--orange-dark); padding: 1px 5px; border-radius: 3px; font-size: 0.7rem; font-family: monospace; }}

  .platform-tab {{ display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 14px; }}
  .tab-btn {{ flex: 1; padding: 7px 10px; font-size: 0.72rem; font-weight: 500; background: var(--white); border: none; cursor: pointer; font-family: 'DM Sans', sans-serif; color: var(--gray); border-right: 1px solid var(--border); }}
  .tab-btn:last-child {{ border-right: none; }}
  .tab-btn.active {{ background: var(--orange); color: #fff; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}
  .info-note {{ background: var(--orange-light); border-left: 3px solid var(--orange); border-radius: 0 6px 6px 0; padding: 10px 12px; font-size: 0.74rem; color: var(--orange-dark); line-height: 1.5; margin-top: 14px; }}
  .divider {{ height: 1px; background: var(--border); margin: 14px 0; }}

  .site-footer {{ border-top: 1px solid var(--border); padding: 20px 24px; text-align: center; font-size: 0.72rem; color: var(--gray); }}
  .site-footer a {{ color: var(--orange); text-decoration: none; }}
  .site-footer a:hover {{ text-decoration: underline; }}
  .no-events {{ text-align: center; padding: 40px; color: var(--gray); font-size: 0.9rem; }}
</style>
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <div class="header-eyebrow">Dell Medical School · UT Austin</div>
    <h1 class="header-title">Events Calendar</h1>
    <p class="header-sub">Upcoming events from Dell Med. Subscribe to add all events directly to your Outlook calendar.</p>
  </div>
</header>

<div class="subscribe-section">
  <div class="subscribe-inner">
    <div class="subscribe-icon">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
    </div>
    <div class="subscribe-text">
      <div class="subscribe-label">Calendar Subscribe URL</div>
      <div class="subscribe-url" id="ics-url">https://michael-dean22.github.io/Dell_Med_Internal_Events/dell-med-events.ics</div>
    </div>
    <button class="copy-btn" id="copy-btn" onclick="copyUrl()">Copy Link</button>
  </div>
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
      <div class="sidebar-card-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8C3E00" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <div class="sidebar-card-title">How to Subscribe</div>
      </div>
      <div class="sidebar-card-body">
        <p style="font-size:0.78rem;color:var(--gray);margin-bottom:14px;line-height:1.5;">Subscribe once and all Dell Med events appear automatically in your Outlook — updated daily.</p>
        <div class="platform-tab">
          <button class="tab-btn active" onclick="switchTab('desktop')">Outlook Desktop</button>
          <button class="tab-btn" onclick="switchTab('web')">Outlook Web</button>
          <button class="tab-btn" onclick="switchTab('mac')">Mac Calendar</button>
        </div>
        <div class="tab-panel active" id="tab-desktop">
          <div class="step"><div class="step-num">1</div><div class="step-content"><div class="step-title">Copy the subscribe URL</div><div class="step-desc">Click <strong>Copy Link</strong> at the top of this page.</div></div></div>
          <div class="step"><div class="step-num">2</div><div class="step-content"><div class="step-title">Open Account Settings</div><div class="step-desc">In Outlook, go to <code>File → Account Settings → Account Settings</code></div></div></div>
          <div class="step"><div class="step-num">3</div><div class="step-content"><div class="step-title">Add Internet Calendar</div><div class="step-desc">Click <code>Internet Calendars</code> tab → <code>New</code> → paste the URL → click <code>Add</code></div></div></div>
          <div class="step"><div class="step-num">4</div><div class="step-content"><div class="step-title">Done!</div><div class="step-desc">Events appear under <strong>Other Calendars</strong>. Outlook syncs automatically.</div></div></div>
        </div>
        <div class="tab-panel" id="tab-web">
          <div class="step"><div class="step-num">1</div><div class="step-content"><div class="step-title">Copy the subscribe URL</div><div class="step-desc">Click <strong>Copy Link</strong> at the top of this page.</div></div></div>
          <div class="step"><div class="step-num">2</div><div class="step-content"><div class="step-title">Open Outlook Calendar</div><div class="step-desc">Go to <code>outlook.office.com</code> and click the Calendar icon.</div></div></div>
          <div class="step"><div class="step-num">3</div><div class="step-content"><div class="step-title">Add Calendar</div><div class="step-desc">Click <code>Add calendar</code> → <code>Subscribe from web</code> → paste URL → <code>Import</code></div></div></div>
          <div class="step"><div class="step-num">4</div><div class="step-content"><div class="step-title">Done!</div><div class="step-desc">Name it <strong>Dell Med Events</strong> for easy reference.</div></div></div>
        </div>
        <div class="tab-panel" id="tab-mac">
          <div class="step"><div class="step-num">1</div><div class="step-content"><div class="step-title">Copy the subscribe URL</div><div class="step-desc">Click <strong>Copy Link</strong> at the top of this page.</div></div></div>
          <div class="step"><div class="step-num">2</div><div class="step-content"><div class="step-title">Open Calendar app</div><div class="step-desc">Go to <code>File → New Calendar Subscription</code></div></div></div>
          <div class="step"><div class="step-num">3</div><div class="step-content"><div class="step-title">Paste URL and subscribe</div><div class="step-desc">Paste the URL → <code>Subscribe</code> → set auto-refresh to <strong>Every day</strong></div></div></div>
          <div class="step"><div class="step-num">4</div><div class="step-content"><div class="step-title">Done!</div><div class="step-desc">Syncs to iPhone too if iCloud Calendar is enabled.</div></div></div>
        </div>
        <div class="info-note">💡 The calendar updates automatically every morning. New events appear in your Outlook within 24 hours.</div>
      </div>
    </div>

    <div class="sidebar-card">
      <div class="sidebar-card-header">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8C3E00" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
        <div class="sidebar-card-title">Event Sources</div>
      </div>
      <div class="sidebar-card-body">
        <div style="font-size:0.78rem;color:var(--gray);line-height:1.6;">
          <div style="margin-bottom:8px;display:flex;gap:8px;align-items:flex-start;"><span style="color:var(--orange);font-weight:700;flex-shrink:0;">→</span><span><strong style="color:var(--charcoal);">Dell Med Public Events</strong><br>dellmed.utexas.edu/events</span></div>
          <div class="divider"></div>
          <div style="display:flex;gap:8px;align-items:flex-start;"><span style="color:var(--orange);font-weight:700;flex-shrink:0;">→</span><span><strong style="color:var(--charcoal);">Internal Events (Cerebrum)</strong><br>Dell Med intranet faculty &amp; staff events</span></div>
        </div>
      </div>
    </div>
  </aside>
</div>

<footer class="site-footer">
  <p>Dell Medical School · The University of Texas at Austin &nbsp;|&nbsp;
  <a href="https://dellmed.utexas.edu/events" target="_blank">View all public events →</a></p>
</footer>

<script>
const EVENTS = {events_json};

const DAYS        = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const MONTHS_S    = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const MONTHS_L    = ["January","February","March","April","May","June","July","August","September","October","November","December"];

function copyUrl() {{
  navigator.clipboard.writeText(document.getElementById('ics-url').textContent).then(() => {{
    const btn = document.getElementById('copy-btn');
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = 'Copy Link'; btn.classList.remove('copied'); }}, 2500);
  }});
}}

function switchTab(id) {{
  document.querySelectorAll('.tab-btn').forEach((b,i) => b.classList.toggle('active', ['desktop','web','mac'][i] === id));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
}}

function formatTime(isoStr) {{
  const d = new Date(isoStr);
  let h = d.getHours(), m = d.getMinutes();
  const ampm = h >= 12 ? 'p.m.' : 'a.m.';
  h = h % 12 || 12;
  return m === 0 ? h + ' ' + ampm : h + ':' + String(m).padStart(2,'0') + ' ' + ampm;
}}

function render() {{
  const today = new Date(); today.setHours(0,0,0,0);
  const todayStr = today.toDateString();

  const upcoming = EVENTS
    .map(e => {{ 
      const dt = new Date(e.start);
      // Normalize to start of day for comparison
      const dayOnly = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
      return {{ ...e, dtstart: dt, dayOnly }};
    }})
    .filter(e => e.dayOnly >= today)
    .sort((a,b) => a.dtstart - b.dtstart);

  document.getElementById('events-count').textContent =
    upcoming.length + ' upcoming event' + (upcoming.length !== 1 ? 's' : '');

  // Group by month
  const groups = {{}};
  for (const ev of upcoming) {{
    const key = ev.dtstart.getFullYear() + '-' + ev.dtstart.getMonth();
    if (!groups[key]) groups[key] = {{ label: MONTHS_L[ev.dtstart.getMonth()] + ' ' + ev.dtstart.getFullYear(), events: [] }};
    groups[key].events.push(ev);
  }}

  const container = document.getElementById('events-container');
  container.innerHTML = '';

  if (upcoming.length === 0) {{
    container.innerHTML = '<div class="no-events">No upcoming events found.</div>';
    return;
  }}

  for (const group of Object.values(groups)) {{
    const groupEl = document.createElement('div');
    groupEl.className = 'month-group';
    const label = document.createElement('div');
    label.className = 'month-label';
    label.textContent = group.label;
    groupEl.appendChild(label);

    for (const ev of group.events) {{
      const isToday = ev.dayOnly.toDateString() === todayStr;
      const card = document.createElement('a');
      card.className = 'event-card';
      card.href = ev.url || '#';
      if (ev.url) {{ card.target = '_blank'; card.rel = 'noopener noreferrer'; }}

      const dateCol = document.createElement('div');
      dateCol.className = 'event-date-col' + (isToday ? ' today-col' : '');
      const mon = document.createElement('span'); mon.className = 'ev-month'; mon.textContent = MONTHS_S[ev.dtstart.getMonth()];
      const day = document.createElement('span'); day.className = 'ev-day'; day.textContent = ev.dtstart.getDate();
      const dow = document.createElement('span'); dow.className = 'ev-dow'; dow.textContent = DAYS[ev.dtstart.getDay()];
      dateCol.append(mon, day, dow);

      const body = document.createElement('div');
      body.className = 'event-body';

      const title = document.createElement('div');
      title.className = 'ev-title';
      title.textContent = ev.title;
      if (isToday) {{ const badge = document.createElement('span'); badge.className = 'badge-today'; badge.textContent = 'Today'; title.appendChild(badge); }}

      const meta = document.createElement('div');
      meta.className = 'ev-meta';

      if (!ev.allDay && ev.start) {{
        const timeEl = document.createElement('span'); timeEl.className = 'ev-meta-item';
        let t = formatTime(ev.start);
        if (ev.end) t += ' – ' + formatTime(ev.end);
        timeEl.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> ' + t;
        meta.appendChild(timeEl);
      }}
      if (ev.location) {{
        const locEl = document.createElement('span'); locEl.className = 'ev-meta-item';
        locEl.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg> ' + ev.location;
        meta.appendChild(locEl);
      }}

      body.appendChild(title);
      body.appendChild(meta);

      const arrow = document.createElement('div');
      arrow.className = 'ev-arrow';
      arrow.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';

      card.append(dateCol, body, arrow);
      groupEl.appendChild(card);
    }}
    container.appendChild(groupEl);
  }}
}}

render();
</script>
</body>
</html>"""
    return html


def main():
    print("📡 Fetching RSS feed...")
    rss_items = fetch_rss_links(RSS_URL)
    print(f"   Found {len(rss_items)} events in feed")

    events = []
    for i, (title, url) in enumerate(rss_items, 1):
        print(f"   [{i}/{len(rss_items)}] Scraping: {title[:60]}...")
        ev = scrape_event_page(url)
        if ev:
            events.append(ev)

    print(f"\n✅ Successfully scraped {len(events)} events")

    print("📅 Building ICS calendar...")
    ics_content = build_ics(events)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print(f"✅ Saved {OUTPUT_FILE}")

    print("🌐 Building calendar.html...")
    html_content = build_calendar_html(events)
    with open("calendar.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Saved calendar.html")
    print(f"\n   Pages live at:")
    print(f"   https://michael-dean22.github.io/Dell_Med_Internal_Events/dell-med-events.ics")
    print(f"   https://michael-dean22.github.io/Dell_Med_Internal_Events/calendar.html")


if __name__ == "__main__":
    main()
