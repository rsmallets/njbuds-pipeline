import csv, re, time, sys, os
import concurrent.futures as cf
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

INPUT  = "nj_dispensaries.csv"                # your current (clean) file
OUTPUT = "nj_dispensaries_enriched.csv"       # new file will be written
LOG    = "enrich_log.txt"

# -------- Settings --------
TIMEOUT = 12
MAX_WORKERS = 10
PAUSE_BETWEEN_DOMAINS = 0.2  # politeness (seconds)

# Try these contact-like paths in addition to the homepage
CONTACT_PATHS = [
    "/contact", "/contact-us", "/contactus", "/locations", "/location", "/about", "/about-us"
]

# Optional: try to guess a domain if website is missing (OFF by default)
ENABLE_GUESSING = False
GUESS_TLDS = [".com", ".org", ".net"]
# --------------------------

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NJBudsSiteBot/1.0"

PHONE_RE = re.compile(r"""
    (?:
      (?:\+?1[\s\-\.\)]*)?              # optional country code
      (?:\(?\d{3}\)?[\s\-\.\)]*)        # area code
      \d{3}[\s\-\.\)]*\d{4}             # local number
    )
""", re.VERBOSE)

SOCIAL_HOSTS = ("facebook.com","instagram.com","twitter.com","x.com","youtube.com","tiktok.com","linktr.ee")

def load_rows(path):
    if not os.path.exists(path):
        print(f"ERROR: {path} not found"); sys.exit(1)
    with open(path, newline="", encoding="utf-8") as f:
        r = list(csv.DictReader(f))
    for row in r:
        for k in ("name","street","city","state","zip","website","phone","source"):
            row.setdefault(k, "")
    return r

def norm_phone(s):
    digits = re.sub(r"\D", "", s or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return s.strip() if s else ""

def canonical_url(u):
    if not u: return ""
    u = u.strip()
    if not u.startswith(("http://","https://")):
        u = "https://" + u
    # strip fragments, keep scheme/host/path
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}{p.path or ''}"

def base_origin(u):
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""

def is_social(u):
    try:
        host = urlparse(u).netloc.lower()
        return any(h in host for h in SOCIAL_HOSTS)
    except Exception:
        return False

def request_url(u, session):
    try:
        return session.get(u, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
    except Exception:
        return None

def html_phones(soup):
    # 1) tel: links
    phones = set()
    for a in soup.select("a[href^='tel:']"):
        href = a.get("href","")
        num = href.split("tel:")[-1]
        if num: phones.add(norm_phone(num))

    # 2) visible text patterns
    txt = soup.get_text(separator=" ", strip=True)
    for m in PHONE_RE.finditer(txt):
        phones.add(norm_phone(m.group(0)))
    # prune obviously short/bad
    phones = {p for p in phones if re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", p)}
    return list(phones)

def crawl_for_contact(website):
    """
    Returns (final_website, best_phone)
    final_website: site after redirects (homepage)
    best_phone: formatted phone found on homepage/contact-like pages
    """
    if not website:
        return ("","")

    start = canonical_url(website)
    base = base_origin(start)
    if not base:
        return (website, "")

    with requests.Session() as s:
        s.headers.update({"User-Agent": UA})

        # fetch homepage
        r = request_url(base, s)
        if not r or (r.status_code >= 400):
            # try full start if base failed
            r = request_url(start, s)
        if not r or (r.status_code >= 400):
            return (website, "")

        final_home = r.url  # after redirects
        best_phone = ""
        try:
            soup = BeautifulSoup(r.text, "lxml")
            phones = html_phones(soup)
            if phones:
                best_phone = phones[0]
        except Exception:
            pass

        # if no phone yet, try contact-like pages
        if not best_phone:
            for path in CONTACT_PATHS:
                url = urljoin(base_origin(final_home), path)
                r2 = request_url(url, s)
                if not r2 or (r2.status_code >= 400):
                    continue
                try:
                    soup2 = BeautifulSoup(r2.text, "lxml")
                    phones2 = html_phones(soup2)
                    if phones2:
                        best_phone = phones2[0]
                        break
                except Exception:
                    continue

        time.sleep(PAUSE_BETWEEN_DOMAINS)
        return (final_home, best_phone)

def guess_website(name):
    # extremely conservative guesser (disabled by default)
    if not name: return ""
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(slug) < 4:
        return ""
    for tld in GUESS_TLDS:
        u = f"https://{slug}{tld}"
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=8)
            if r.status_code < 400:
                return u
        except Exception:
            continue
    return ""

def worker(row):
    name = (row.get("name") or "").strip()
    website = (row.get("website") or "").strip()
    phone = (row.get("phone") or "").strip()

    if not website and not ENABLE_GUESSING:
        return (row, False, False)  # nothing to do

    if not website and ENABLE_GUESSING:
        website = guess_website(name)

    if not website:
        return (row, False, False)

    final_site, found_phone = crawl_for_contact(website)

    changed_site = False
    changed_phone = False

    if final_site and final_site.strip() and final_site.strip() != website.strip():
        row["website"] = final_site
        changed_site = True

    if (not phone or not phone.strip()) and found_phone:
        row["phone"] = found_phone
        changed_phone = True

    return (row, changed_site, changed_phone)

def main():
    rows = load_rows(INPUT)

    updated_site = 0
    updated_phone = 0

    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(worker, r.copy()) for r in rows]
        new_rows = []
        for fut in cf.as_completed(futures):
            row, cs, cp = fut.result()
            new_rows.append(row)
            if cs: updated_site += 1
            if cp: updated_phone += 1

    # Preserve original order as much as possible
    # (as_completed scrambles order; re-key by (name, street, city))
    key = lambda r: (r["name"].lower(), (r["street"] or "").lower(), (r["city"] or "").lower())
    index = { key(r): i for i, r in enumerate(rows) }
    new_rows.sort(key=lambda r: index.get(key(r), 10**9))

    # Write output
    fieldnames = ["name","street","city","state","zip","website","phone","source"]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in new_rows:
            w.writerow({k: r.get(k,"") for k in fieldnames})

    with open(LOG, "w", encoding="utf-8") as f:
        f.write(f"Updated website on {updated_site} rows\n")
        f.write(f"Filled phone on   {updated_phone} rows\n")

    print(f"Wrote {OUTPUT}")
    print(f"Updated website on {updated_site} rows")
    print(f"Filled phone on   {updated_phone} rows")
    print(f"See {LOG} for a tiny summary.")

if __name__ == "__main__":
    main()
