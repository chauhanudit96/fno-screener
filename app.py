"""
NSE F&O Momentum Screener - Single-file version
=================================================
Everything (config, mock data, screening logic, UI) lives in this one file
on purpose, so deployment only ever needs ONE file uploaded to GitHub and
pointed at from Streamlit Cloud.

Run locally with:   streamlit run app.py

CURRENTLY RUNNING ON MOCK DATA. Search for "TODO: LIVE DATA" below to find
the one function you'll replace once your Dhan API is wired up - nothing
else needs to change.
"""

import os
import numpy as np
import pandas as pd
import streamlit as st

# streamlit-autorefresh is optional - app still works without it (auto-refresh
# checkbox just won't do anything until this package is installed)
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False


# =============================================================================
# CONFIG - reads from Streamlit Cloud "Secrets" if deployed, else from a local
# .env file if you're running locally. Either way works with the same code.
# =============================================================================

def get_secret(key: str, default: str = "") -> str:
    """Checks Streamlit secrets first (for cloud deploy), then env vars
    (for local runs with a .env file), else returns the default."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


DHAN_CLIENT_ID = get_secret("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = get_secret("DHAN_ACCESS_TOKEN")

DEFAULT_MIN_PRICE_CHANGE_PCT = 1.5
DEFAULT_MIN_VOLUME_MULTIPLE = 1.5
DEFAULT_MIN_OI_CHANGE_PCT = 3.0
DEFAULT_MIN_DELIVERY_CHANGE_PCT = 2.0
REFRESH_INTERVAL_SECONDS = 15


# =============================================================================
# DATA LAYER - swap generate_mock_snapshot() for a real Dhan fetch when ready
# =============================================================================

SAMPLE_SYMBOLS = [
    "RELIANCE", "TATASTEEL", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "SBIN", "AXISBANK", "ADANIENT", "BAJFINANCE", "MARUTI", "TATAMOTORS",
    "HINDALCO", "JSWSTEEL", "ONGC", "COALINDIA", "NTPC", "POWERGRID",
    "SUNPHARMA", "DRREDDY", "CIPLA", "WIPRO", "HCLTECH", "TECHM",
    "ULTRACEMCO", "GRASIM", "SHREECEM", "BPCL", "IOC", "GAIL",
]


def generate_mock_snapshot(seed: int = None) -> pd.DataFrame:
    """Fake-but-realistic F&O snapshot so the app is fully runnable today."""
    rng = np.random.default_rng(seed)
    n = len(SAMPLE_SYMBOLS)

    prev_close = rng.uniform(200, 4000, n)
    price_move_pct = rng.normal(0, 2.2, n)
    ltp = prev_close * (1 + price_move_pct / 100)

    avg_volume_20d = rng.uniform(5e5, 8e6, n)
    volume = avg_volume_20d * rng.uniform(0.4, 3.5, n)

    prev_oi = rng.uniform(1e6, 5e7, n)
    oi_move_pct = rng.normal(0, 6, n)
    oi = prev_oi * (1 + oi_move_pct / 100)

    avg_delivery_pct_20d = rng.uniform(25, 65, n)
    delivery_pct = np.clip(avg_delivery_pct_20d + rng.normal(0, 6, n), 5, 95)

    return pd.DataFrame({
        "symbol": SAMPLE_SYMBOLS,
        "ltp": ltp.round(2),
        "prev_close": prev_close.round(2),
        "volume": volume.astype(int),
        "avg_volume_20d": avg_volume_20d.astype(int),
        "oi": oi.astype(int),
        "prev_oi": prev_oi.astype(int),
        "delivery_pct": delivery_pct.round(1),
        "avg_delivery_pct_20d": avg_delivery_pct_20d.round(1),
    })


def get_live_snapshot() -> pd.DataFrame:
    """
    TODO: LIVE DATA - replace this function's body with real Dhan API calls.

    Must return a DataFrame with these exact columns:
        symbol, ltp, prev_close, volume, avg_volume_20d,
        oi, prev_oi, delivery_pct, avg_delivery_pct_20d

    Note on delivery_pct: NSE publishes this once daily after market close
    (it's not available intraday from any broker API). So avg_delivery_pct_20d
    and delivery_pct will typically both reflect the most recent confirmed
    trading day, not a live-updating number.
    """
    if not DHAN_CLIENT_ID or not DHAN_ACCESS_TOKEN:
        st.warning("Dhan API credentials not set - showing mock data instead.")
        return generate_mock_snapshot()

    # from dhanhq import dhanhq
    # client = dhanhq(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
    # ... real fetch logic goes here ...
    raise NotImplementedError("Wire up your Dhan API calls here.")


# =============================================================================
# SCREENING LOGIC
# =============================================================================

def classify_quadrant(row: pd.Series) -> str:
    price_up = row["price_change_pct"] > 0
    oi_up = row["oi_change_pct"] > 0
    if price_up and oi_up:
        return "Long Buildup"
    elif price_up and not oi_up:
        return "Short Covering"
    elif not price_up and oi_up:
        return "Short Buildup"
    else:
        return "Long Unwinding"


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["price_change_pct"] = (df["ltp"] - df["prev_close"]) / df["prev_close"] * 100
    df["oi_change_pct"] = (df["oi"] - df["prev_oi"]) / df["prev_oi"].replace(0, np.nan) * 100
    df["volume_multiple"] = df["volume"] / df["avg_volume_20d"].replace(0, np.nan)
    df["delivery_change_pct"] = df["delivery_pct"] - df["avg_delivery_pct_20d"]
    df["quadrant"] = df.apply(classify_quadrant, axis=1)
    return df


def flag_aggressive_fresh_long(df, min_price, min_oi, min_vol, min_deliv):
    mask = (
        (df["quadrant"] == "Long Buildup")
        & (df["price_change_pct"] >= min_price)
        & (df["oi_change_pct"] >= min_oi)
        & (df["volume_multiple"] >= min_vol)
        & (df["delivery_change_pct"] >= min_deliv)
    )
    return df[mask].sort_values("delivery_change_pct", ascending=False)


def flag_short_covering(df, min_price, min_oi, min_vol):
    mask = (
        (df["quadrant"] == "Short Covering")
        & (df["price_change_pct"] >= min_price)
        & (df["oi_change_pct"] <= -min_oi)
        & (df["volume_multiple"] >= min_vol)
    )
    return df[mask].sort_values("oi_change_pct", ascending=True)


# =============================================================================
# UI
# =============================================================================

st.set_page_config(page_title="F&O Momentum Screener", layout="wide")

st.title("F&O Momentum Screener")
st.caption(
    "Long Buildup (fresh aggressive longs) and Short Covering, filtered by "
    "volume and delivery-% confirmation."
)

with st.sidebar:
    st.header("Filters")
    min_price_change = st.slider("Min price change %", 0.0, 10.0, DEFAULT_MIN_PRICE_CHANGE_PCT, 0.5)
    min_oi_change = st.slider("Min OI change %", 0.0, 20.0, DEFAULT_MIN_OI_CHANGE_PCT, 0.5)
    min_volume_multiple = st.slider("Min volume vs 20d avg (x)", 1.0, 5.0, DEFAULT_MIN_VOLUME_MULTIPLE, 0.1)
    min_delivery_change = st.slider("Min delivery % change (long setup only)", 0.0, 15.0, DEFAULT_MIN_DELIVERY_CHANGE_PCT, 0.5)

    st.divider()
    auto_refresh = st.checkbox("Auto-refresh", value=False, disabled=not HAS_AUTOREFRESH)
    if not HAS_AUTOREFRESH:
        st.caption("Add `streamlit-autorefresh` to requirements.txt to enable this.")
    if auto_refresh and HAS_AUTOREFRESH:
        st_autorefresh(interval=REFRESH_INTERVAL_SECONDS * 1000, key="refresh")

    st.divider()
    if st.button("Refresh now"):
        st.rerun()

    st.divider()
    data_source = st.radio("Data source", ["Mock data (demo)", "Live Dhan API"], index=0)

# ---------- Fetch data ----------
raw = generate_mock_snapshot() if data_source == "Mock data (demo)" else get_live_snapshot()
df = add_derived_columns(raw)

fresh_longs = flag_aggressive_fresh_long(df, min_price_change, min_oi_change, min_volume_multiple, min_delivery_change)
short_covers = flag_short_covering(df, min_price_change, min_oi_change, min_volume_multiple)

# ---------- Summary ----------
c1, c2, c3, c4 = st.columns(4)
c1.metric("F&O stocks scanned", len(df))
c2.metric("Aggressive fresh longs", len(fresh_longs))
c3.metric("Short covering", len(short_covers))
c4.metric("Long Buildup (all)", int((df["quadrant"] == "Long Buildup").sum()))

st.divider()

def format_display_table(source_df, cols):
    """
    Builds a plain, pre-formatted string DataFrame for display.
    Deliberately avoids pandas .style.format() -- Styler objects behave
    inconsistently with hide_index across Streamlit versions, so plain
    string formatting is the more robust choice for deployment.
    """
    out = pd.DataFrame()
    for col in cols:
        if col == "symbol":
            out["Symbol"] = source_df[col]
        elif col == "ltp":
            out["LTP"] = source_df[col].map(lambda x: "Rs {:.2f}".format(x))
        elif col == "price_change_pct":
            out["Price Chg %"] = source_df[col].map(lambda x: "{:+.2f}%".format(x))
        elif col == "oi_change_pct":
            out["OI Chg %"] = source_df[col].map(lambda x: "{:+.2f}%".format(x))
        elif col == "volume_multiple":
            out["Volume (x avg)"] = source_df[col].map(lambda x: "{:.2f}x".format(x))
        elif col == "delivery_pct":
            out["Delivery %"] = source_df[col].map(lambda x: "{:.1f}%".format(x))
        elif col == "delivery_change_pct":
            out["Delivery Chg (pp)"] = source_df[col].map(lambda x: "{:+.2f}".format(x))
        elif col == "quadrant":
            out["Quadrant"] = source_df[col]
    return out


# ---------- Fresh Long table ----------
st.subheader("Aggressive Fresh Long")
st.caption("Price up + OI up (Long Buildup) + Volume above average + Delivery % rising")
if fresh_longs.empty:
    st.info("No stocks currently meet the aggressive fresh-long criteria.")
else:
    cols = ["symbol", "ltp", "price_change_pct", "oi_change_pct", "volume_multiple", "delivery_pct", "delivery_change_pct"]
    st.dataframe(format_display_table(fresh_longs, cols), use_container_width=True, hide_index=True)

st.divider()

# ---------- Short Covering table ----------
st.subheader("Short Covering")
st.caption("Price up + OI down (Short Covering) + Volume above average")
if short_covers.empty:
    st.info("No stocks currently meet the short-covering criteria.")
else:
    cols = ["symbol", "ltp", "price_change_pct", "oi_change_pct", "volume_multiple", "delivery_pct"]
    st.dataframe(format_display_table(short_covers, cols), use_container_width=True, hide_index=True)

st.divider()

with st.expander("Full F&O universe - all quadrants"):
    full_cols = ["symbol", "ltp", "price_change_pct", "oi_change_pct", "volume_multiple",
                 "delivery_pct", "delivery_change_pct", "quadrant"]
    full_sorted = df.sort_values("price_change_pct", ascending=False)
    st.dataframe(format_display_table(full_sorted, full_cols), use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "Note: This tool surfaces patterns for your own research - it does not "
    "constitute trading advice, and past OI/volume/delivery patterns don't "
    "guarantee future price moves. Position sizing and risk management are on you."
)
