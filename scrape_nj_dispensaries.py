import os, io, sys
import requests
import pandas as pd

JSON_URL     = "https://data.nj.gov/resource/8hz7-zvhn.json"   # may 403 from your network
RESOURCE_CSV = "https://data.nj.gov/resource/8hz7-zvhn.csv"    # most reliable
OUTFILE      = "nj_dispensaries.csv"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NJBudsBot/1.0"

def pick(d, keys, default=""):
    for k in keys:
        if k in d and d[k]:
            return str(d[k]).strip()
    return default

def normalize_rows(rows):
    out = []
    for rec in rows:
        r = {(k.lower() if isinstance(k,str) else k): v for k, v in rec.items()}
        name    = pick(r, ["name","business_name","retailer_name","dispensary_name"])
        street  = pick(r, ["address","street_address","location_address","site_address"])
        city    = pick(r, ["city","municipality","town"])
        state   = pick(r, ["state","st"], "NJ")
        zipc    = pick(r, ["zip","zipcode","postal_code"])
        phone   = pick(r, ["phone","phone_number","telephone"])
        website = pick(r, ["website","website_url","url"])
        if "location" in r and isinstance(r["location"], dict):
            loc = r["location"]
            street = street or loc.get("address","")
            city   = city   or loc.get("city","")
            state  = state  or loc.get("state", state)
            zipc   = zipc   or loc.get("zip","")
        if name and (street or city):
            out.append({
                "name": name, "street": street, "city": city, "state": state,
                "zip": zipc, "phone": phone, "website": website, "source": "data.nj.gov 8hz7-zvhn"
            })
    # de-dupe name+street
    seen, dedup = set(), []
    for r in out:
        key = (r["name"].lower(), r["street"].lower())
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup

def fetch_rows():
    token = os.getenv("NJ_SODA_APP_TOKEN", "").strip()
    headers = {"User-Agent": UA}
    params  = {"$limit": 5000}
    if token:
        headers["X-App-Token"] = token
        params["$$app_token"]  = token  # some portals require param instead of header

    # 1) try JSON (fastest if allowed)
    try:
        rj = requests.get(JSON_URL, params=params, headers=headers, timeout=30)
        if rj.status_code == 200:
            return rj.json()
        else:
            print(f"JSON blocked ({rj.status_code}). Falling back to CSV resource…")
    except Exception as e:
        print("JSON fetch error:", e, "→ Falling back to CSV resource…")

    # 2) resource CSV (most reliable)
    rc = requests.get(RESOURCE_CSV, params=params, headers=headers, timeout=60)
    if rc.status_code != 200:
        # print some diagnostics to help us pivot
        print("CSV resource blocked. Status:", rc.status_code)
        print("First 300 chars of response:\n", rc.text[:300])
        sys.exit(1)

    df = pd.read_csv(io.StringIO(rc.text))
    # convert rows to dicts with lowercase keys for normalize()
    return [{(str(k).lower() if isinstance(k,str) else k): v for k, v in row.items()}
            for row in df.to_dict(orient="records")]

def main():
    print("=== NJ Dispensaries via NJ Open Data (resource CSV with token support) ===")
    rows = fetch_rows()
    print(f"Fetched {len(rows)} raw rows")
    dedup = normalize_rows(rows)
    print(f"Normalized & deduped: {len(dedup)} rows")
    pd.DataFrame(dedup, columns=["name","street","city","state","zip","phone","website","source"]) \
      .to_csv(OUTFILE, index=False, encoding="utf-8")
    print("Wrote", OUTFILE)

if __name__ == "__main__":
    main()
