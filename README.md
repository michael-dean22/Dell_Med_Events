# Dell Med Events Ticker

Auto-aggregating events ticker for Dell Medical School. Pulls from three sources, deduplicates, and serves a single feed.

**Live site:** https://michael-dean22.github.io/Dell_Med_Events/
**iCal subscribe URL:** https://michael-dean22.github.io/Dell_Med_Events/dell-med-events.ics

## How it works

A GitHub Action runs twice daily, executes `scripts/build-events.py`, and commits updated `data/events.json` and `dell-med-events.ics` files back to the repo. The static `index.html` page fetches `data/events.json` on load and renders both the ticker and the upcoming-events list.

### Sources

| Source | How it gets in | Auth needed |
|---|---|---|
| **Dell Med public events** (`dellmed.utexas.edu/events`) | Scraped from the public HTML page | No |
| **UT Texas Today calendar** (`calendar.utexas.edu`) | Localist `.ics` feed at `/calendar/1.ics` | No |
| **Dell Med internal events** (`intranet.dellmed.utexas.edu/events`) | Manually maintained in `data/internal-events.json` | The intranet requires UT EID login, so it can't be scraped by an unauthenticated runner |

## Adding internal events

Edit `data/internal-events.json` and commit. The Action runs on every push, so your event will appear in the ticker within a minute or two.

Each event needs at minimum a `title`, `start` (YYYY-MM-DD), and `url`. Optional fields: `time`, `location`, `speaker`, `end`.

```json
{
  "events": [
    {
      "title": "Department of Medicine Faculty Meeting",
      "start": "2026-06-15",
      "time": "12:00 PM - 1:00 PM",
      "location": "HDB 1.208",
      "speaker": "Dr. Jane Smith",
      "url": "https://intranet.dellmed.utexas.edu/events/some-event"
    }
  ]
}
```

## Adding the aggregated feed to the Dell Med Events Calendar (intranet)

Since the intranet calendar can't be updated programmatically, do this once:

1. Download `dell-med-events.ics` from the live URL above
2. Go to https://intranet.dellmed.utexas.edu/public/dell-med-events-calendar
3. Use the calendar's import option (or have the calendar admin subscribe to the URL if the system supports iCal subscriptions — most do)

If the Dell Med Events Calendar supports iCal subscription URLs, you can simply give it the `.ics` URL and it will auto-refresh.

## Manual refresh

In the Actions tab on GitHub, find "Update Events" and click **Run workflow**.

## Local development

```bash
python3 scripts/build-events.py
python3 -m http.server 8000   # then open http://localhost:8000
```

## File layout

```
.
├── index.html                       # The ticker page
├── dell-med-events.ics              # Auto-generated subscribe feed
├── data/
│   ├── events.json                  # Auto-generated merged event feed
│   └── internal-events.json         # MANUALLY EDITED - intranet events
├── scripts/
│   └── build-events.py              # Scrapes sources, builds events.json + .ics
└── .github/workflows/
    └── update-events.yml            # Runs the build script on a schedule
```
