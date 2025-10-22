import re, time, os, sys
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, JavascriptException

INPUT_CSV  = "nj_dispensaries.csv"              # your current file (269 rows from rec+med scrape)
OUTPUT_CSV = "nj_dispensaries_enriched.csv"     # will be written

CRC_URL = "https://www.nj.gov/cannabis/dispensaries/find/"
ATLIST_FALLBACK = "https://my.atlist.com/map/8bed33fa-9b8c-4c51-bb33-74cd0d98628a?share=true"

ON_LABELS = [
    "adult-use cannabis","adult use cannabis","adult-use","adult use","recreational",
    "medicinal cannabis","medical cannabis","medicinal","medical","atc","alternative treatment"
]

PHONE_RE = re.compile(r"""
    (?:
      \+?1[\s\-\.\)]*?            # optional country code
    )?
    (?:\(?\d{3}\)?[\s\-\.\)]*?)   # area code
    \d{3}[\s\-\.\)]*?\d{4}        # local
""", re.VERBOSE)

SOCIAL = ("facebook.com","instagram.com","twitter.com","x.com","youtube.com","tiktok.com","nj.gov","my.atlist.com")

def norm(s): return (s or "").strip().lower()

def load_csv(path):
    if not os.path.exists(path):
        print(f"ERROR: {path} not found in {os.getcwd()}")
        sys.exit(1)
    df = pd.read_csv(path)
    for col in ["name","street","city","state","zip","website","phone","source"]:
        if col not in df.columns:
            df[col] = ""
    return df

# ------------- JS helpers -------------
def js_find_buttons_by_text(drv, labels):
    script = """
    const labels = arguments[0].map(s => s.toLowerCase());
    const out = [];
    const all = Array.from(document.querySelectorAll('button,[role="button"],a,label,div,span'));
    for (const el of all) {
      const t = (el.innerText || el.textContent || '').trim().toLowerCase();
      if (!t) continue;
      if (labels.some(l => t === l || t.includes(l))) out.push(el);
    }
    return out;
    """
    return drv.execute_script(script, labels)

def js_set_button_state(drv, node, want_on=True):
    script = """
    const el = arguments[0]; const wantOn = arguments[1];
    const hasOnClass = (e) => ((e.className||'').toLowerCase().match(/active|selected|on/) !== null);
    const getState = () => {
      const ap = (el.getAttribute('aria-pressed')||'').toLowerCase();
      if (ap === 'true') return true;
      if (ap === 'false') return false;
      return hasOnClass(el);
    };
    const cur = getState();
    if (cur !== wantOn) { try { el.click(); } catch(e) {} }
    return true;
    """
    try: drv.execute_script(script, node, want_on)
    except Exception: pass

def js_zoom_out(drv, times=6):
    script = """
    const times = arguments[0];
    const candidates = Array.from(document.querySelectorAll('button,[role="button"]'));
    for (let i=0;i<times;i++){
      let clicked=false;
      for (const b of candidates){
        const a=(b.getAttribute('aria-label')||'').toLowerCase();
        const t=(b.getAttribute('title')||'').toLowerCase();
        const x=(b.innerText||'').toLowerCase();
        if (a.includes('zoom out') || t.includes('zoom out') || x === '-') { try{b.click();}catch(e){} clicked=true; break; }
      }
      if (!clicked) break;
    }
    return true;
    """
    try: drv.execute_script(script, times)
    except Exception: pass

def js_get_list_container(drv):
    script = """
    return (function(){
      const cands = Array.from(document.querySelectorAll('div,aside,section,ul'));
      for (const el of cands) {
        const style = window.getComputedStyle(el);
        const scrollable = (el.scrollHeight > el.clientHeight + 20) && (/(auto|scroll)/.test(style.overflowY));
        const txt = (el.innerText || '');
        const matches = (txt.match(/Get Directions/g) || []).length;
        if (scrollable && matches >= 2) return el;
      }
      let best=null,bestH=0;
      for (const el of cands) {
        const txt = (el.innerText||'');
        if (txt.includes('Get Directions')) {
          const h = el.scrollHeight;
          if (h>bestH){ best=el; bestH=h; }
        }
      }
      return best;
    })();
    """
    return drv.execute_script(script)

def scroll_list_until_stable(drv, container, rounds=40, settle=6, pause=0.9):
    prev_len = -1; stable = 0
    for _ in range(rounds):
        try:
            drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container); time.sleep(pause)
            drv.execute_script("arguments[0].scrollTop = 0;", container); time.sleep(pause*0.7)
            cur_len = drv.execute_script("return (arguments[0].innerText||'').length;", container)
            if cur_len == prev_len:
                stable += 1
                if stable >= settle: break
            else:
                stable = 0; prev_len = cur_len
        except Exception: break

# ------------- extraction helpers -------------
def parse_phones_from_html(html_text):
    phones = set()
    for m in PHONE_RE.finditer(html_text or ""):
        digits = re.sub(r"\D","", m.group(0))
        if len(digits) == 11 and digits[0] == "1": digits = digits[1:]
        if len(digits) == 10:
            fmt = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            phones.add(fmt)
    return list(phones)

def extract_links_and_phone_from_panel(drv):
    """
    Inspect the visible details panel / overlay:
    - collect tel: links
    - collect external (non-social) website link
    - also parse visible text for phone numbers
    """
    website = ""
    phones = set()

    # try obvious overlay/panel containers
    candidates = drv.find_elements(By.XPATH, "//div[contains(@class,'modal') or contains(@class,'panel') or contains(@class,'drawer') or contains(@class,'inner') or contains(@class,'content') or @role='dialog']")
    if not candidates:
        candidates = drv.find_elements(By.XPATH, "//*")

    # scan a few top candidates
    for el in candidates[:20]:
        try:
            html = el.get_attribute("innerHTML") or ""
        except StaleElementReferenceException:
            continue
        if not html or "Get Directions" not in html and "Directions" not in html and "Website" not in html:
            continue

        # BeautifulSoup parse
        soup = BeautifulSoup(html, "lxml")
        # phones from tel: first
        for a in soup.select("a[href^='tel:']"):
            num = a.get("href","").split("tel:")[-1]
            num = re.sub(r"\D","", num)
            if len(num)==11 and num.startswith("1"): num=num[1:]
            if len(num)==10:
                phones.add(f"({num[:3]}) {num[3:6]}-{num[6:]}")

        # phones from visible text
        phones.update(parse_phones_from_html(soup.get_text(" ", strip=True)))

        # external link for website (avoid socials/nj.gov/atlist)
        if not website:
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href.startswith("http"):
                    continue
                low = href.lower()
                if any(s in low for s in SOCIAL):
                    continue
                website = href
                break

        # stop early if we found phone and website
        if website and phones:
            break

    phone = next(iter(phones), "")
    return website, phone

def click_card_open_panel(drv, card_el):
    try:
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", card_el)
        time.sleep(0.3)
        card_el.click()
        time.sleep(0.8)
        return True
    except Exception:
        return False

def close_panel_if_any(drv):
    # try close buttons / ESC-like elements
    try:
        close_buttons = drv.find_elements(By.XPATH, "//button[contains(.,'Close') or contains(.,'×') or contains(.,'close')]")
        if close_buttons:
            try:
                drv.execute_script("arguments[0].click();", close_buttons[0]); time.sleep(0.6)
                return
            except Exception:
                pass
        # fallback: click outside panel
        drv.execute_script("document.body.click();")
        time.sleep(0.3)
    except Exception:
        pass

def main():
    # Load base
    df = load_csv(INPUT_CSV)
    df["_key"] = df.apply(lambda r: (norm(r["name"]), norm(r["street"]), norm(r["city"])), axis=1)
    base_index = {k:i for i,k in enumerate(df["_key"])}

    # Selenium
    opts = Options()
    # opts.add_argument("--headless=new")  # keep visible while we debug
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")
    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    # Discover Atlist src
    drv.get(CRC_URL); time.sleep(4)
    atlist_src = None
    for f in drv.find_elements(By.TAG_NAME, "iframe"):
        s = f.get_attribute("src") or ""
        if "my.atlist.com/map" in s:
            atlist_src = s; break
    if not atlist_src: atlist_src = ATLIST_FALLBACK
    print("Atlist src:", atlist_src)

    # Load Atlist and show everything (both categories ON)
    drv.get(atlist_src); time.sleep(6)
    js_zoom_out(drv, times=6)
    nodes = js_find_buttons_by_text(drv, ON_LABELS) or []
    for _ in range(2):
        for n in nodes:
            js_set_button_state(drv, n, True)
        time.sleep(1.0)

    # Find list container and fully load it
    container = js_get_list_container(drv)
    if container:
        scroll_list_until_stable(drv, container, rounds=45, settle=6, pause=0.9)
    else:
        # fallback - entire window
        for _ in range(14):
            drv.execute_script("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.0)
            drv.execute_script("window.scrollTo(0, 0);"); time.sleep(0.7)

    # Enumerate cards by their "Get Directions" control
    direction_links = drv.find_elements(By.XPATH, "//a[contains(., 'Get Directions')] | //button[contains(., 'Get Directions')]")
    print("Cards detected:", len(direction_links))

    scraped = {}
    for idx, link in enumerate(direction_links, start=1):
        try:
            card = link.find_element(By.XPATH, "ancestor::*[self::div or self::li or self::article or self::section][1]")
        except NoSuchElementException:
            continue

        # Roughly parse name/street/city from the card to build a key
        text_lines = []
        try:
            text_lines = [ln.strip() for ln in card.text.splitlines() if ln.strip()]
        except Exception:
            pass

        name = ""
        for ln in text_lines:
            low = ln.lower()
            if low in ("get directions","directions","website","view website"): continue
            name = ln; break

        addr_line = ""
        for ln in text_lines:
            if "NJ" in ln and (("," in ln and re.search(r"\b\d{5}\b", ln)) or ", NJ" in ln):
                addr_line = ln; break

        street = city = ""
        if addr_line:
            parts = [p.strip() for p in addr_line.split(",")]
            if len(parts) >= 2:
                street = parts[0]
                city = parts[-2] if len(parts) >= 2 else ""

        key = (norm(name), norm(street), norm(city))
        if not key[0] and not key[1]:
            # skip if we can't identify
            continue

        # Click card to open details panel
        ok = click_card_open_panel(drv, card)
        if not ok:
            continue

        # Extract website/phone from panel
        website, phone = extract_links_and_phone_from_panel(drv)
        close_panel_if_any(drv)

        if website or phone:
            scraped[key] = {"website": website, "phone": phone}

        # small breather to be gentle
        time.sleep(0.15)

    drv.quit()

    # Merge back into df
    updated_web = 0; updated_phone = 0
    for i, r in df.iterrows():
        key = r["_key"]
        if key in scraped:
            add = scraped[key]
            if (not str(r.get("website") or "").strip()) and add.get("website"):
                df.at[i, "website"] = add["website"]; updated_web += 1
            if (not str(r.get("phone") or "").strip()) and add.get("phone"):
                df.at[i, "phone"] = add["phone"]; updated_phone += 1

    df.drop(columns=["_key"], inplace=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"Wrote {OUTPUT_CSV}")
    print(f"Filled website on {updated_web} rows")
    print(f"Filled phone   on {updated_phone} rows")

if __name__ == "__main__":
    main()
