import re, time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

CRC_URL = "https://www.nj.gov/cannabis/dispensaries/find/"
ATLIST_FALLBACK = "https://my.atlist.com/map/8bed33fa-9b8c-4c51-bb33-74cd0d98628a?share=true"
OUTFILE = "nj_dispensaries_medicinal.csv"

ADDR_RE = re.compile(r"""
    ^(?P<street>.+?)\s*,\s*
    (?P<city>[A-Za-z'\.\-\s]+)\s*,\s*
    (?P<state>NJ)\s*
    (?P<zip>\d{5})?
    """, re.VERBOSE)

def js_click_any_with_text(driver, needles):
    # Click ALL elements whose innerText contains any needle (case-insensitive).
    # We execute JS so references don't go stale on re-render.
    script = """
    const needles = arguments[0].map(s => s.toLowerCase());
    let clicked = 0;
    const all = Array.from(document.querySelectorAll('button, a, label, div, span, input'));
    for (const el of all) {
      const t = (el.innerText || el.textContent || '').trim().toLowerCase();
      if (!t) continue;
      if (needles.some(n => t.includes(n))) {
        try { el.click(); clicked++; } catch(e) {}
      }
    }
    return clicked;
    """
    return driver.execute_script(script, needles)

def harvest_text(driver):
    # aggressive scroll to trigger lazy load
    for _ in range(16):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.9)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

    els = driver.find_elements(By.CSS_SELECTOR, "body *")
    lines = [e.text.strip() for e in els if e.text and e.text.strip()]

    externals = []
    for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = (a.get_attribute("href") or "").strip()
        if href.startswith("http") and "nj.gov" not in href.lower():
            if all(s not in href.lower() for s in ["facebook.com","instagram.com","twitter.com","x.com","youtube.com"]):
                externals.append(href)

    # Pair names with following address-looking lines
    rows = []
    for i, ln in enumerate(lines):
        if "NJ" in ln and (re.search(r"\b\d{5}\b", ln) and "," in ln or ", NJ" in ln):
            addr = ln
            # previous non-empty line ≈ name
            name = ""
            j = i - 1
            while j >= 0 and not name:
                cand = lines[j].strip()
                if cand and len(cand) > 2 and "dispensary" not in cand.lower():
                    name = cand
                j -= 1

            m = ADDR_RE.search(addr)
            street = city = state = zipc = ""
            if m:
                street = (m.group("street") or "").strip(" ,")
                city   = (m.group("city") or "").strip(" ,")
                state  = (m.group("state") or "NJ").strip()
                zipc   = (m.group("zip") or "").strip()

            # attach nearby external website if we can guess a domain seen in this block
            website = ""
            block = " ".join(lines[max(0, i-4): i+6]).lower()
            for link in externals:
                dom = re.sub(r"^https?://(www\\.)?", "", link.lower()).split("/")[0]
                if dom and dom in block:
                    website = link
                    break

            if name and (street or city):
                rows.append({"name": name, "street": street, "city": city, "state": state, "zip": zipc,
                             "website": website, "phone": "", "source": ATLIST_FALLBACK})

    # de-dupe by (name, street)
    seen, out = set(), []
    for r in rows:
        key = (r["name"].lower(), r["street"].lower())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def main():
    opts = Options()
    # run non-headless so we can see and interact if needed:
    # comment out the next line to run headless
    # opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    # 1) open CRC page to discover the atlist iframe src
    print("Opening CRC finder…")
    driver.get(CRC_URL)
    time.sleep(4)
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    atlist_src = None
    for f in frames:
        src = f.get_attribute("src") or ""
        if "my.atlist.com/map" in src:
            atlist_src = src
            break
    if not atlist_src:
        atlist_src = ATLIST_FALLBACK
    print("Atlist src:", atlist_src)

    # 2) open the atlist map directly
    driver.get(atlist_src)
    time.sleep(5)

    # 3) click any controls that look like Medicinal
    # try a few common labels; you can add more if you see exact wording in the UI
    labels = ["medicinal", "medical", "medical dispensaries", "medicinal dispensaries", "alternative treatment", "ATC"]
    clicked = js_click_any_with_text(driver, labels)
    print("Clicked elements matching medicinal labels:", clicked)
    time.sleep(8)  # allow list to refresh

    # 4) harvest
    rows = harvest_text(driver)
    print("Harvested candidate rows:", len(rows))

    # 5) write csv
    df = pd.DataFrame(rows, columns=["name","street","city","state","zip","website","phone","source"])
    df.to_csv(OUTFILE, index=False, encoding="utf-8")
    print(f"Wrote {OUTFILE} with {len(df)} rows")

    driver.quit()

if __name__ == "__main__":
    main()
