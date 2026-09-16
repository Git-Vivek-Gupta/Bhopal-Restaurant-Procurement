-- =============================================================================
-- BHOPAL RESTAURANT PROCUREMENT DECISION ENGINE - FINAL COMPLETE SQL
-- Run in Supabase SQL Editor in order
-- =============================================================================

-- =============================================================================
-- SECTION 0: CLEANUP (Run this first if re-running whole file)
-- =============================================================================
DROP VIEW IF EXISTS vw_all_three_options CASCADE;
DROP VIEW IF EXISTS vw_single_partial CASCADE;
DROP VIEW IF EXISTS vw_single_full CASCADE;
DROP VIEW IF EXISTS vw_two_mandi_combinations CASCADE;
DROP VIEW IF EXISTS vw_procurement_cheapest_first CASCADE;
DROP VIEW IF EXISTS vw_cheapest_per_commodity CASCADE;
DROP VIEW IF EXISTS vw_basket_breakdown CASCADE;
DROP VIEW IF EXISTS vw_basket_item_cost CASCADE;
DROP VIEW IF EXISTS vw_latest_prices_7d CASCADE;
DROP VIEW IF EXISTS vw_latest_prices CASCADE;
DROP VIEW IF EXISTS vw_mandi_transport_cost CASCADE;

-- =============================================================================
-- SECTION 1: CORE TABLES
-- =============================================================================

-- 1.1 mandi_prices - Main fact table, stores daily prices from data.gov.in
-- PK includes arrival_date so history accumulates (7 days, 30 days etc)
-- fetched_at tells when we pulled from API
CREATE TABLE IF NOT EXISTS mandi_prices (
    state TEXT NOT NULL,
    district TEXT NOT NULL,
    market TEXT NOT NULL,
    commodity TEXT NOT NULL,
    variety TEXT NOT NULL,
    grade TEXT NOT NULL,
    arrival_date DATE NOT NULL,
    min_price NUMERIC,
    max_price NUMERIC,
    modal_price NUMERIC,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT mandi_prices_pkey PRIMARY KEY (market, commodity, variety, arrival_date, grade)
);
CREATE INDEX IF NOT EXISTS idx_mandi_state_commodity_date ON mandi_prices(state, commodity, arrival_date DESC);
CREATE INDEX IF NOT EXISTS idx_mandi_market_date ON mandi_prices(market, arrival_date DESC);

-- 1.2 mandi_master - 20 mandis within 250km of Bhopal, with road distance
-- Market name must match EXACTLY as API returns (e.g., 'Bhopal APMC', 'Haatpipliya (F&V) APMC')
-- Distance verified via NH46/NH52, district sites
CREATE TABLE IF NOT EXISTS mandi_master (
    market TEXT PRIMARY KEY,
    district TEXT NOT NULL,
    distance_from_bhopal_km NUMERIC NOT NULL,
    travel_time_hours NUMERIC NOT NULL,
    is_fv_market BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE
);

TRUNCATE mandi_master;
INSERT INTO mandi_master (market, district, distance_from_bhopal_km, travel_time_hours, is_fv_market) VALUES
-- 0-50km Core Bhopal ring - daily auto pickup possible
('Bhopal APMC', 'Bhopal', 0, 0, false),
('Berasia APMC', 'Bhopal', 42, 1.0, false),
('Obedullaganj APMC', 'Raisen', 35, 0.8, false),
('Sehore APMC', 'Sehore', 38, 0.9, false),
('Raisen APMC', 'Raisen', 45, 1.1, false),
-- 50-100km 1-2 hour
('Vidisha APMC', 'Vidisha', 56, 1.3, false),
('Hoshangabad APMC', 'Narmadapuram', 77, 1.8, false),
('Ashta APMC', 'Sehore', 82, 1.8, false),
('Itarsi APMC', 'Narmadapuram', 92, 2.1, false),
('Ganj Basoda APMC', 'Vidisha', 98, 2.2, false),
-- 100-200km 2-4 hour - main arbitrage zone
('Sarangpur(F&V) APMC', 'Rajgarh', 110, 2.4, true),
('Haatpipliya (F&V) APMC', 'Dewas', 128, 2.8, true),
('Dewas APMC', 'Dewas', 155, 3.2, false),
('Gadarwada (F&V) APMC', 'Narsinghpur', 165, 3.3, true),
('Sagar APMC', 'Sagar', 175, 3.5, false),
('Narsinghpur APMC', 'Narsinghpur', 185, 3.7, false),
('Ujjain APMC', 'Ujjain', 190, 3.8, false),
('Indore APMC', 'Indore', 194, 4.0, false),
('Indore(F&V) APMC', 'Indore', 195, 4.0, true),
('Deori (F&V) APMC', 'Sagar', 200, 4.1, true);

-- 1.3 transport_config - Constants for total cost calculation
-- Vehicle: Tata Ace Gold Petrol 5yr old, common for Bhopal restaurant procurement
-- Fuel: Rs 114.54/litre Bhopal (Moneycontrol Aug 2026)
-- Mileage: 12 kmpl for 5yr old (new 13-15 kmpl per TrucksBuses)
-- Fuel cost per km: 114.54/12 = 9.55, + driver/maintenance/depreciation 4.45 = 14 Rs/km one-way
-- Round trip: distance * 2 * 14
CREATE TABLE IF NOT EXISTS transport_config (
    id INT PRIMARY KEY,
    vehicle_type TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    fuel_price_per_litre NUMERIC NOT NULL,
    mileage_kmpl NUMERIC NOT NULL,
    fuel_cost_per_km NUMERIC NOT NULL,
    driver_maintenance_per_km NUMERIC NOT NULL,
    total_cost_per_km_one_way NUMERIC NOT NULL,
    round_trip BOOLEAN DEFAULT TRUE,
    notes TEXT
);

TRUNCATE transport_config;
INSERT INTO transport_config VALUES (
    1, 'Tata Ace Gold Petrol - 5 Year Old', 'Petrol',
    114.54, 12.0, 9.55, 4.45, 14.0, TRUE,
    'Round trip = distance * 2 * 14. Sehore 38km = 1064 Rs, Indore 194km = 5432 Rs'
);

-- 1.4 basket_input - Restaurant owner's basket for tomorrow, editable daily
-- Example: Onion 20kg, Tomato 30kg, Potato 25kg
CREATE TABLE IF NOT EXISTS basket_input (
    commodity TEXT PRIMARY KEY,
    quantity_kg NUMERIC NOT NULL
);

-- Initial test basket - change anytime with DELETE + INSERT
DELETE FROM basket_input;
INSERT INTO basket_input (commodity, quantity_kg) VALUES
('Onion', 20),
('Tomato', 30),
('Potato', 25);

-- =============================================================================
-- SECTION 2: TRANSPORT COST VIEW
-- =============================================================================

-- vw_mandi_transport_cost - Calculates round-trip transport cost per mandi
-- Uses mandi_master distance * 2 * 14 Rs/km
CREATE OR REPLACE VIEW vw_mandi_transport_cost AS
SELECT 
    m.market, m.district, m.distance_from_bhopal_km, m.travel_time_hours, m.is_fv_market,
    c.total_cost_per_km_one_way,
    (m.distance_from_bhopal_km * 2 * c.total_cost_per_km_one_way) as round_trip_transport_cost
FROM mandi_master m
CROSS JOIN transport_config c
WHERE c.id = 1
ORDER BY m.distance_from_bhopal_km ASC;

-- =============================================================================
-- SECTION 3: LATEST PRICES VIEWS
-- =============================================================================

-- 3.1 vw_latest_prices - Latest price per market per commodity (only latest date)
-- Uses ROW_NUMBER() to pick latest arrival_date + cheapest grade if multiple grades same day
-- Important: Preserves grade/variety distinction, doesn't treat FAQ vs Non-FAQ as same
CREATE OR REPLACE VIEW vw_latest_prices AS
WITH ranked AS (
    SELECT mp.*,
           ROW_NUMBER() OVER (PARTITION BY mp.market, mp.commodity ORDER BY mp.arrival_date DESC, mp.modal_price ASC) as rn
    FROM mandi_prices mp
    JOIN mandi_master mm ON mm.market = mp.market
    WHERE mp.modal_price > 0 AND mp.modal_price < 50000
)
SELECT * FROM ranked WHERE rn = 1;

-- 3.2 vw_latest_prices_7d - Most recent price within last 7 days (more realistic)
-- If Bhopal didn't report Tomato today, use yesterday's price within 7-day window
-- This increases FULL BASKET availability vs only today
CREATE OR REPLACE VIEW vw_latest_prices_7d AS
WITH ranked AS (
    SELECT mp.*,
           ROW_NUMBER() OVER (PARTITION BY mp.market, mp.commodity ORDER BY mp.arrival_date DESC, mp.modal_price ASC) as rn
    FROM mandi_prices mp
    JOIN mandi_master mm ON mm.market = mp.market
    WHERE mp.modal_price > 0 
      AND mp.modal_price < 50000
      AND mp.arrival_date >= CURRENT_DATE - INTERVAL '7 days'
)
SELECT * FROM ranked WHERE rn = 1;

-- =============================================================================
-- SECTION 4: PROCUREMENT ENGINE - CHEAPEST FIRST (BASE VIEW)
-- =============================================================================

-- vw_procurement_cheapest_first - Base engine: For each mandi, calculate total cost for basket
-- Logic: For each mandi in mandi_master CROSS JOIN basket_input
--        LEFT JOIN latest prices to check availability
--        Commodity Cost = (modal_price / 100 * quantity_kg) because price is per quintal (100kg)
--        Transport = round_trip cost from vw_mandi_transport_cost
--        Total = commodity + transport
-- Ordered by total_cost ASC (cheapest first) - what restaurant owner wants
CREATE OR REPLACE VIEW vw_procurement_cheapest_first AS
WITH agg AS (
    SELECT 
        mm.market, mm.district, mm.distance_from_bhopal_km,
        MAX(mt.round_trip_transport_cost) as t_cost,
        COUNT(*) as total_req,
        SUM(CASE WHEN lp.modal_price IS NOT NULL THEN 1 ELSE 0 END) as avail,
        SUM(CASE WHEN lp.modal_price IS NOT NULL THEN (lp.modal_price/100.0 * b.quantity_kg) ELSE 0 END) as comm_cost,
        STRING_AGG(CASE WHEN lp.modal_price IS NULL THEN b.commodity END, ', ') as missing
    FROM mandi_master mm
    CROSS JOIN basket_input b
    LEFT JOIN vw_latest_prices_7d lp ON lp.market = mm.market AND lp.commodity = b.commodity
    LEFT JOIN vw_mandi_transport_cost mt ON mt.market = mm.market
    GROUP BY mm.market, mm.district, mm.distance_from_bhopal_km
)
SELECT 
    market, district, distance_from_bhopal_km,
    avail as available_items,
    total_req as total_requested,
    CASE WHEN avail = total_req THEN 'FULL BASKET' ELSE 'PARTIAL - Missing: ' || COALESCE(missing,'') END as basket_status,
    ROUND(comm_cost::numeric,2) as commodity_cost,
    ROUND(t_cost::numeric,2) as transport_cost,
    ROUND((comm_cost + t_cost)::numeric,2) as total_basket_cost,
    missing as missing_items
FROM agg
WHERE avail > 0  -- Exclude mandis with 0 items
ORDER BY (comm_cost + t_cost) ASC, avail DESC;

-- =============================================================================
-- SECTION 5: THREE DECISION OPTIONS (FINAL ENGINE)
-- =============================================================================

-- 5.1 SINGLE PARTIAL - One mandi, incomplete basket (e.g., Bhopal 1/3 items Rs 600)
-- Use case: Cheapest, but need second trip for missing items
-- Condition: available >0 AND available < total_requested
CREATE OR REPLACE VIEW vw_single_partial AS
SELECT 
    market as combination,
    'SINGLE PARTIAL' as option_type,
    available_items, total_requested,
    basket_status as status,
    commodity_cost, transport_cost, total_basket_cost as total_cost,
    missing_items
FROM vw_procurement_cheapest_first
WHERE available_items > 0 
  AND available_items < total_requested
ORDER BY total_basket_cost ASC;

-- 5.2 SINGLE FULL - One mandi gives full basket in 1 stop (e.g., Ujjain 2/2 Rs 6089)
-- Use case: Convenience, single trip, even if costly
-- Condition: available = total_requested
CREATE OR REPLACE VIEW vw_single_full AS
SELECT 
    market as combination,
    'SINGLE FULL - 1 STOP' as option_type,
    available_items, total_requested,
    basket_status as status,
    commodity_cost, transport_cost, total_basket_cost as total_cost,
    missing_items
FROM vw_procurement_cheapest_first
WHERE available_items = total_requested
ORDER BY total_basket_cost ASC;

-- 5.3 SPLIT FULL - Cheapest 2-mandi combo that gives full basket (e.g., Bhopal+Haatpipliya Rs 4434)
-- Logic: Generate all pairs (20 choose 2 =190 combos), for each commodity pick cheapest price among the two
--        Total commodity = sum(cheapest price per commodity * qty)
--        Total transport = transport1 + transport2 (2 round trips)
--        Keep only pairs where combined availability = total_requested (full basket)
-- Use case: Often cheapest way to get full basket with 2 stops, saves vs single full
CREATE OR REPLACE VIEW vw_two_mandi_combinations AS
WITH pairs AS (
    SELECT m1.market as market1, m2.market as market2,
           t1.round_trip_transport_cost as transport1,
           t2.round_trip_transport_cost as transport2
    FROM mandi_master m1
    JOIN mandi_master m2 ON m1.market < m2.market  -- Avoid duplicates and self-pairs
    JOIN vw_mandi_transport_cost t1 ON t1.market = m1.market
    JOIN vw_mandi_transport_cost t2 ON t2.market = m2.market
),
pair_commodity AS (
    SELECT p.market1, p.market2, p.transport1, p.transport2,
           b.commodity, b.quantity_kg,
           lp1.modal_price as price1, lp2.modal_price as price2,
           CASE WHEN lp1.modal_price IS NOT NULL AND lp2.modal_price IS NOT NULL THEN LEAST(lp1.modal_price, lp2.modal_price)
                WHEN lp1.modal_price IS NOT NULL THEN lp1.modal_price ELSE lp2.modal_price END as best_price,
           CASE WHEN lp1.modal_price IS NOT NULL OR lp2.modal_price IS NOT NULL THEN 1 ELSE 0 END as is_available
    FROM pairs p
    CROSS JOIN basket_input b
    LEFT JOIN vw_latest_prices_7d lp1 ON lp1.market = p.market1 AND lp1.commodity = b.commodity
    LEFT JOIN vw_latest_prices_7d lp2 ON lp2.market = p.market2 AND lp2.commodity = b.commodity
),
agg AS (
    SELECT market1, market2, transport1+transport2 as total_transport,
           COUNT(*) as total_req, SUM(is_available) as avail,
           SUM(CASE WHEN best_price IS NOT NULL THEN (best_price/100.0 * quantity_kg) ELSE 0 END) as comm_cost
    FROM pair_commodity GROUP BY market1, market2, transport1, transport2
)
SELECT 
    market1 || ' + ' || market2 as combination,
    'SPLIT FULL - 2 STOPS' as option_type,
    avail as available_items, total_req as total_requested,
    'FULL BASKET - 2 STOPS' as status,
    ROUND(comm_cost::numeric,2) as commodity_cost,
    ROUND(total_transport::numeric,2) as transport_cost,
    ROUND((comm_cost + total_transport)::numeric,2) as total_cost,
    '' as missing_items
FROM agg WHERE avail = total_req
ORDER BY total_cost ASC
LIMIT 20; -- Top 20 cheapest 2-mandi combos

-- 5.4 ALL THREE TOGETHER - Unified ranking for Power BI / Streamlit
-- Shows: SINGLE PARTIAL (cheapest) -> SPLIT FULL -> SINGLE FULL, all ordered by total_cost ASC
-- This is the main view for dashboard
CREATE OR REPLACE VIEW vw_all_three_options AS
SELECT combination, option_type, available_items, total_requested, status, commodity_cost, transport_cost, total_cost, missing_items FROM vw_single_partial
UNION ALL
SELECT combination, option_type, available_items, total_requested, status, commodity_cost, transport_cost, total_cost, missing_items FROM vw_single_full
UNION ALL
SELECT combination, option_type, available_items, total_requested, status, commodity_cost, transport_cost, total_cost, missing_items FROM vw_two_mandi_combinations
ORDER BY total_cost ASC;

-- =============================================================================
-- SECTION 6: HELPER VIEWS FOR DASHBOARD
-- =============================================================================

-- Cheapest mandi per commodity (for per-item planning)
CREATE OR REPLACE VIEW vw_cheapest_per_commodity AS
SELECT DISTINCT ON (b.commodity)
    b.commodity, b.quantity_kg,
    lp.market, lp.district, mm.distance_from_bhopal_km,
    lp.modal_price,
    ROUND((lp.modal_price/100.0 * b.quantity_kg)::numeric,2) as commodity_cost
FROM basket_input b
JOIN vw_latest_prices_7d lp ON lp.commodity = b.commodity
JOIN mandi_master mm ON mm.market = lp.market
ORDER BY b.commodity, (lp.modal_price/100.0 * b.quantity_kg) ASC;

-- Detailed breakdown for selected mandi (for decision breakdown panel)
CREATE OR REPLACE VIEW vw_basket_breakdown AS
SELECT 
    mm.market, mm.distance_from_bhopal_km,
    b.commodity as requested_commodity, b.quantity_kg,
    lp.modal_price, lp.grade,
    ROUND((lp.modal_price/100.0 * b.quantity_kg)::numeric,2) as commodity_cost,
    CASE WHEN lp.modal_price IS NOT NULL THEN 1 ELSE 0 END as is_available
FROM mandi_master mm
CROSS JOIN basket_input b
LEFT JOIN vw_latest_prices_7d lp ON lp.market = mm.market AND lp.commodity = b.commodity
ORDER BY mm.distance_from_bhopal_km, b.commodity;

-- =============================================================================
-- SECTION 7: TEST QUERIES
-- =============================================================================

Test basket -- change anytime
DELETE FROM basket_input;
INSERT INTO basket_input VALUES ('Onion', 20), ('Tomato', 30), ('Potato', 25);

Main decision
SELECT * FROM vw_all_three_options LIMIT 10;

Coverage check
SELECT commodity, COUNT(*) as records, COUNT(DISTINCT market) as mandis FROM mandi_prices GROUP BY commodity ORDER BY records DESC;

Transport cost per mandi
SELECT * FROM vw_mandi_transport_cost;

Latest prices
SELECT * FROM vw_latest_prices_7d ORDER BY market, commodity;
