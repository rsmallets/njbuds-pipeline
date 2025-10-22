#This script went through each website using duckduckgo and pulled home url

import time, os
import pandas as pd
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

INPUT = "nj_dispensaries.csv"
OUTPUT = "nj_dispensaries_with_websites.csv"
CHECKPOINT_EVERY = 20

# Avoid picking these domains as “official site”
BAN_HOSTS = (
    "facebook.com","instagram.com","twitter.com","x.com","youtube.com","tiktok.com","linktr.ee",
    "google.com","maps.google.","bing.com","mapquest.com","apple.com","nj.gov","my.atlist.com",
    "weedmaps.com","leafly.com","iheartjane.com","dutchie.com","menus.","menufy.com","doordash.com","grubhub.com",
    "yelp.com","tripadvisor.com","waze.com","uber.com","lyft.com","postmates.com","square.site"
)

def host(u: str) -> str:
    try: return urlparse(u).netloc.lower()
    except: return ""

def is_banned(u: str) -> bool:
    h = host(u or "")
    return any(b in h for b in BAN_HOSTS)

def canonical(u: str) -> str:
    if not u: return ""
    u = u.strip()
    if not u.startswith(("http://","https://")):
        u = "https://" + u
    p = urlparse(u)
    path = (p.path or "")
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}"

def load_df():
    if not os.path.exists(INPUT):
        raise FileNotFoundError(f"{INPUT} not found in {os.getcwd()}")
    df = pd.read_csv(INPUT)

    # Ensure all expected columns exist
    for col in ["name","street","city","state","zip","website","phone","source"]:
        if col not in df.columns:
            df[col] = ""

    # Treat NaN as empty strings (CRITICAL FIX)
    df["website"] = df["website"].astype(str)
    df["website"] = df["website"].replace({"nan": "", "NaN": "", "None": ""})
    df["website"] = df["website"].where(df["website"].notna(), "")

    return df

def bootstrap_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1280,1000")
    opts.add_argument("--lang=en-US")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    return driver

def ddg_query(driver, q: str):
    driver.get("https://duckduckgo.com/?va=j&t=h_&ia=web")
    time.sleep(1.5)
    # New UI first
    boxes = driver.find_elements(By.ID, "searchbox_input")
    if boxes:
        boxes[0].clear(); boxes[0].send_keys(q)
        btns = driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")
        (btns[0] if btns else boxes[0]).click()
    else:
        # Classic UI fallback
        box = driver.find_element(By.ID, "search_form_input_homepage")
        box.clear(); box.send_keys(q)
        driver.find_element(By.ID, "search_button_homepage").click()
    time.sleep(1.8)

def ddg_top_links(driver, max_links=10):
    links = []

    # New UI selector
    for a in driver.find_elements(By.CSS_SELECTOR, "a[data-testid='result-title-a']"):
        href = a.get_attribute("href") or ""
        if href.startswith("http"):
            links.append(href)
        if len(links) >= max_links:
            break

    # Classic UI fallback
    if len(links) < 2:
        for a in driver.find_elements(By.CSS_SELECTOR, "a.result__a"):
            href = a.get_attribute("href") or ""
            if href.startswith("http"):
                links.append(href)
            if len(links) >= max_links:
                break

    # De-dupe & canonicalize
    seen, out = set(), []
    for u in links:
        cu = canonical(u)
        if cu and cu not in seen:
            seen.add(cu); out.append(cu)
    return out

def pick_best(cands):
    for u in cands:
        if not is_banned(u):
            return u
    return cands[0] if cands else ""

def main():
    df = load_df()
    total = len(df)
    print(f"Loaded {total} rows")

    # Resume: if OUTPUT exists, carry over any websites already found
    if os.path.exists(OUTPUT):
        prev = pd.read_csv(OUTPUT)
        # normalize prev too
        if "website" in prev.columns:
            prev["website"] = prev["website"].astype(str).replace({"nan":"","NaN":"","None":""}).fillna("")
        prev_key = (prev["name"].str.lower().fillna("") + "|" +
                    prev["street"].str.lower().fillna("") + "|" +
                    prev["city"].str.lower().fillna(""))
        cur_key  = (df["name"].str.lower().fillna("")  + "|" +
                    df["street"].str.lower().fillna("")  + "|" +
                    df["city"].str.lower().fillna(""))
        mapping = dict(zip(prev_key, prev.get("website","")))
        df["__key"] = cur_key
        df["website"] = df.apply(lambda r: mapping.get(r["__key"], r.get("website","")), axis=1)
        df.drop(columns=["__key"], inplace=True)

    driver = bootstrap_driver(headless=False)  # set True to hide the browser
    filled = 0
    started = time.time()

    for i, row in df.iterrows():
        current_site = str(row.get("website","") or "").strip()
        if current_site:
            # already has a site; skip
            continue

        name = str(row.get("name","")).strip()
        city = str(row.get("city","")).strip()
        if not name:
            continue

        q = f'{name} {city} NJ dispensary'
        try:
            ddg_query(driver, q)
            links = ddg_top_links(driver, max_links=10)

            # If nothing, try a variant
            if not links:
                q2 = f'{name} {city} New Jersey cannabis'
                ddg_query(driver, q2)
                links = ddg_top_links(driver, max_links=10)

            best = pick_best(links)
            if best:
                df.at[i, "website"] = best
                filled += 1
        except Exception as e:
            # don’t crash the whole run on one failure
            print(f"Row {i+1} error: {e}")

        # progress + checkpoint
        if (i+1) % 10 == 0:
            elapsed = int(time.time() - started)
            print(f"[{i+1}/{total}] websites added so far: {filled} (elapsed {elapsed}s)")
        if (i+1) % CHECKPOINT_EVERY == 0:
            df.to_csv(OUTPUT, index=False, encoding="utf-8")
            print(f"Checkpoint written → {OUTPUT}")

        time.sleep(1.2)  # be polite to DDG; increase if you see blocking

    driver.quit()
    df.to_csv(OUTPUT, index=False, encoding="utf-8")
    print(f"Done. Wrote {OUTPUT}")
    print(f"Websites added this run: {filled}")

if __name__ == "__main__":
    main()
