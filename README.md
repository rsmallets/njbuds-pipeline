# NJBuds — New Jersey Dispensary Data Pipeline

## Overview
Automated pipeline to collect, enrich, and prepare a statewide dataset of New Jersey cannabis dispensaries for downstream analytics and product/menu ingestion.

## Key Stages
1. Scrape CRC official map to get dispensary list.
2. Discover official websites via automated search.
3. Enrich phone numbers by crawling brand sites.
4. Detect online menu platform (Dutchie, iHeartJane, Leafly, etc.).
5. Prepare for database ingestion (Postgres/Supabase).

## Repository Layout
- `scripts/` — runnable entry points (scrapers, enrichment)
- `src/njbuds/` — reusable helpers (future refactor)
- `data/` — raw/interim/processed (large files ignored by git)
- `docs/` — architecture and notes
- `notebooks/` — exploratory analysis
- `config/` — example configuration
- `tests/` — test harness

## Quickstart
```bash
python -m venv venv
# Windows
.\venv\Scripts\activate
# macOS/Linux
# source venv/bin/activate

pip install -r requirements.txt

# Run scrapers/enrichers
python scripts/scrape_crc_iframe.py
python scripts/find_websites_via_search_selenium.py
python scripts/enrich_phones_from_sites.py
python scripts/detect_menu_platforms.py
