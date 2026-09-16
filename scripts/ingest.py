"""
Bhopal Restaurant Procurement - Final Production Ingestion
Resource: 9ef84268-d588-465a-a308-a864a43d0070
Fixes: timeout infinite loop, Green Gram bug, dedup cardinality, data-quality check
"""
import os
import sys
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests
import psycopg2
from psycopg2.extras import execute_values
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_KEY = os.getenv("DATA_GOV_API_KEY")
DB_URL = os.getenv("SUPABASE_DB_URL")
RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"

# Validation
if not API_KEY:
    logger.error("DATA_GOV_API_KEY not set")
    sys.exit(1)
if not DB_URL:
    logger.error("SUPABASE_DB_URL not set")
    sys.exit(1)

COMMODITIES_TO_FETCH = [
    "Onion", "Tomato", "Potato", "Garlic",
    "Ginger(Green)", "Green Chilli", "Brinjal", "Coriander"
]

FINAL_ALLOWED = [
    "Onion", "Tomato", "Potato", "Garlic",
    "Ginger(Green)", "Green Chilli", "Brinjal", "Coriander(Leaves)"
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
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429,500,502,503,504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

SESSION = get_session()

def parse_date(s):
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except:
        return None

def fetch_mp(commodity, max_retries_per_offset=3):
    all_recs = []
    offset = 0
    limit = 100
    while True:
        params = {
            "api-key": API_KEY,
            "format": "json",
            "limit": limit,
            "offset": offset,
            "filters[state.keyword]": "Madhya Pradesh",
            "filters[commodity]": commodity
        }
        attempt = 0
        while attempt < max_retries_per_offset:
            try:
                logger.info(f"Fetching {commodity} offset {offset} attempt {attempt+1}")
                r = SESSION.get(BASE_URL, params=params, timeout=60, headers={"User-Agent":"Mozilla/5.0"})
                r.raise_for_status()
                data = r.json()
                if data.get("status") != "ok":
                    raise Exception(f"API status not ok: {data}")
                batch = data.get("records", [])
                if not batch:
                    return all_recs
                all_recs.extend(batch)
                total = int(data.get("total", 0))
                logger.info(f"  -> Got {len(batch)} | Total {total}")
                offset += limit
                if offset >= total:
                    return all_recs
                time.sleep(1)
                break  # success, break retry loop
            except Exception as e:
                attempt += 1
                logger.warning(f"Error {commodity} offset {offset}: {e} (attempt {attempt}/{max_retries_per_offset})")
                if attempt >= max_retries_per_offset:
                    logger.error(f"Failed after {max_retries_per_offset} attempts for {commodity} offset {offset}")
                    raise
                time.sleep(5 * attempt)

def clean_and_filter(records, requested):
    cleaned = []
    for rec in records:
        market = rec.get("market")
        commodity = rec.get("commodity")
        # STRICT EXACT MATCH - kills Green Gram, Green Peas
        if commodity not in FINAL_ALLOWED:
            continue
        if requested == "Coriander" and commodity != "Coriander(Leaves)":
            continue
        if requested != "Coriander" and commodity != requested:
            continue
        if market not in ALLOWED_MARKETS:
            continue
        arrival = parse_date(rec.get("arrival_date") or "")
        if not arrival:
            continue
        def to_num(x):
            try:
                return float(str(x).replace(",","").strip())
            except:
                return None
        modal = to_num(rec.get("modal_price"))
        if modal is None or modal <= 0 or modal > 100000:
            continue
        cleaned.append((
            rec.get("state"), rec.get("district"), market,
            commodity, rec.get("variety") or "Other",
            rec.get("grade") or "FAQ",
            arrival,
            to_num(rec.get("min_price")),
            to_num(rec.get("max_price")),
            modal
        ))
    return cleaned

def upsert(rows):
    if not rows:
        logger.warning("No rows to upsert")
        return 0
    # DEDUP by PK
    deduped = {}
    for r in rows:
        key = (r[2], r[3], r[4], r[5], r[6])  # market, commodity, variety, grade, date
        deduped[key] = r
    rows = list(deduped.values())
    logger.info(f"After dedup: {len(rows)} unique rows")
    try:
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
        logger.info(f"Upserted {len(rows)} rows")
        return len(rows)
    except Exception as e:
        logger.error(f"DB upsert failed: {e}")
        raise

def main():
    logger.info("Starting ingestion")
    all_cleaned = []
    try:
        for comm in COMMODITIES_TO_FETCH:
            recs = fetch_mp(comm)
            cleaned = clean_and_filter(recs, comm)
            logger.info(f"{comm}: {len(recs)} fetched, {len(cleaned)} kept")
            all_cleaned.extend(cleaned)
        
       # Data quality check
commodity_counts = {}

for row in all_cleaned:
    commodity = row[3]
    commodity_counts[commodity] = commodity_counts.get(commodity, 0) + 1

valid_commodities = len(commodity_counts)

logger.info(
    f"Data quality: {valid_commodities} commodities, "
    f"{len(all_cleaned)} valid rows"
)

if valid_commodities < 3 or len(all_cleaned) < 5:
    logger.error(
        f"Data quality check failed: "
        f"{valid_commodities} commodities, {len(all_cleaned)} rows"
    )
    sys.exit(1)
        
        upserted = upsert(all_cleaned)
        logger.info(f"DONE - Upserted {upserted} rows")
        
        # Final check
        if upserted == 0:
            logger.error("Upserted 0 rows, failing")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
