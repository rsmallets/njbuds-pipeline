#This is the version that worked and pulled in all the recreational sites

import re, time, io
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException

URL = "https://www.nj.gov/cannabis/dispensaries/find/"
OUTFILE = "nj_dispensaries.csv"

ADDR_RE = re.compile(r"""
    ^(?P<street>.+?)\s*,\s*
    (?P<city>[A-Za-z'\.\-\s]+)\s*,\s*
    (?P<state>NJ)\s*
    (?P<zip>\d{5})?
    """, re.VERBOSE)

def looks_like_address(line: str) -> bool:
    line = line.strip()
    if "NJ" not in line:
        return False
    # quick check for a 5-digit zip anywhere
    if re.search(r"\b\d{5}\b", line) and "," in line:
        return True
    # sometimes the zip may be missing in some displays
    if ", NJ" in line and "," in line:
        return True
    return False

def extract_records_from_text_lines(lines, external_links):
    """Pair name lines with following address lines, attach a nearest external link if any."""
    rows = []
    for i, ln in enumerate(lines):
        if looks_like_address(ln):
            addr = ln
            # name is usually the previous non-empty line
            name = ""
            j = i - 1
            while j >= 0 and not name:
                candidate = lines[j].strip()
                # ignore generic headings
                if candidate and len(candidate) > 2 and "dispensary" not in candidate.lower():
                    name = candidate
                j -= 1
            # parse address
            m = ADDR_RE.search(addr)
            street = city = state = zipc = ""
            if m:
                street = m.group("street") or ""
                city   = (m.group("city") or "").strip(" ,")
                state  = m.group("state") or "NJ"
                zipc   = m.group("zip") or ""
            # pick a website near this block if present (best-effort)
            website = ""
            # light heuristic: choose any external link whose domain appears in surrounding text
            block_text = " ".join(lines[max(0,i-3): i+4]).lower()
            for link in external_links:
                dom = re.sub(r"^https?://(www\.)?", "", link.lower()).split("/")[0]
                if dom and dom in block_text:
                    website = link
                    break
            rows.append({
                "name": name, "street": street, "city": city, "state": state,
                "zip": zipc, "website": website, "phone": "", "source": URL
            })
    # de-dupe on (name, street)
    seen, out = set(), []
    for r in rows:
        key = (r["name"].lower(), r["street"].lower())
        if key not in seen and r["name"] and (r["street"] or r["city"]):
            seen.add(key)
            out.append(r)
    return out

def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1600,1200")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    driver.get(URL)
    time.sleep(12)  # allow outer page to load

    # Find all iframes and try each until we detect multiple addresses
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    print(f"Found {len(frames)} iframe(s)")
    best_rows = []

    for idx, frame in enumerate(frames):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            time.sleep(8)  # allow inner app to render

            # collect visible text lines
            els = driver.find_elements(By.CSS_SELECTOR, "body *")
            lines = [e.text.strip() for e in els if e.text and e.text.strip()]
            # collect external links
            links = []
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
                href = a.get_attribute("href") or ""
                if href.startswith("http") and ("nj.gov" not in href.lower()):
                    # ignore social links
                    if all(s not in href.lower() for s in ["facebook.com","instagram.com","twitter.com","x.com"]):
                        links.append(href)

            # attempt extraction
            rows = extract_records_from_text_lines(lines, links)
            print(f"iframe {idx+1}: candidates={len(rows)}, ext_links={len(links)}")
            if len(rows) >= len(best_rows):
                best_rows = rows

        except Exception as e:
            print(f"iframe {idx+1} error: {e}")
            continue
        finally:
            driver.switch_to.default_content()

    driver.quit()

    # Write results
    df = pd.DataFrame(best_rows, columns=["name","street","city","state","zip","website","phone","source"])
    df.to_csv(OUTFILE, index=False, encoding="utf-8")
    print(f"Wrote {OUTFILE} with {len(df)} rows")

if __name__ == "__main__":
    main()
