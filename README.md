# Bhopal Restaurant Procurement Decision Engine

**Next-morning procurement planning | Latest reported mandi data**

A real-world decision-support system for restaurants, hotels, and cloud kitchens in Bhopal to decide **where to buy tomorrow's vegetables cheapest, including transport cost**.

> Instead of buying daily from nearest Bhopal mandi (which may be expensive), the engine compares all nearby mandis within 250km and tells you the cheapest single-mandi and cheapest 2-mandi combination that fulfills your basket.

Live Demo: `https://bhopal-restaurant-procurement.streamlit.app`  
Data Source: Government of India Open Data — `data.gov.in` / Agmarknet

---

## Problem

Restaurant owners in Bhopal purchase vegetables every morning at 4 AM for that day's guests. If they always buy from the nearest Bhopal mandi, they often overpay. A nearby mandi like Dewas or Sehore may be cheaper, but information comes late from local contacts, leaving no time for transport planning.

Current process is manual, late, and cost-inefficient.

## Vision

Open dashboard at night, enter tomorrow's basket (e.g., Onion 20kg + Tomato 30kg + Potato 25kg), and get ranked procurement options before 4 AM:

*   **Single Partial** — cheapest single mandi even if incomplete
*   **Single Full** — cheapest single mandi that gives full basket in 1 stop
*   **Split Full** — cheapest combination of 2 mandis that gives full basket in 2 stops

With total landed cost = commodity cost + transport cost, and clear missing-item alerts.

## Solution Overview

```
Agmarknet (APMC staff daily entry till 11:30 PM)
        ↓
data.gov.in API (Resource: 9ef84268-d588-465a-a308-a864a43d0070)
        ↓
GitHub Actions (Daily 10:30 PM IST - Asia/Kolkata)
        ↓
Python Ingestion (requests + pagination + strict exact-match + dedup)
        ↓
Supabase Postgres (mandi_prices - history accumulates, PK includes arrival_date)
        ↓
SQL Decision Engine (views - 7-day window, transport model, 3 options)
        ↓
Streamlit / Power BI Dashboard (DirectQuery)
```



## Data Sources & Constants

### Data Source
*   **Dataset:** Current Daily Price of Various Commodities from Various Markets (Mandi)
*   **Portal:** `data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi`
*   **API Base:** `api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070`
*   **Original System:** `agmarknet.gov.in`
*   **Fields:** State, District, Market, Commodity, Variety, Grade, Arrival_Date (dd/mm/yyyy), Min_Price, Max_Price, Modal_Price (Rs/Quintal)
*   **Refresh:** Daily, market staff can update till next day 11:30 PM
*   **API Limit:** Anonymous key caps at 10 records/call, personal free key allows 1000/call, pagination via `offset` and `limit`

### Geographic Constants (Base of Decision)
*   **Base Location:** Bhopal APMC (0 km)
*   **Radius:** 250 km max (3-4 hours one-way by mini-truck)
*   **Mandi Master:** 20 mandis within 250km selected for restaurant relevance and data availability:
    *   0-50km: Bhopal APMC (0), Obedullaganj (35), Sehore (38), Berasia (42), Raisen (45)
    *   50-100km: Vidisha (56), Hoshangabad (77), Ashta (82), Itarsi (92), Ganj Basoda (98)
    *   100-200km: Sarangpur(F&V) (110), Haatpipliya(F&V) (128), Dewas (155), Gadarwada(F&V) (165), Sagar (175), Narsinghpur (185), Ujjain (190), Indore (194), Indore(F&V) (195), Deori(F&V) (200)
*   **Distances:** Road distances from Bhopal via NH46/NH52, verified from official district sites and Maps. Travel time assumed avg 40-50 kmph for loaded mini-truck.

### Transport Constants (Base of Total Cost)
*   **Vehicle:** Tata Ace Gold Petrol - 5 Year Old (common for Bhopal restaurant procurement)
*   **Fuel Price:** Rs 114.54 per litre (Bhopal, Moneycontrol Aug 2026 - dynamic but used as base)
*   **Mileage:** 12 kmpl for 5-year old (new is 13-15 kmpl per TrucksBuses)
*   **Fuel Cost per km:** 114.54 / 12 = Rs 9.55/km
*   **Driver + Maintenance + Depreciation:** Rs 4.45/km (driver 2 + maintenance 1.5 + depreciation 1)
*   **Total Cost per km One-Way:** Rs 14/km
*   **Round Trip Cost:** distance * 2 * 14 = distance * 28
    *   Example: Sehore 38km → Rs 1064 round trip, Indore 194km → Rs 5432
*   **Formula:** `Total Basket Cost = SUM((Modal_Price/100 * Qty_kg)) + Round_Trip_Transport_Cost`
    *   For split: `Total = SUM(cheapest price per commodity across 2 mandis) + Transport1 + Transport2`

### Business Rules (Constants)
*   **Price Used:** Modal Price (most common price), not Min/Max — modal is representative, Min/Max show range and data quality issues
*   **Grade Handling:** Comparisons require Commodity + Variety + Grade + Date, not just Commodity + Market (e.g., FAQ vs Non-FAQ Onion cannot be compared blindly)
*   **Availability Window:** 7 days — if a mandi didn't report Tomato today, we use most recent within last 7 days (more realistic than only today)
*   **No Arrival Quantity:** Source API does not provide arrival quantity/volume, so volume trend analysis excluded — no fake metrics
*   **Basket:** User-defined, 0-10 commodities, quantity in kg. Final scope with good data coverage within 250km: Onion, Tomato, Potato, Garlic, Ginger(Green), Green Chilli, Brinjal, Coriander(Leaves) — Capsicum and Lemon have 0 records in radius and are excluded data-driven

## Database Schema

**mandi_prices**
*   state, district, market, commodity, variety, grade, arrival_date (PK: market, commodity, variety, arrival_date, grade), min_price, max_price, modal_price, fetched_at

**mandi_master**
*   market (PK), district, distance_from_bhopal_km, travel_time_hours, is_fv_market, is_active

**transport_config**
*   vehicle_type, fuel_price_per_litre, mileage_kmpl, fuel_cost_per_km, driver_maintenance_per_km, total_cost_per_km_one_way, round_trip

**basket_input**
*   commodity (PK), quantity_kg — edited daily, drives all views

**Views**
*   `vw_mandi_transport_cost` — distance * 2 * 14
*   `vw_latest_prices_7d` — ROW_NUMBER() partitioned by market, commodity, latest date + cheapest grade within 7 days
*   `vw_procurement_cheapest_first` — single mandi cheapest first (partial allowed)
*   `vw_single_partial` — WHERE available >0 AND available < total
*   `vw_single_full` — WHERE available = total (1 stop)
*   `vw_two_mandi_combinations` — all pairs (20 choose 2 =190), cheapest price per commodity across pair, full basket only, 2 stops
*   `vw_all_three_options` — UNION of above 3, ordered by total_cost ASC

## Phases

**Phase 0 — Business + Data Discovery (Done)**
Validated API: total 13844 records all-India, 263 MP, 53 Onion, 27 Tomato etc. Discovered grade matters, no quantity field, filter is substring (Green Chilli returns Green Gram). Final scope: Bhopal-centered 20 mandis, 7-8 restaurant commodities.

**Phase 1 — Database (Done)**
Supabase project ap-south-1 Mumbai, tables with composite PK to prevent duplicates, upsert logic.

**Phase 2 — Ingestion (Done)**
Python script with Session + Retry(5), limit 100 (not 1000 to avoid timeout), 60s timeout, strict exact-match filter FINAL_ALLOWED, dedup by PK, data-quality check (<5 rows → fail). Tested: Onion 7 within 250km, Ginger(Green) 16 etc.

**Phase 2B — Mandi Master (Done)**
Manual distance table from Google Maps/NH data.

**Phase 3 — Procurement SQL Engine (Done)**
3-option framework: single partial (cheapest), single full (1 trip convenience), split full (2 trips, often cheapest full). Multi-mandi optimization via self-join pairs.

**Phase 4 — Dashboard (85% Done)**
Streamlit app replicates reference Power BI design: Build Basket left, KPIs top, Procurement Options middle ranked by total cost, Cost vs Distance scatter, Basket Coverage donut, Decision Breakdown right with commodity purchase plan and missing alerts. Light theme, compact layout.

**Phase 5 — Automation (Done)**
GitHub Actions `.github/workflows/daily_ingestion.yml` cron `30 22 * * *` timezone Asia/Kolkata = 10:30 PM IST daily + workflow_dispatch for manual test. Secrets: DATA_GOV_API_KEY, SUPABASE_DB_URL (Session Pooler). First run succeeded 19s, Supabase fetched_at fresh.

**Phase 6 — Portfolio (Current)**
README, architecture, Loom video, resume bullet.

## Tech Stack

*   Python (requests, psycopg2-binary, python-dotenv, urllib3 Retry)
*   Supabase Postgres (DirectQuery, Views, CTEs, Window Functions, Self-Join Pairs)
*   Streamlit + Plotly (or Power BI DirectQuery)
*   GitHub Actions (cron + secrets)
*   Data.gov.in OGD API

## How to Run Locally

```bash
git clone https://github.com/your/bhopal-restaurant-procurement
cd bhopal-restaurant-procurement
pip install -r requirements.txt
# create .env with DATA_GOV_API_KEY and SUPABASE_DB_URL
python scripts/ingest.py
streamlit run streamlit_app/app.py
```

## Deployment

*   **Ingestion:** GitHub Actions auto-runs daily 10:30 PM IST, upserts into Supabase. History accumulates because PK includes arrival_date.
*   **Dashboard:** Streamlit Cloud — connect GitHub repo, main file `streamlit_app/app.py`, add secret `SUPABASE_DB_URL` (Session Pooler URL), set Python version 3.11 via `.python-version` file to avoid psycopg2 build error on 3.14.
*   **Power BI Alternative:** Connect via PostgreSQL Session Pooler host `aws-0-xxx.pooler.supabase.com:5432`, DirectQuery mode, load `vw_all_three_options`.

## Future Enhancements

*   Add transport cost per kg (divide round-trip by total qty) for fair comparison
*   Add map with lat/long for mandis
*   Add price trend (30-day line chart) per commodity per mandi
*   Add WhatsApp alert when price spike >10% vs 7-day avg
*   Add user-specific transport rate input in dashboard

## Conclusion

This is not a mandi price dashboard. It is a **procurement decision engine** that answers: "Given what I need to buy tomorrow, where should I buy it, what will it cost including transport, what is missing, and how much do I save by doing 2 stops vs 1?"

It uses official government data, handles real data-quality issues (grade, substring filter, timeouts, dedup), models transport realistically, and provides 3 actionable options ranked by total landed cost — exactly how a restaurant procurement manager thinks before 4 AM.

## Resume Bullet

> Built end-to-end restaurant procurement decision engine for Bhopal using live Govt. of India mandi API (3K+ mandis), Supabase, and GitHub Actions daily automation. Engineered SQL logic for 3 options — single partial, single full (1 stop), split full (2 mandis, 190 pairs brute-force) — calculating total landed cost = commodity cost (modal/100 * qty) + transport (Tata Ace 5yr, Rs 14/km one-way, petrol Rs 114.54, 12 kmpl, 250km radius). Streamlit dashboard shows cheapest full basket (e.g., Bhopal+Haatpipliya split saves 27% vs Ujjain single) with missing-item alerts and 7-day availability window.

## Constants Reference (Do Not Change Daily)

*   Petrol: 114.54 Rs/l
*   Mileage: 12 kmpl (5yr old)
*   Cost per km one-way: 14 Rs
*   Round trip multiplier: 2
*   Radius: 250 km
*   Mandis: 20
*   Time window: 7 days
*   Vehicle: Tata Ace Gold Petrol 5yr
*   Resource ID: 9ef84268-d588-465a-a308-a864a43d0070
