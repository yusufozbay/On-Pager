# Bulk URL Meta Crawler (Streamlit + Playwright)

This app crawls uploaded URLs and returns a table with:

- URL Address
- Meta Title
- Meta Description
- HTTP Status
- Error

It uses Playwright in-process and applies delay between hits on the same domain to reduce 429/403 blocking risk.

## Input formats

- CSV (uses first column as URL list)
- TXT (one URL per line)
- XLSX (uses first column as URL list)

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
streamlit run app.py
```

## Notes

- The crawler extracts metadata from the page `<head>` only.
- Non-essential resources like images/fonts/media are blocked for efficiency.
