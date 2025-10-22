import re, time, sys, os
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# Input & output
INPUT_CSV  = "nj_dispensaries_medicinal_rec_edit.csv"            # your current file (269 rows)
OUTPUT_CSV = "nj_dispensaries_enriched.csv"   # will be created

# CRC page + Atlist (we’ll discover the iframe src first)
CRC_URL = "https://www.nj.gov/cannabis/dispensaries/find/"
ATLIST_FALLBACK = "https://my.atlist.com/map/8bed33fa-9b8c-4c51-bb33-74cd0d98628a?share=true"

# Labels to ensure both categories are visible (so we enrich all)
ON_LABELS = [
    "medicinal cannabis", "medical cannabis", "medicinal", "medical",
    "adult-use cannabis", "adult use cannabis", "adult-use", "adult use", "recreational"
]

PHONE_RE = re.compile(r"""
    (?:
      \+?1[\s\-\.\)]*      # optional country code
    )?
    (?:\(?\d{3}\)?[\s\-\.\)]*) # area code
    \d{3}[\s\-\.\)]*\d{4}
""", re.VERBOSE)

def norm(s):
    return (s or "").strip().lower()

def load_base():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found in {os.getcwd()}")
        sys.exit(1)
    df = pd.read_csv(INPUT_CSV)
    # normalize expected columns
    for col in ["name","street","city","state","zip","website","phone","source"]:
        if col not in df.columns:
            df[col] = ""
    return df

def js_find_buttons_by_text(drv, labels):
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

def js_get_list_container(drv):
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

def extract_card_contacts(drv):
    """
    Returns list of dicts keyed by (name, street, city) with website/phone if present.
    """
    contacts = {}
    # find each card via "Get Directions"
    direction_links = drv.find_elements(
        By.XPATH, "//a[contains(., 'Get Directions')] | //button[contains(., 'Get Directions')]"
    )
    for link in direction_links:
        try:
            card = link.find_element(By.XPATH, "ancestor::*[self::div or self::li or self::article or self::section][1]")
            text_lines = [ln.strip() for ln in card.text.splitlines() if ln.strip()]
            if not text_lines: 
                continue

            # name = first non-button line
            name = ""
            for ln in text_lines:
                low = ln.lower()
                if low in ("get directions", "directions", "website", "view website"): 
                    continue
                name = ln; break

            # address line
            addr = ""
            for ln in text_lines:
                if "NJ" in ln and (("," in ln and re.search(r"\b\d{5}\b", ln)) or ", NJ" in ln):
                    addr = ln; break

            if not name or not addr:
                continue

            # split address to street/city
            street = city = ""
            parts = [p.strip() for p in addr.split(",")]
            if len(parts) >= 2:
                street = parts[0]
                city   = parts[-2] if len(parts) >= 2 else ""

            # website inside this card
            website = ""
            phone = ""

            links = card.find_elements(By.CSS_SELECTOR, "a[href]")
            for a in links:
                href = (a.get_attribute("href") or "").strip()
                if href.lower().startswith("tel:"):
                    phone = re.sub(r"[^0-9+]", "", href.replace("tel:", ""))
                    continue
                if not href.startswith("http"): 
                    continue
                low = href.lower()
                if any(s in low for s in ["facebook.com","instagram.com","twitter.com","x.com","youtube.com","nj.gov","my.atlist.com"]):
                    continue
                if not website:
                    website = href  # first decent external link wins

            # also try phone from visible text in the card
            if not phone:
                joined = " ".join(text_lines)
                m = PHONE_RE.search(joined)
                if m: phone = m.group(0)

            key = (norm(name), norm(street), norm(city))
            contacts[key] = {
                "name": name, "street": street, "city": city,
                "website": website, "phone": phone
            }
        except Exception:
            continue

    return contacts

def main():
    base = load_base()
    keycols = ["name","street","city"]
    base["_key"] = base.apply(lambda r: (norm(r["name"]), norm(r["street"]), norm(r["city"])), axis=1)

    # build selenium
    opts = Options()
    # Run visible so you can watch; comment next line to go headless
    # opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1440,1000")

    drv = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    # Find Atlist src from CRC page
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

    # Open Atlist, turn BOTH categories ON (so we enrich all rows)
    drv.get(atlist_src)
    time.sleep(6)
    js_zoom_out(drv, times=6)

    nodes = js_find_buttons_by_text(drv, ON_LABELS) or []
    # try to set them ON a couple times in case of re-rendering
    for _ in range(2):
        for n in nodes:
            js_set_button_state(drv, n, want_on=True)
        time.sleep(1.0)

    # Scroll list pane until fully loaded
    container = js_get_list_container(drv)
    if container:
        scroll_list_until_stable(drv, container, max_rounds=45, settle_rounds=6, pause=0.9)
    else:
        # fallback: whole window
        for _ in range(14):
            drv.execute_script("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.0)
            drv.execute_script("window.scrollTo(0, 0);"); time.sleep(0.7)

    # Extract contacts from all cards
    contacts = extract_card_contacts(drv)
    drv.quit()

    # Merge back into base where website/phone missing
    updated_web = updated_phone = 0
    for i, r in base.iterrows():
        key = r["_key"]
        if key in contacts:
            c = contacts[key]
            if (pd.isna(r["website"]) or not str(r["website"]).strip()) and c.get("website"):
                base.at[i, "website"] = c["website"]; updated_web += 1
            if (pd.isna(r["phone"]) or not str(r["phone"]).strip()) and c.get("phone"):
                base.at[i, "phone"] = c["phone"]; updated_phone += 1

    base.drop(columns=["_key"], inplace=True)
    base.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"Wrote {OUTPUT_CSV}")
    print(f"Filled website: {updated_web} rows")
    print(f"Filled phone:   {updated_phone} rows")

if __name__ == "__main__":
    main()
