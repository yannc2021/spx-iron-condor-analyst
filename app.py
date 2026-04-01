import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date, timedelta

st.set_page_config(page_title="SPX Iron Condor Analyst", layout="wide")
st.title("SPX Iron Condor EV Analyst")

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.header("Date Range")

today = date.today()

# Quick-select presets
presets = {
    "Last Week": (today - timedelta(weeks=1), today),
    "Last Month": (today - timedelta(days=30), today),
    "Last 3 Months": (today - timedelta(days=90), today),
    "YTD": (date(today.year, 1, 1), today),
    "1 Year": (today - timedelta(days=365), today),
    "3 Years": (today - timedelta(days=365 * 3), today),
    "5 Years": (today - timedelta(days=365 * 5), today),
    "Custom": None,
}

preset = st.sidebar.radio("Quick select", list(presets.keys()), index=4)

if preset == "Custom":
    start_date = st.sidebar.date_input("Start date", value=today - timedelta(days=365))
    end_date = st.sidebar.date_input("End date", value=today)
else:
    start_date, end_date = presets[preset]
    st.sidebar.caption(f"{start_date.strftime('%b %d, %Y')}  →  {end_date.strftime('%b %d, %Y')}")

st.sidebar.divider()
st.sidebar.header("Strategy Settings")

threshold = st.sidebar.number_input("Range (± points)", min_value=1, value=100, step=5,
                                    help="Iron condor survives if daily move stays within ±this many points")
wing = st.sidebar.number_input("Wing width (points)", min_value=1, value=5, step=1,
                               help="Distance from short strike to long strike")
fill = st.sidebar.number_input("Fill price (credit, points)", min_value=0.01, value=1.20, step=0.05,
                               help="Net credit collected per condor spread")

move_basis = st.sidebar.radio(
    "Measure move as",
    ["Close → Close (overnight)", "Open → Close (intraday)"],
    index=0,
)

# ── Data ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_data(start: date, end: date) -> pd.DataFrame:
    # Fetch in 6-month chunks to avoid Yahoo Finance large-request limits
    chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=180), end)
        raw = yf.download("^GSPC", start=chunk_start, end=chunk_end,
                          interval="1d", auto_adjust=True, progress=False)
        if not raw.empty:
            chunks.append(raw)
        chunk_start = chunk_end + timedelta(days=1)

    if not chunks:
        return pd.DataFrame()

    raw = pd.concat(chunks)
    raw = raw[~raw.index.duplicated()]
    raw.columns = raw.columns.get_level_values(0)
    df = raw[["Open", "High", "Low", "Close"]].copy()
    df["Prev Close"] = df["Close"].shift(1)
    df["C2C Change"] = df["Close"] - df["Prev Close"]
    df["O2C Change"] = df["Close"] - df["Open"]
    df = df.dropna().round(2)
    return df

with st.spinner("Fetching SPX data…"):
    try:
        df = load_data(start_date, end_date)
        if df.empty:
            st.error("yfinance returned empty data. Yahoo Finance may be blocking the request.")
            st.stop()
    except Exception as e:
        st.error(f"Failed to fetch data: {e}")
        st.exception(e)
        st.stop()

# Pick the move column based on user selection
move_col = "C2C Change" if "Close → Close" in move_basis else "O2C Change"
move_label = "Close-to-Close" if "Close → Close" in move_basis else "Open-to-Close"

df["Inside"] = df[move_col].abs() <= threshold

# ── Stats ─────────────────────────────────────────────────────────────────────
total = len(df)
inside = df["Inside"].sum()
outside = total - inside
p_inside = inside / total
p_outside = outside / total

max_loss = wing - fill
ev_pts = (p_inside * fill) - (p_outside * max_loss)
ev_dollar = ev_pts * 100

# ── Metrics row ───────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Trading days", total)
col2.metric(f"Inside ±{threshold} pts", f"{inside}  ({p_inside*100:.1f}%)")
col3.metric(f"Outside ±{threshold} pts", f"{outside}  ({p_outside*100:.1f}%)", delta_color="inverse")
col4.metric("EV per contract", f"${ev_dollar:+.0f}")

st.divider()

# ── EV breakdown ─────────────────────────────────────────────────────────────
with st.expander("EV Breakdown", expanded=True):
    b1, b2, b3 = st.columns(3)
    b1.metric("Credit collected", f"{fill:.2f} pts")
    b2.metric("Max loss", f"{max_loss:.2f} pts")
    b3.metric("Wing width", f"{wing} pts")

    st.latex(
        r"\text{EV} = P_{inside} \times credit - P_{outside} \times max\_loss"
        + rf" = {p_inside:.3f} \times {fill} - {p_outside:.3f} \times {max_loss:.2f}"
        + rf" = {ev_pts:+.4f}"
    )

# ── Candlestick + range chart ─────────────────────────────────────────────────
st.subheader(f"SPX {move_label} Moves vs ±{threshold} pt Range")

recent = df

fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    row_heights=[0.65, 0.35],
    vertical_spacing=0.04,
)

# Candlesticks
fig.add_trace(
    go.Candlestick(
        x=recent.index,
        open=recent["Open"],
        high=recent["High"],
        low=recent["Low"],
        close=recent["Close"],
        name="SPX",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ),
    row=1, col=1,
)

# ── Move bar chart coloured by inside/outside ────────────────────────────────
colors = recent["Inside"].map({True: "#26a69a", False: "#ef5350"})

fig.add_trace(
    go.Bar(
        x=recent.index,
        y=recent[move_col],
        name=f"{move_label} move",
        marker_color=colors,
        showlegend=False,
    ),
    row=2, col=1,
)

# Threshold lines
for sign in [1, -1]:
    fig.add_hline(
        y=sign * threshold,
        line_dash="dash",
        line_color="orange",
        line_width=1.5,
        row=2, col=1,
    )

# Zero line
fig.add_hline(y=0, line_color="gray", line_width=0.5, row=2, col=1)

fig.update_layout(
    xaxis_rangeslider_visible=False,
    height=650,
    template="plotly_dark",
    legend=dict(orientation="h", y=1.02),
    margin=dict(t=30, b=30),
)
fig.update_yaxes(title_text="SPX Price", row=1, col=1)
fig.update_yaxes(title_text=f"{move_label} (pts)", row=2, col=1)

st.plotly_chart(fig, use_container_width=True)

# ── Outlier days table ────────────────────────────────────────────────────────
st.subheader(f"Days Outside ±{threshold} pts")
outliers = df[~df["Inside"]][["Open", "Close", "C2C Change", "O2C Change"]].sort_index(ascending=False)
outliers.columns = ["Open", "Close", "C→C Change", "O→C Change"]
st.dataframe(outliers, use_container_width=True)
