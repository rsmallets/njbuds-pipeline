import re, time, pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = "https://www.nj.gov/cannabis/dispensaries/find/"

ADDRESS_RE = re.compile(r"""
    ^\s*                                  # start
    (?P<street>[\w\.\-#&' ]+\d[\w\.\-#&' ]*) # street with at least one digit
    ,\s*
    (?P<city>[A-Za-z\.\- &'()]+)
    ,\s*
    (?P<state>NJ)\s*
    (?P<zip>\d{5})?                       # optional zip
    \s*$                                  # end
""", re.VERBOSE)

SOCIAL = ("facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com")
EXCLUDE_DOMAINS = ("nj.gov",) + SOCIAL

def visible_texts(driver):
    els = driver.find_elements(By.CSS_SELECTOR, "body *")
    return [e.text.strip() for e in els if e.text and e.text.strip()]

def external_links(driver):
    out = []
    for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = (a.get_attribute("href") or "").strip()
        if href.startswith("http") and not any(dom in href for dom in EXCLUDE_DOMAINS):
            out.append(href)
    return list(dict.fromkeys(out))  # de-dupe, keep order

def scroll_page(driver, steps=6, pause=0.8):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight/3);")
        time.sleep(pause)

def try_collect(driver):
    # 1) Grab all text lines and hunt for address lines
    lines = visible_texts(driver)
    candidates = []
    for i, ln in enumerate(lines):
        m = ADDRESS_RE.match(ln)
        if m:
            # Heuristic: name is often on previous non-empty line
            name = ""
            for back in range(1, 4):
                if i - back >= 0 and lines[i-back] and not ADDRESS_RE.match(lines[i-back]):
                    name = lines[i-back]
                    break
            candidates.append({
                "name": name,
                "street": m.group("street"),
                "city": m.group("city"),
                "state": "NJ",
                "zip": m.group("zip") or "",
            })
    # 2) Attach likely website links (best-effort)
    links = external_links(driver)
    for c in candidates:
        c["website"] = ""
        # attach first link containing part of the name, else first remaining link
        nm = c["name"].lower()
        match = next((u for u in links if any(tok in u.lower() for tok in nm.split() if len(tok) > 3)), None)
        c["website"] = match or (links[0] if links else "")
        c["phone"] = ""
        c["source"] = URL
    # de-dupe by name+street
    seen, out = set(), []
    for r in candidates:
        key = (r["name"].lower(), r["street"].lower())
        if key not in seen:
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

    # Wait for network/JS; then scroll to force lazy content to render
    WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(4)
    scroll_page(driver)

    # Save top-level rendered HTML for troubleshooting
    with open("crc_rendered_top.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    # First pass at top-level DOM
    rows = try_collect(driver)

    # If too few, check if content lives in an iframe; iterate iframes
    if len(rows) < 10:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for idx, iframe in enumerate(iframes):
            try:
                driver.switch_to.frame(iframe)
                time.sleep(2)
                scroll_page(driver, steps=4, pause=0.6)
                # save per-iframe HTML
                with open(f"crc_iframe_{idx}.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                rows.extend(try_collect(driver))
                driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()

    # Finalize
    # Keep only rows that have both name and street
    cleaned = [r for r in rows if r["name"] and r["street"]]
    # De-dupe again
    seen, dedup = set(), []
    for r in cleaned:
        key = (r["name"].lower(), r["street"].lower())
        if key not in seen:
