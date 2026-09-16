import os
import psycopg2
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="Bhopal Restaurant Procurement Decision Engine", layout="wide", page_icon="🥬", initial_sidebar_state="collapsed")

# --- Compact layout + light theme to match reference ---
st.markdown("""
<style>
.block-container { padding-top: 0.8rem; padding-bottom: 0.5rem; }
[data-testid="stMetric"] { background: white; border: 1px solid #e6e6e6; padding: 10px; border-radius: 10px; }
.main-header { background: #eef6ff; padding: 12px 18px; border-radius: 10px; border-left: 6px solid #1a4d8f; margin-bottom: 12px; }
.kpi-label { font-size: 11px; color: #666; }
h3 { margin-top: 0.5rem; }
</style>
""", unsafe_allow_html=True)

DB_URL = os.getenv("SUPABASE_DB_URL") or st.secrets.get("SUPABASE_DB_URL", "")

@st.cache_resource
def get_conn():
    if not DB_URL:
        st.error("SUPABASE_DB_URL not set in .env or Streamlit secrets")
        st.stop()
    return psycopg2.connect(DB_URL)

@st.cache_data(ttl=600)
def fetch_data():
    conn = get_conn()
    df_master = pd.read_sql("SELECT * FROM vw_mandi_transport_cost ORDER BY distance_from_bhopal_km", conn)
    try:
        df_latest = pd.read_sql("SELECT * FROM vw_latest_prices_7d", conn)
    except:
        df_latest = pd.read_sql("SELECT * FROM mandi_prices WHERE arrival_date >= CURRENT_DATE - INTERVAL '7 days'", conn)
    df_prices = pd.read_sql("SELECT * FROM mandi_prices WHERE arrival_date >= CURRENT_DATE - INTERVAL '7 days'", conn)
    return df_master, df_latest, df_prices

def compute_options(basket_dict, df_master, df_latest):
    if not basket_dict:
        return pd.DataFrame()
    latest_lookup = {}
    for _, row in df_latest.iterrows():
        key = (row['market'], row['commodity'])
        if key not in latest_lookup or float(row['modal_price']) < float(latest_lookup[key]['modal_price']):
            latest_lookup[key] = row
    single_rows = []
    for _, m in df_master.iterrows():
        market = m['market']
        transport = float(m['round_trip_transport_cost'])
        dist = float(m['distance_from_bhopal_km'])
        available = 0
        comm_cost = 0
        missing = []
        for comm, qty in basket_dict.items():
            key = (market, comm)
            if key in latest_lookup:
                price = float(latest_lookup[key]['modal_price'])
                comm_cost += (price/100.0 * qty)
                available += 1
            else:
                missing.append(comm)
        total = comm_cost + transport
        status = "FULL BASKET" if available == len(basket_dict) else f"PARTIAL - Missing: {', '.join(missing)}"
        option_type = "SINGLE FULL - 1 STOP" if available == len(basket_dict) else "SINGLE PARTIAL"
        if available > 0:
            single_rows.append({
                "combination": market, "market1": market, "market2": None,
                "option_type": option_type, "available_items": available,
                "total_requested": len(basket_dict), "status": status,
                "commodity_cost": round(comm_cost,2), "transport_cost": round(transport,2),
                "total_cost": round(total,2), "distance": dist, "missing_items": ", ".join(missing)
            })
    split_rows = []
    markets = df_master['market'].tolist()
    for i in range(len(markets)):
        for j in range(i+1, len(markets)):
            m1, m2 = markets[i], markets[j]
            t1 = float(df_master[df_master['market']==m1]['round_trip_transport_cost'].iloc[0])
            t2 = float(df_master[df_master['market']==m2]['round_trip_transport_cost'].iloc[0])
            dist_avg = (float(df_master[df_master['market']==m1]['distance_from_bhopal_km'].iloc[0]) + float(df_master[df_master['market']==m2]['distance_from_bhopal_km'].iloc[0]))/2
            comm_cost = 0
            available = 0
            for comm, qty in basket_dict.items():
                p1 = latest_lookup.get((m1, comm))
                p2 = latest_lookup.get((m2, comm))
                best = None
                if p1 is not None and p2 is not None:
                    best = min(float(p1['modal_price']), float(p2['modal_price']))
                elif p1 is not None:
                    best = float(p1['modal_price'])
                elif p2 is not None:
                    best = float(p2['modal_price'])
                if best is not None:
                    comm_cost += (best/100.0 * qty)
                    available += 1
            if available == len(basket_dict):
                total = comm_cost + t1 + t2
                split_rows.append({
                    "combination": f"{m1} + {m2}", "market1": m1, "market2": m2,
                    "option_type": "SPLIT FULL - 2 STOPS", "available_items": available,
                    "total_requested": len(basket_dict), "status": "FULL BASKET - 2 STOPS",
                    "commodity_cost": round(comm_cost,2), "transport_cost": round(t1+t2,2),
                    "total_cost": round(total,2), "distance": dist_avg, "missing_items": ""
                })
    df_all = pd.DataFrame(single_rows + split_rows)
    if not df_all.empty:
        df_all = df_all.sort_values(by=["total_cost", "available_items"], ascending=[True, False])
    return df_all

# --- Header ---
st.markdown("""
<div class="main-header">
<h2 style="margin:0; color:#1a4d8f;">Bhopal Restaurant Procurement Decision Engine</h2>
<p style="margin:0; color:#444;">Next-morning procurement planning | Latest reported mandi data</p>
</div>
""", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Local Mandis", "20 within 250 km", "Tata Ace")
c2.metric("Transport Model", "Tata Ace (Round Trip)", "Rs 14/km")
c3.metric("Live Mandi Prices", "Govt. of India (data.gov.in)", "7-day window")
c4.metric("Data", datetime.now().strftime("%d Sep %Y"), "Latest observations")

try:
    df_master, df_latest, df_prices = fetch_data()
except Exception as e:
    st.error(f"DB connection failed: {e}")
    st.stop()

left, middle, right = st.columns([1, 2.1, 1.1])

with left:
    st.subheader("1. Build Basket")
    st.caption("Select commodities and enter quantity (kg)")
    all_comms = ["Onion","Tomato","Potato","Garlic","Ginger(Green)","Green Chilli","Brinjal","Coriander(Leaves)","Capsicum","Lemon"]
    basket = {}
    for comm in all_comms:
        col_a, col_b = st.columns([2.2,1])
        with col_a:
            chk = st.checkbox(comm, value=(comm in ["Onion","Tomato","Potato"]), key=f"chk_{comm}")
        with col_b:
            qty = st.number_input("", min_value=0, max_value=500, value=20 if comm=="Onion" else (30 if comm=="Tomato" else (25 if comm=="Potato" else 0)), label_visibility="collapsed", key=f"qty_{comm}")
        if chk and qty>0:
            basket[comm]=qty
    if st.button("Clear basket"):
        st.rerun()
    st.divider()
    st.markdown("**Selected Basket**")
    for comm,qty in basket.items():
        st.write(f"• {comm} — {qty} kg")
    st.write(f"**Total Items:** {len(basket)}")
    st.write(f"**Total Quantity:** {sum(basket.values())} kg")
    st.info("Only commodities with recent mandi observations are shown in decision results.")

with middle:
    if not basket:
        st.warning("Select at least 1 commodity")
        st.stop()
    df_all = compute_options(basket, df_master, df_latest)
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Basket Items", f"{len(basket)}", f"of {len(all_comms)} commodities")
    k2.metric("Requested Quantity", f"{sum(basket.values())} kg")
    full_cnt = len(df_all[df_all['available_items']==len(basket)]) if not df_all.empty else 0
    k3.metric("Full-Basket Options", f"{full_cnt}", "1 or 2 mandis")
    cheapest_full = df_all[df_all['available_items']==len(basket)]['total_cost'].min() if full_cnt>0 else 0
    k4.metric("Lowest Full-Basket Cost", f"₹ {cheapest_full:,.0f}" if cheapest_full else "N/A")

    st.subheader("2. Procurement Options")
    st.caption("Ranked by total cost (single or two-mandi options)")
    if df_all.empty:
        st.error("No mandi has any of your basket items in last 7 days")
    else:
        # Format numbers to 2 decimals for display
        display_df = df_all[["combination","option_type","available_items","commodity_cost","transport_cost","total_cost"]].copy()
        display_df["Coverage"] = df_all.apply(lambda r: f"{int(r['available_items'])}/{int(r['total_requested'])}", axis=1)
        # Format for display
        for col in ["commodity_cost","transport_cost","total_cost"]:
            display_df[col] = display_df[col].map(lambda x: f"₹ {x:,.2f}")
        
        st.dataframe(display_df, use_container_width=True, height=320)
        
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("**Cost vs Distance from Bhopal**")
            fig = px.scatter(df_all, x="distance", y="total_cost", color="option_type", size="available_items",
                             hover_data=["combination"], labels={"distance":"Distance (km)", "total_cost":"Total Cost (₹)"}, height=300)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.markdown("**Basket Coverage**")
            with_obs = len([c for c in basket.keys() if any((df_latest['commodity']==c).tolist())])
            fig2 = go.Figure(data=[go.Pie(labels=["With observations","No observation","Not selected"], values=[with_obs, len(basket)-with_obs, len(all_comms)-len(basket)], hole=.55, marker_colors=["#28a745","#dc3545","#6c757d"])])
            fig2.update_layout(showlegend=True, height=300, margin=dict(t=10,b=10,l=10,r=10))
            st.plotly_chart(fig2, use_container_width=True)

with right:
    st.subheader("3. Decision Breakdown")
    if df_all.empty:
        st.stop()
    selected = st.selectbox("Select combination", df_all["combination"].tolist(), index=0)
    row = df_all[df_all["combination"]==selected].iloc[0]
    st.markdown("**Commodity Purchase Plan**")
    st.write(f"**{selected}**")
    markets_in_combo = [row["market1"], row["market2"]] if pd.notna(row["market2"]) and row["market2"] else [row["market1"]]
    markets_in_combo = [m for m in markets_in_combo if m]
    for m in markets_in_combo:
        m_info = df_master[df_master['market']==m]
        if m_info.empty: continue
        dist = float(m_info['distance_from_bhopal_km'].iloc[0])
        trans = float(m_info['round_trip_transport_cost'].iloc[0])
        st.markdown(f"**{m}**  \nDistance: {dist:.1f} km | Transport: ₹ {trans:,.0f}")
        for comm,qty in basket.items():
            matching = df_latest[(df_latest['market']==m) & (df_latest['commodity']==comm)]
            if not matching.empty:
                price = float(matching.iloc[0]['modal_price'])
                cost = price/100.0 * qty
                st.write(f"{comm} — {qty}kg @ ₹{price/100:.2f}/kg = ₹{cost:.0f}")
    st.divider()
    st.write(f"**Commodity Cost:** ₹ {row['commodity_cost']:,.2f}")
    st.write(f"**Transport Cost:** ₹ {row['transport_cost']:,.2f}")
    st.markdown(f"### Total Landed Cost: ₹ {row['total_cost']:,.2f}")
    st.divider()
    st.markdown("**Missing / No Current Observation**")
    all_available = set(df_latest['commodity'].unique())
    missing = [c for c in basket.keys() if c not in all_available]
    if missing:
        for m in missing:
            st.write(f"{m} — No current mandi observation reported")
    else:
        st.write("All selected items have recent observations")

st.caption("Transport estimated @ Rs 14/km one-way (Tata Ace 5yr, petrol Rs 114.54, 12 kmpl, round-trip) | Prices are modal prices from data.gov.in last 7 days | 20 mandis within 250km of Bhopal")
