import re, time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

CRC_URL = "https://www.nj.gov/cannabis/dispensaries/find/"
ATLIST_FALLBACK = "https://my.atlist.com/map/8bed33fa-9b8c-4c51-bb33-74cd0d98628a?share=true"
OUTFILE = "nj_dispensaries.csv"  # overwrite your rec file with cleaner names

ADDR_RE = re.compile(r"""
    ^(?P<street>.+?)\s*,\s*
    (?P<city>[A-Za-z'\.\-\s]+)\s*,\s*
    (?P<state>NJ)\s*
    (?P<zip>\d{5})?
    """, re.VERBOSE)

def js_text_snapshot_len(driver):
    return driver.execute_script("""
        const els = Array.from(document.querySelectorAll('body *'));
        const lines = els.map(el => (el.innerText || '').trim()).filter(Boolean);
        return lines.join('\\n').length;
    """)

def scroll_until_stable(driver, max_rounds=25, settle_rounds=4, pause=0.9):
    prev = -1
    stable = 0
    for _ in range(max_rounds):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(pause * 0.7)
        cur = js_text_snapshot_len(driver)
        if cur == prev:
            stable += 1
            if stable >= settle_rounds:
                break
        else:
            stable = 0
            prev = cur

def harvest_cards(driver, source_url):
    rows = []
    # 1) find every "Get Directions" control (anchor/button)
    direction_links = driver.find_elements(By.XPATH, "//a[contains(., 'Get Directions')] | //button[contains(., 'Get Directions')]")
    seen_cards = set()

    for link in direction_links:
        try:
            # 2) go up to the closest logical card container (div/li/article/section)
            card = link.find_element(By.XPATH, "ancestor::*[self::div or self::li or self::article or self::section][1]")
            card_text_lines = [ln.strip() for ln in card.text.splitlines() if ln.strip()]
            if not card_text_lines:
                continue

            # 3) name = first non-empty line that is not a button label
            name = ""
            for ln in card_text_lines:
                if ln.lower() in ("get directions", "directions", "website", "view website"):
                    continue
                name = ln
                break

            # 4) find the address-looking line within the card
            addr_line = ""
            for ln in card_text_lines:
                if "NJ" in ln and (("," in ln and re.search(r"\b\d{5}\b", ln)) or ", NJ" in ln):
                    addr_line = ln
                    break

            if not name or not addr_line:
                # try the card's first line as name and hope the next line is address
                if len(card_text_lines) >= 2:
                    name = name or card_text_lines[0]
                    addr_line = addr_line or card_text_lines[1]
                else:
                    continue

            # 5) parse address
            m = ADDR_RE.search(addr_line)
            street = city = state = zipc = ""
            if m:
                street = (m.group("street") or "").strip(" ,")
                city   = (m.group("city") or "").strip(" ,")
                state  = (m.group("state") or "NJ").strip()
                zipc   = (m.group("zip") or "").strip()

            # 6) website: prefer a link in this card that isn't social/atlist/nj.gov
            website = ""
            links_in_card = card.find_elements(By.CSS_SELECTOR, "a[href]")
            for a in links_in_card:
                href = (a.get_attribute("href") or "").strip()
                if not href.startswith("http"):
                    continue
                low = href.lower()
                if any(s in low for s in ["facebook.com","instagram.com","twitter.com","x.com","youtube.com","nj.gov","my.atlist.com"]):
                    continue
                website = href
                break

            key = (name.lower(), street.lower(), city.lower())
            if name and (street or city) and key not in seen_cards:
                seen_cards.add(key)
                rows.append({
                    "name": name,
                    "street": street, "city": city, "state": state, "zip": zipc,
                    "website": website, "phone": "", "source": source_url
                })
        except Exception:
            continue
    return rows

def main():
    opts = Options()
    # run visible so you can watch; comment next line to go headless
    # opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")

    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    # Discover atlist src from CRC page
    drv.get(CRC_URL)
    time.sleep(4)
    atlist_src = None
    for f in drv.find_elements(By.TAG_NAME, "iframe"):
        s = f.get_attribute("src") or ""
        if "my.atlist.com/map" in s:
            atlist_src = s
            break
    if not atlist_src:
        atlist_src = ATLIST_FALLBACK
    print("Atlist src:", atlist_src)

    # Open atlist directly and load everything
    drv.get(atlist_src)
    time.sleep(5)
    scroll_until_stable(drv, max_rounds=30, settle_rounds=5, pause=1.0)

    rows = harvest_cards(drv, atlist_src)
    print("Rows harvested (recreational):", len(rows))

    pd.DataFrame(rows, columns=["name","street","city","state","zip","website","phone","source"]) \
      .to_csv(OUTFILE, index=False, encoding="utf-8")
    print(f"Wrote {OUTFILE} with {len(rows)} rows")

    drv.quit()

if __name__ == "__main__":
    main()
