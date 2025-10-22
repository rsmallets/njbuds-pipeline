#This script will scrape all sites recreational and medicinal

import re, time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# --- Config ---
CRC_URL = "https://www.nj.gov/cannabis/dispensaries/find/"
ATLIST_FALLBACK = "https://my.atlist.com/map/8bed33fa-9b8c-4c51-bb33-74cd0d98628a?share=true"
OUTFILE = "nj_dispensaries_medicinal.csv"

# Exact labels we’ll target on the Atlist page
OFF_LABELS = [
    "adult-use cannabis", "adult use cannabis", "adult-use", "adult use", "recreational"
]
ON_LABELS = [
    "medicinal cannabis", "medical cannabis", "medicinal", "medical",
    "alternative treatment", "atc", "atcs"
]

# --- Helpers ---
ADDR_RE = re.compile(r"""
    ^(?P<street>.+?)\s*,\s*
    (?P<city>[A-Za-z'\.\-\s]+)\s*,\s*
    (?P<state>NJ)\s*
    (?P<zip>\d{5})?
    """, re.VERBOSE)

def js_find_buttons_by_text(drv, labels):
    """Return DOM nodes whose visible text matches any label (case-insensitive)."""
    script = """
    const labels = arguments[0].map(s => s.toLowerCase());
    const nodes = [];
    const all = Array.from(document.querySelectorAll('button, [role="button"], a, label, div, span'));
    for (const el of all) {
      const t = (el.innerText || el.textContent || '').trim().toLowerCase();
      if (!t) continue;
      if (labels.some(l => t === l || t.includes(l))) nodes.push(el);
    }
    return nodes;
    """
    return drv.execute_script(script, labels)

def js_set_button_state(drv, node, want_on=True):
    """
    Try to set a toggle-like control to the desired state using aria-pressed or 'active' classes.
    We only click when the current state doesn't match 'want_on'.
    """
    script = """
    const el = arguments[0];
    const wantOn = arguments[1];

    const hasOnClass = (e) => {
      const cls = (e.className || '').toLowerCase();
      return cls.includes('active') || cls.includes('selected') || cls.includes('on');
    };
    const getState = () => {
      const ap = (el.getAttribute('aria-pressed') || '').toLowerCase();
      if (ap === 'true')  return true;
      if (ap === 'false') return false;
      return hasOnClass(el);
    };

    const cur = getState();
    if (cur !== wantOn) {
      try { el.click(); } catch(e) {}
    }
    return true;
    """
    try:
        drv.execute_script(script, node, want_on)
    except Exception:
        pass

def js_zoom_out(drv, times=6):
    """Click any 'Zoom out' control repeatedly to show the whole state."""
    script = """
    const times = arguments[0];
    const labels = ['zoom out','-'];
    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
    let cnt = 0;
    for (let i=0;i<times;i++) {
      let clicked = false;
      for (const b of buttons) {
        const a = (b.getAttribute('aria-label')||'').toLowerCase();
        const t = (b.getAttribute('title')||'').toLowerCase();
        const x = (b.innerText||'').toLowerCase();
        if (labels.some(l => a.includes(l) || t.includes(l) || x === l)) {
          try { b.click(); cnt++; clicked=true; break; } catch(e){}
        }
      }
      if (!clicked) break;
    }
    return cnt;
    """
    try:
        drv.execute_script(script, times)
    except Exception:
        pass

def js_get_list_container_selector(drv):
    """
    Returns the scrollable list container that contains multiple 'Get Directions' cards.
    Uses an IIFE so 'const' works correctly.
    """
    script = """
    return (function(){
      const cands = Array.from(document.querySelectorAll('div, aside, section, ul'));
      for (const el of cands) {
        const style = window.getComputedStyle(el);
        const scrollable = (el.scrollHeight > el.clientHeight + 20) && (/(auto|scroll)/.test(style.overflowY));
        const txt = (el.innerText || '');
        const matches = (txt.match(/Get Directions/g) || []).length;
        if (scrollable && matches >= 2) return el;
      }
      let best = null, bestH = 0;
      for (const el of cands) {
        const txt = (el.innerText || '');
        if (txt.includes('Get Directions')) {
          const h = el.scrollHeight;
          if (h > bestH) { best = el; bestH = h; }
        }
      }
      return best;
    })();
    """
    return drv.execute_script(script)

def scroll_list_until_stable(drv, container, max_rounds=40, settle_rounds=6, pause=0.9):
    """Scroll the detected list pane until text length stops changing."""
    prev_len = -1
    stable = 0
    for _ in range(max_rounds):
        try:
            drv.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
            time.sleep(pause)
            drv.execute_script("arguments[0].scrollTop = 0;", container)
            time.sleep(pause * 0.7)
            cur_len = drv.execute_script("return (arguments[0].innerText || '').length;", container)
            if cur_len == prev_len:
                stable += 1
                if stable >= settle_rounds:
                    break
            else:
                stable = 0
                prev_len = cur_len
        except Exception:
            break

def harvest_cards(drv, source_url):
    """Card-aware extraction: find each card via 'Get Directions', then parse name/address/website."""
    rows = []
    direction_links = drv.find_elements(
        By.XPATH, "//a[contains(., 'Get Directions')] | //button[contains(., 'Get Directions')]"
    )
    seen = set()

    for link in direction_links:
        try:
            card = link.find_element(By.XPATH, "ancestor::*[self::div or self::li or self::article or self::section][1]")
            card_lines = [ln.strip() for ln in card.text.splitlines() if ln.strip()]
            if not card_lines:
                continue

            # Name = first non-button-ish line
            name = ""
            for ln in card_lines:
                low = ln.lower()
                if low in ("get directions", "directions", "website", "view website"):
                    continue
                name = ln
                break

            # Address line inside the card
            addr_line = ""
            for ln in card_lines:
                if "NJ" in ln and (("," in ln and re.search(r"\b\d{5}\b", ln)) or ", NJ" in ln):
                    addr_line = ln
                    break
            if not name or not addr_line:
                if len(card_lines) >= 2:
                    name = name or card_lines[0]
                    addr_line = addr_line or card_lines[1]
                else:
                    continue

            # Parse address
            m = ADDR_RE.search(addr_line)
            street = city = state = zipc = ""
            if m:
                street = (m.group("street") or "").strip(" ,")
                city   = (m.group("city") or "").strip(" ,")
                state  = (m.group("state") or "NJ").strip()
                zipc   = (m.group("zip") or "").strip()

            # Website in this card (non-social, non-gov, non-atlist)
            website = ""
            for a in card.find_elements(By.CSS_SELECTOR, "a[href]"):
                href = (a.get_attribute("href") or "").strip()
                if not href.startswith("http"):
                    continue
                low = href.lower()
                if any(s in low for s in ["facebook.com","instagram.com","twitter.com","x.com","youtube.com","nj.gov","my.atlist.com"]):
                    continue
                website = href
                break

            key = (name.lower(), street.lower(), city.lower())
            if name and (street or city) and key not in seen:
                seen.add(key)
                rows.append({
                    "name": name,
                    "street": street, "city": city, "state": state, "zip": zipc,
                    "website": website, "phone": "", "source": source_url
                })
        except Exception:
            continue
    return rows

# --- Main ---
def main():
    opts = Options()
    # Run visible so you can watch; comment the next line to go headless
    # opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")

    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    # Discover Atlist src from the CRC page
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

    # Open Atlist and prep the UI
    drv.get(atlist_src)
    time.sleep(6)
    js_zoom_out(drv, times=6)  # make sure statewide items are visible

    # Try to expose filter UI (non-fatal if nothing to do)
    try:
        _ = js_find_buttons_by_text(drv, ["filters","filter","categories","layers","locations","view all"])
        time.sleep(1.0)
    except Exception:
        pass

    # Deterministically set category states:
    # OFF: Adult-Use/Recreational;  ON: Medicinal/Medical/ATC
    off_nodes = js_find_buttons_by_text(drv, OFF_LABELS) or []
    on_nodes  = js_find_buttons_by_text(drv, ON_LABELS) or []

    for _ in range(2):
        for n in off_nodes:
            js_set_button_state(drv, n, want_on=False)
        time.sleep(0.8)
        for n in on_nodes:
            js_set_button_state(drv, n, want_on=True)
        time.sleep(1.2)

    # Find the list pane and scroll it until stable (lazy-loaded items)
    container = js_get_list_container_selector(drv)
    if container:
        scroll_list_until_stable(drv, container, max_rounds=45, settle_rounds=6, pause=0.9)
    else:
        # Fallback: scroll window if list container not detected
        for _ in range(14):
            drv.execute_script("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.0)
            drv.execute_script("window.scrollTo(0, 0);"); time.sleep(0.7)

    # Harvest and write CSV
    rows = harvest_cards(drv, atlist_src)
    print("Rows harvested (medicinal):", len(rows))

    pd.DataFrame(rows, columns=["name","street","city","state","zip","website","phone","source"]) \
      .to_csv(OUTFILE, index=False, encoding="utf-8")
    print(f"Wrote {OUTFILE} with {len(rows)} rows")

    drv.quit()

if __name__ == "__main__":
    main()
