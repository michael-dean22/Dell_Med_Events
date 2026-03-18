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
    soup = BeautifulSoup(resp.content, "xml")
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

    print(f"✅ Saved to {OUTPUT_FILE}")
    print(f"   Calendar will be available at:")
    print(f"   https://michael-dean22.github.io/Dell_Med_Internal_Events/dell-med-events.ics")


if __name__ == "__main__":
    main()
