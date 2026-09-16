import os, time, requests
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

API_KEY = os.getenv("DATA_GOV_API_KEY")
DB_URL = os.getenv("SUPABASE_DB_URL")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

# What we QUERY from API (these are search terms)
COMMODITIES_TO_FETCH = [
    "Onion",
    "Tomato",
    "Potato",
    "Garlic",
    "Ginger(Green)",
    "Green Chilli",
    "Brinjal",
    "Coriander"
]

# What we actually KEEP after fetching - EXACT names only
# This removes Green Gram, Green Peas junk
FINAL_ALLOWED = [
    "Onion",
    "Tomato",
    "Potato",
    "Garlic",
    "Ginger(Green)",
    "Green Chilli",
    "Brinjal",
    "Coriander(Leaves)"
]

ALLOWED_MARKETS = [
    'Bhopal APMC','Berasia APMC','Obedullaganj APMC','Sehore APMC','Raisen APMC',
    'Vidisha APMC','Hoshangabad APMC','Ashta APMC','Itarsi APMC','Ganj Basoda APMC',
    'Sarangpur(F&V) APMC','Haatpipliya (F&V) APMC','Dewas APMC',
    'Gadarwada (F&V) APMC','Sagar APMC','Narsinghpur APMC','Ujjain APMC',
    'Indore APMC','Indore(F&V) APMC','Deori (F&V) APMC'
]

def get_session():
    s = requests.Session()
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[429,500,502,503,504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

SESSION = get_session()

def parse_date(s):
    try: return datetime.strptime(s, "%d/%m/%Y").date()
    except: return None

def fetch_mp(commodity):
    all_recs, offset, limit = [], 0, 100
    while True:
        params = {
            "api-key": API_KEY,
            "format": "json",
            "limit": limit,
            "offset": offset,
            "filters[state.keyword]": "Madhya Pradesh",
            "filters[commodity]": commodity
        }
        print(f"Fetching {commodity} offset {offset}...")
        try:
            r = SESSION.get(BASE_URL, params=params, timeout=60, headers={"User-Agent":"Mozilla/5.0"})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"Timeout {commodity} {offset}: {e} retrying")
            time.sleep(5)
            continue
        batch = data.get("records", [])
        if not batch: break
        all_recs.extend(batch)
        total = int(data.get("total", 0))
        print(f"  -> Got {len(batch)} | Total {total}")
        offset += limit
        if offset >= total: break
        time.sleep(1)
    return all_recs

def clean_and_filter(records, requested):
    cleaned = []
    for rec in records:
        market = rec.get("market")
        commodity = rec.get("commodity")
        # STRICT EXACT MATCH - This kills Green Gram
        if commodity not in FINAL_ALLOWED:
            continue
        # Also ensure it matches what we asked (except Coriander -> Coriander(Leaves))
        if requested == "Coriander" and commodity != "Coriander(Leaves)":
            continue
        if requested != "Coriander" and commodity != requested:
            # For Ginger(Green) and Green Chilli we need exact
            if not (requested in ["Ginger(Green)", "Green Chilli"] and commodity == requested):
                if commodity != requested:
                    continue
        if market not in ALLOWED_MARKETS:
            continue
        arrival = parse_date(rec.get("arrival_date") or "")
        if not arrival: continue
        def to_num(x):
            try: return float(str(x).replace(",","").strip())
            except: return None
        cleaned.append((
            rec.get("state"), rec.get("district"), market,
            commodity, rec.get("variety") or "Other",
            rec.get("grade") or "FAQ",
            arrival,
            to_num(rec.get("min_price")),
            to_num(rec.get("max_price")),
            to_num(rec.get("modal_price"))
        ))
    return cleaned

def upsert(rows):
    if not rows:
        print("No rows")
        return
    # DEDUP
    deduped = {}
    for r in rows:
        key = (r[2], r[3], r[4], r[5], r[6])
        deduped[key] = r
    rows = list(deduped.values())
    print(f"After dedup: {len(rows)} unique rows")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    q = """
    INSERT INTO mandi_prices (state, district, market, commodity, variety, grade, arrival_date, min_price, max_price, modal_price)
    VALUES %s
    ON CONFLICT (market, commodity, variety, arrival_date, grade)
    DO UPDATE SET min_price=EXCLUDED.min_price, max_price=EXCLUDED.max_price, modal_price=EXCLUDED.modal_price, fetched_at=NOW();
    """
    execute_values(cur, q, rows)
    conn.commit()
    cur.close()
    conn.close()
    print(f"Upserted {len(rows)}")

if __name__ == "__main__":
    all_cleaned = []
    for comm in COMMODITIES_TO_FETCH:
        recs = fetch_mp(comm)
        cleaned = clean_and_filter(recs, comm)
        print(f"{comm}: {len(recs)} fetched, {len(cleaned)} kept after strict filter")
        all_cleaned.extend(cleaned)
    upsert(all_cleaned)