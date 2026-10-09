# Open gym San Diego

Free drop-in indoor open play (basketball, pickleball, volleyball, badminton, open gym) at City of San Diego rec centers, sorted by distance from any San Diego ZIP code you enter.

## How it stays current

A GitHub Action (`.github/workflows/update.yml`) runs every morning. It visits each center's page on sandiego.gov, reads whatever schedule is posted there (PDF calendar, weekly flyer, scanned image, Google Calendar, or hours written on the page), and writes `schedules.json`. GitHub Pages republishes the site whenever that file changes.

If a center's schedule can't be read cleanly, its last good data is kept and the site marks it "May be outdated" with a link to the posted flyer, rather than showing guessed times. Centers that haven't posted the current month yet are listed under "No schedule posted for this date yet."

The workflow commits at least once a day, which also keeps GitHub from pausing it for inactivity.

## Files

- `index.html` – the site
- `schedules.json` – generated data (don't edit by hand)
- `scraper/gyms.json` – the list of centers: name, address, phone, map coordinates, and sandiego.gov page slug
- `scraper/update.py`, `scraper/parse.py` – the schedule reader

## Running it yourself (optional)

```
sudo apt-get install poppler-utils tesseract-ocr
pip install -r scraper/requirements.txt
python scraper/update.py            # all centers
python scraper/update.py doyle      # just one
```

To trigger an update on GitHub immediately: Actions → Update schedules → Run workflow.
