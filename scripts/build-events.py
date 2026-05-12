#!/usr/bin/env python3
"""
Build events.json and dell-med-events.ics from three sources:
  1. Dell Med public events (scraped from dellmed.utexas.edu/events)
  2. UT Texas Today calendar (Localist .ics feed)
  3. Manually maintained internal events (data/internal-events.json)
"""

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_JSON = DATA_DIR / "events.json"
OUT_ICS = ROOT / "dell-med-events.ics"
INTERNAL_JSON = DATA_DIR / "internal-events.json"

UA = "Mozilla/5.0 (compatible; DellMedEventsBot/1.0; +https://github.com/michael-dean22/Dell_Med_Events)"

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------- Dell Med public events scraper ----------

class DellMedParser(HTMLParser):
    """
    Parse the events list at dellmed.utexas.edu/events. The page structure is:
      <h2>May 2026</h2>
      <div with date 'May 12'>
        <div with time '7:30-8:30 a.m.'>
        <a href="/events/..." >Title</a>
        <div with location>
        <div with speaker (optional)>
        <a>Category</a> ...
    Rather than parse strict structure (it changes), we walk through the page
    capturing month headers, date markers, and the first link inside each card.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.current_year = None
        self.current_month = None
        self.current_day = None
        self.current_day_end = None
        self.in_event_link = False
        self.event_url = None
        self.event_title_parts = []
        self.recent_links = []
        # State machine: after a date, collect time, link (title), location, speaker
        self.pending_event = None
        self.text_buffer = []
        self.in_h2 = False
        self.h2_text = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "h2":
            self.in_h2 = True
            self.h2_text = []
        elif tag == "a":
            href = attrs_d.get("href", "")
            if href.startswith("/events/") and href != "/events":
                self.in_event_link = True
                self.event_url = "https://dellmed.utexas.edu" + href
                self.event_title_parts = []

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_h2 = False
            text = "".join(self.h2_text).strip()
            m = re.match(r"^([A-Za-z]+)\s+(\d{4})$", text)
            if m and m.group(1)[:3] in MONTH_MAP:
                self.current_month = MONTH_MAP[m.group(1)[:3]]
                self.current_year = int(m.group(2))
        elif tag == "a" and self.in_event_link:
            self.in_event_link = False
            title = "".join(self.event_title_parts).strip()
            if title and self.event_url and self.current_year and self.current_month:
                # We need a day. Look at recent text buffer for "Mmm DD" or "DD" pattern.
                day = self._find_recent_day()
                if day:
                    iso = f"{self.current_year:04d}-{self.current_month:02d}-{day:02d}"
                    # Time guess from buffer
                    time_str = self._find_recent_time()
                    self.events.append({
                        "source": "public",
                        "title": title,
                        "url": self.event_url,
                        "start": iso,
                        "time": time_str,
                    })
            self.event_url = None
            self.event_title_parts = []

    def handle_data(self, data):
        if self.in_h2:
            self.h2_text.append(data)
        if self.in_event_link:
            self.event_title_parts.append(data)
        # Keep a rolling buffer of recent visible text (last ~500 chars)
        self.text_buffer.append(data)
        if len(self.text_buffer) > 60:
            self.text_buffer = self.text_buffer[-60:]

    def _find_recent_day(self):
        text = " ".join(self.text_buffer[-40:])
        # Match "May 12" or "Jun 26-28" or just "12" right after month
        matches = list(re.finditer(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})(?:[\u2013\u2014-]\d{1,2})?\b",
            text
        ))
        if matches:
            return int(matches[-1].group(2))
        return None

    def _find_recent_time(self):
        text = " ".join(self.text_buffer[-40:])
        m = re.search(
            r"(\d{1,2}(?::\d{2})?(?:\s*[\u2013\u2014-]\s*\d{1,2}(?::\d{2})?)?\s*[apAP]\.?[mM]\.?)",
            text
        )
        return m.group(1).strip() if m else None


def fetch_dellmed_public():
    try:
        html = fetch("https://dellmed.utexas.edu/events")
    except Exception as e:
        print(f"WARN: failed to fetch Dell Med public events: {e}", file=sys.stderr)
        return []
    p = DellMedParser()
    p.feed(html)
    # Dedupe by URL
    seen = set()
    out = []
    for e in p.events:
        if e["url"] in seen:
            continue
        seen.add(e["url"])
        out.append(e)
    # Now do a second pass to pull location and speaker by re-parsing each event card region.
    # Cheap approach: pull location/speaker by looking around the link text in raw HTML.
    out = enrich_dellmed(out, html)
    return out


def enrich_dellmed(events, html):
    """For each event, find its anchor in html and pull surrounding context for location."""
    for e in events:
        # href in the html
        href_path = e["url"].replace("https://dellmed.utexas.edu", "")
        idx = html.find(href_path)
        if idx < 0:
            continue
        # Look at ~800 chars after the anchor close for location
        chunk = html[idx:idx + 1500]
        # Location is typically the next paragraph-like text after the link
        # Strip tags
        text = re.sub(r"<[^>]+>", "\n", chunk)
        text = re.sub(r"\n+", "\n", text).strip()
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 2:
            # Lines[0] is title; next non-empty line that looks like a venue/location
            for ln in lines[1:6]:
                if any(kw in ln for kw in ["HDB", "HLB", "HTB", "Zoom", "Virtual", "Hybrid", "Hotel", "Auditorium", "Building", "Center"]):
                    e["location"] = ln
                    break
            # speaker: a line with "M.D." or "Ph.D." after the location
            for ln in lines[1:8]:
                if any(kw in ln for kw in ["M.D.", "Ph.D.", "D.O.", "MPH", "M.Ed.", "MBA"]):
                    e.setdefault("speaker", ln)
                    break
    return events


# ---------- UT Localist .ics parser ----------

def parse_ics(text):
    """Minimal ICS parser. Returns list of {title, start, end, url, location}."""
    events = []
    # Unfold lines
    lines = []
    for raw in text.splitlines():
        if raw.startswith(" ") or raw.startswith("\t"):
            if lines:
                lines[-1] += raw[1:]
        else:
            lines.append(raw)
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current and "DTSTART" in current:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";", 1)[0]
            current[key] = val
    return events


def ics_date_to_iso(s):
    """ICS date can be YYYYMMDD or YYYYMMDDTHHMMSSZ."""
    s = s.strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def fetch_ut_calendar():
    try:
        text = fetch("https://calendar.utexas.edu/calendar/1.ics")
    except Exception as e:
        print(f"WARN: failed to fetch UT calendar: {e}", file=sys.stderr)
        return []
    raw = parse_ics(text)
    today = date.today().isoformat()
    out = []
    seen = set()
    for ev in raw:
        iso = ics_date_to_iso(ev.get("DTSTART", ""))
        if not iso or iso < today:
            continue
        title = ev.get("SUMMARY", "").strip()
        if not title:
            continue
        # Drop obvious noise / sports unless someone wants them; we keep them but tag source
        key = (title, iso)
        if key in seen:
            continue
        seen.add(key)
        url = ev.get("URL", "").strip() or "https://calendar.utexas.edu/?school=austin"
        location = ev.get("LOCATION", "").strip()
        out.append({
            "source": "ut",
            "title": title,
            "url": url,
            "start": iso,
            "location": location or None,
        })
    return out


# ---------- Internal events (manual JSON) ----------

def load_internal():
    if not INTERNAL_JSON.exists():
        return []
    try:
        data = json.loads(INTERNAL_JSON.read_text())
    except Exception as e:
        print(f"WARN: could not parse internal-events.json: {e}", file=sys.stderr)
        return []
    out = []
    for e in data.get("events", []):
        if not e.get("start") or not e.get("title"):
            continue
        out.append({
            "source": "internal",
            "title": e["title"],
            "url": e.get("url") or "https://intranet.dellmed.utexas.edu/events",
            "start": e["start"],
            "time": e.get("time"),
            "location": e.get("location"),
            "speaker": e.get("speaker"),
            "end": e.get("end"),
        })
    return out


# ---------- ICS output ----------

def ics_escape(s):
    if not s:
        return ""
    return (s.replace("\\", "\\\\")
             .replace(",", "\\,")
             .replace(";", "\\;")
             .replace("\n", "\\n"))


def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Dell Med Events Ticker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Dell Med Events",
        "X-WR-CALDESC:Aggregated events from Dell Medical School and UT Austin",
    ]
    for i, e in enumerate(events):
        d = e["start"].replace("-", "")
        end_d = (e.get("end") or e["start"]).replace("-", "")
        # All-day events: DTEND should be the day after
        try:
            sd = datetime.strptime(end_d, "%Y%m%d")
            from datetime import timedelta
            ed = (sd + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            ed = end_d
        uid = f"{i}-{d}-{e['source']}@dell-med-events"
        title = e["title"]
        if e.get("time"):
            title = f"{title} ({e['time']})"
        description_parts = []
        if e.get("speaker"):
            description_parts.append(f"Speaker: {e['speaker']}")
        if e.get("time"):
            description_parts.append(f"Time: {e['time']}")
        description_parts.append(f"Source: {e['source']}")
        description_parts.append(f"More info: {e['url']}")
        out.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{d}",
            f"DTEND;VALUE=DATE:{ed}",
            f"SUMMARY:{ics_escape(title)}",
            f"DESCRIPTION:{ics_escape(chr(10).join(description_parts))}",
        ])
        if e.get("location"):
            out.append(f"LOCATION:{ics_escape(e['location'])}")
        out.append(f"URL:{e['url']}")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


# ---------- Main ----------

def main():
    public_events = fetch_dellmed_public()
    print(f"Dell Med public: {len(public_events)} events", file=sys.stderr)
    ut_events = fetch_ut_calendar()
    print(f"UT calendar:    {len(ut_events)} events", file=sys.stderr)
    internal_events = load_internal()
    print(f"Internal:       {len(internal_events)} events", file=sys.stderr)

    # Filter out the placeholder example
    internal_events = [e for e in internal_events
                       if not e["title"].startswith("Example:")]

    today = date.today().isoformat()
    all_events = [e for e in public_events + internal_events + ut_events
                  if e.get("start", "") >= today]
    all_events.sort(key=lambda e: (e["start"], e["title"]))

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "public": len([e for e in all_events if e["source"] == "public"]),
            "internal": len([e for e in all_events if e["source"] == "internal"]),
            "ut": len([e for e in all_events if e["source"] == "ut"]),
        },
        "events": all_events,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT_JSON} ({len(all_events)} total events)", file=sys.stderr)

    ics = build_ics(all_events)
    OUT_ICS.write_text(ics)
    print(f"Wrote {OUT_ICS}", file=sys.stderr)


if __name__ == "__main__":
    main()
