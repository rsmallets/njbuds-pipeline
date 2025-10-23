# NJBuds — New Jersey Cannabis Dispensary Data Pipeline
AI-Assisted Data Engineering Project by Ryan Smallets

---

## Overview
NJBuds is an automated data pipeline that collects, enriches, and structures official New Jersey cannabis dispensary data from the NJ Cannabis Regulatory Commission (CRC) website.

The system demonstrates AI-assisted engineering, combining traditional data engineering methods with modern generative AI workflows to accelerate development and design.

The result is a scalable, cloud-ready dataset of verified dispensaries that can power analytics dashboards, business listings, and search experiences.

---

## Architecture Overview
+-------------------+
| NJ CRC Map Iframe |
+--------+----------+
|
| (Selenium Web Scraper)
v
+-----------------------+
| Raw Dispensary Data |
| (HTML → CSV/JSON) |
+-----------+-----------+
|
| (Data Enrichment via Python + AI)
v
+-----------------------+
| Enriched Dataset |
| - Website URLs |
| - Phone Numbers |
| - Platform Detection |
+-----------+-----------+
|
| (Cloud Storage Ready)
v
+-----------------------+
| Azure / PostgreSQL DB |
+-----------------------+

yaml
Copy code

---

## Design Intent and AI Collaboration
This project was built through AI-assisted engineering, where human design and judgment combined with AI-driven coding.

### Human Responsibilities (Ryan’s Role)
- Defined project scope and business objective  
- Designed data pipeline architecture and stages  
- Validated data extraction, testing, and error handling  
- Managed debugging, logging, and enrichment design  
- Documented and versioned all deliverables for reproducibility  

### AI Collaboration (ChatGPT’s Role)
- Generated initial Python scripts (Selenium and Pandas)  
- Provided code optimization and error-resolution recommendations  
- Assisted in modularizing enrichment logic for maintainability  

This workflow reflects modern engineering practice — where generative AI acts as a productivity amplifier and human engineers lead system design, context, and validation.

---

## Components

| Stage | Description | Technologies |
|--------|--------------|---------------|
| **Scraper** | Extracts dispensary data from NJ CRC embedded map using Selenium | Python, Selenium, Pandas |
| **Enrichment** | Finds official websites and phone numbers via AI-assisted web search and regex extraction | requests, re, BeautifulSoup, OpenAI API (optional) |
| **Platform Detection** | Detects menu hosting platform (Dutchie, Jane, Weedmaps, etc.) | Python, Regex, BeautifulSoup |
| **Storage** | Outputs structured dataset ready for database upload | CSV, PostgreSQL, Azure Blob Storage (planned) |

---

## Quickstart

Clone the repository and set up your environment.

```bash
# Create virtual environment
python -m venv venv

# Activate
# Windows
.\venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run pipeline scripts
python scripts/scrape_crc_iframe.py
python scripts/enrich_phones_from_sites.py
python scripts/detect_menu_platforms.py
Output files are saved in the /data directory with timestamps.

Example Output
dispensary_name	address	city	website	phone	platform
Harmony Dispensary	600 Meadowlands Pkwy	Secaucus	harmonydispensary.com	(201) 356-7268	Dutchie
The Botanist	100 Century Dr	Egg Harbor Twp	shopbotanist.com	(609) 277-7547	Jane

Use Cases
Power interactive cannabis dispensary maps and search platforms

Support public transparency dashboards

Feed AI search and recommendation engines

Benchmark platform adoption and regional growth

Lessons Learned
AI drastically accelerates development, but human understanding ensures data reliability.

Dynamic iframes require careful Selenium handling (e.g., explicit waits and frame switching).

Enrichment logic benefits from modular design — each stage runs independently for debugging and scalability.

Documentation is critical — every pipeline should be reproducible without the original author.

Future Enhancements
Deploy to Azure Data Factory or Google Cloud Composer

Implement daily scheduled refresh

Build Tableau or Power BI dashboard visualizing statewide data

Add API endpoint for third-party integrations

Technical Stack
Python 3.11

Selenium for dynamic scraping

Pandas for data transformation

BeautifulSoup for enrichment parsing

Azure / PostgreSQL for storage (planned)

OpenAI API for optional AI-driven data enrichment

Author
Ryan Smallets
Retail operations leader turned AI-assisted data engineer.
Focused on building cloud-native data systems that merge automation, analytics, and practical business use cases.

LinkedIn: linkedin.com/in/ryansmallets
GitHub: github.com/yourusername

Acknowledgments
Special thanks to AI collaboration tools (ChatGPT and GitHub Copilot) for accelerating development speed and documentation clarity.

This project represents the future of engineering — where human creativity meets AI efficiency.
