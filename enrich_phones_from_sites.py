import csv, re, time, os, sys, random
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

INPUT  = "nj_dispensaries_with_websites.csv"   # your file with websites
OUTPUT = "nj_dispensaries_with_phones.csv"     # new file with phone numbers filled
CHECKPOINT_EVERY = 25

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NJBudsPhoneEnricher/1.0"
TIMEOUT = 12
SLEEP_BETWEEN = (1.0, 2.0)  # polite delays between domains

CONTACT_PATHS = [
    "/contact", "/contact-us", "/contactus",
    "/locations", "/location", "/store", "/stores",
    "/about", "/about-us"
]

# domains we don't want to treat as "official" sites
BAN_HOSTS = (
    "facebook.com","instagram.com","twitter.com","x.com","youtube.com","tiktok.com","linktr.ee",
    "nj.gov","my.atlist.com",
    "google.com","maps.google.","bing.com","mapquest.com","apple.com","waze.com",
    "yelp.com","tripadvisor.com","square.site"
)

# directories we may need to parse (or jump from) if no brand site exists
DIR_FALLBACK = ("weedmaps.com","leafly.com","iheartjane.com","dutchie.com")

PHONE_RE = re.compile(r"""
    (?:
      \+?1[\s\-\.\)]*?            # optional +1
    )?
    (?:\(?\d{3}\)?[\s\-\.\)]*?)   # area code
    \d{3}[\s\-\.\)]*?\d{4}        # local
""", re.VERBOSE)

def norm(s): return (s or "").strip()
def is_http(u): return bool(u) and (u.startswith("http://") or u.startswith("https://"))

def canonical(u):
    if not u: return ""
    u = u.strip()
    if not is_http(u):
        u = "https://" + u
    p = urlparse(u)
    path = (p.path or "")
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}"

def host(u):
    try: return urlparse(u).netloc.lower()
    except: return ""

def is_banned(u):  return any(b in host(u) for b in BAN_HOSTS)
def is_dir(u):     return any(d in host(u) for d in DIR_FALLBACK)

def load_rows(path):
    if not os.path.exists(path):
        print(f"ERROR: {path} not found"); sys.exit(1)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # ensure columns exist
    for r in rows:
        for k in ("name","street","city","state","zip","website","phone","source"):
            r.setdefault(k, "")
        # normalize NaNs-as-text
        if str(r["website"]).lower() in ("nan","none"): r["website"] = ""
        if str(r["phone"]).lower()   in ("nan","none"): r["phone"]   = ""
    return rows

def write_rows(path, rows):
    fieldnames = ["name","street","city","state","zip","website","phone","source"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k,"") for k in fieldnames})

def get(url, session=None):
    s = session or requests.Session()
    try:
        r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return r
    except Exception:
        return None

def extract_phones_from_html(html_text):
    phones = set()
    for m in PHONE_RE.finditer(html_text or ""):
        digits = re.sub(r"\D","", m.group(0))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            phones.add(f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")
    return list(phones)

def extract_phones_and_links(html):
    soup = BeautifulSoup(html, "lxml")
    # tel: links
    phones = set()
    for a in soup.select("a[href^='tel:']"):
        num = a.get("href","").split("tel:")[-1]
        num = re.sub(r"\D","", num)
        if len(num) == 11 and num.startswith("1"):
            num = num[1:]
        if len(num) == 10:
            phones.add(f"({num[:3]}) {num[3:6]}-{num[6:]}")

    # visible text phones (if no tel:)
    if not phones:
        phones.update(extract_phones_from_html(soup.get_text(" ", strip=True)))

    # collect external links (possibly to brand site from a directory)
    links = []
    for a in soup.select("a[href]"):
        href = (a.get("href","")).strip()
        if not is_http(href): 
            continue
        links.append(href)
    return list(phones), links

def crawl_brand_site_for_phone(site_url):
    """
    Fetch homepage; if no phone, try common contact/location/about pages.
    Return (final_site, phone)
    """
    if not site_url: return ("","")
    start = canonical(site_url)
    base  = f"{urlparse(start).scheme}://{urlparse(start).netloc}"

    with requests.Session() as s:
        s.headers.update({"User-Agent": UA})

        # Homepage
        r = get(start, s) or get(base, s)
        if not r: return (start, "")
        final_site = canonical(r.url)

        phones, _ = extract_phones_and_links(r.text)
        if phones: return (final_site, phones[0])

        # Try contact-like pages
        for path in CONTACT_PATHS:
            u = urljoin(base, path)
            r2 = get(u, s)
            if not r2: continue
            phones2, _ = extract_phones_and_links(r2.text)
            if phones2:
                return (final_site, phones2[0])

        return (final_site, "")

def try_directory_then_brand(dir_url):
    """
    If we only have a directory page (weedmaps/leafly/iheartjane/dutchie),
    try to find a brand domain in its links; if found, crawl that brand site.
    Otherwise, attempt to parse a phone from the directory page itself.
    """
    r = get(dir_url)
    if not r: 
        return (dir_url, "")
    phones, links = extract_phones_and_links(r.text)
    # if directory itself exposes a phone, return it
    if phones:
        return (dir_url, phones[0])
    # else try to find a brand site to hop to
    brand = ""
    for href in links:
        if not is_dir(href) and not is_banned(href):
            brand = canonical(href); break
    if brand:
        return crawl_brand_site_for_phone(brand)
    return (dir_url, "")

def enrich_row(row):
    website = norm(row.get("website"))
    phone   = norm(row.get("phone"))

    if not website:
        return row, False  # nothing to do

    # if it's a directory, special flow
    if is_dir(website):
        final_site, found_phone = try_directory_then_brand(website)
    else:
        final_site, found_phone = crawl_brand_site_for_phone(website)

    changed = False
    # update website if it redirected to a cleaner canonical
    if final_site and final_site != website:
        row["website"] = final_site
        changed = True
    # fill missing phone (do not overwrite non-empty)
    if (not phone) and found_phone:
        row["phone"] = found_phone
        changed = True

    return row, changed

def main():
    rows = load_rows(INPUT)
    out = []
    changed_count = 0

    for i, r in enumerate(rows, start=1):
        r2, changed = enrich_row(r.copy())
        out.append(r2)
        if changed:
            changed_count += 1

        if i % 10 == 0:
            print(f"[{i}/{len(rows)}] updated rows so far: {changed_count}")
        if i % CHECKPOINT_EVERY == 0:
            write_rows(OUTPUT, out)
            print(f"Checkpoint written → {OUTPUT}")

        time.sleep(random.uniform(*SLEEP_BETWEEN))  # be polite

    write_rows(OUTPUT, out)
    print(f"Done. Wrote {OUTPUT}")
    print(f"Rows updated (website or phone): {changed_count}")

if __name__ == "__main__":
    main()
