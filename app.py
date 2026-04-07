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

credit_fill = st.sidebar.number_input(
    "Credit (fill price $)",
    min_value=0.05, value=1.60, step=0.05, format="%.2f",
    help="Net credit fill price per share. Multiply by 100 for per-contract value.",
)
wing = st.sidebar.selectbox(
    "Wing width (points)",
    options=[5, 10, 15, 20],
    index=0,
    help="Distance from short strike to long strike in SPX points",
)

move_basis = st.sidebar.radio(
    "Measure move as",
    ["Close → Close (overnight)", "Open → Close (intraday)"],
    index=0,
)

# ── Data ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_data(start: date, end: date) -> pd.DataFrame:
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
    df["C2C Pts"] = df["Close"] - df["Prev Close"]
    df["O2C Pts"] = df["Close"] - df["Open"]
    df["C2C %"] = (df["C2C Pts"] / df["Prev Close"]) * 100
    df["O2C %"] = (df["O2C Pts"] / df["Open"]) * 100
    df = df.dropna().round(4)
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

move_col = "C2C %" if "Close → Close" in move_basis else "O2C %"
move_label = "Close-to-Close" if "Close → Close" in move_basis else "Open-to-Close"

current_spx = df["Close"].iloc[-1]
credit_dollars = credit_fill * 100  # per-contract value
credit = credit_fill  # fill price == SPX points (1 SPX pt = $100/contract)
max_loss = wing - credit

total = len(df)
abs_pct = df[move_col].abs()

# ── Delta-calibrated thresholds ───────────────────────────────────────────────
# 0.20 delta → 80th pct of |% moves| (20% of days exceed it)
# 0.15 delta → 85th pct, 0.10 delta → 90th pct
delta_levels = {
    "~0.20 delta": (abs_pct.quantile(0.80), "#ff9800"),
    "~0.15 delta": (abs_pct.quantile(0.85), "#29b6f6"),
    "~0.10 delta": (abs_pct.quantile(0.90), "#ab47bc"),
}

# ── Sidebar: show point equivalents at current SPX ────────────────────────────
st.sidebar.divider()
st.sidebar.caption(f"**At current SPX {current_spx:,.0f}:**")
st.sidebar.caption(f"Credit = ${credit_fill:.2f} fill  (${credit_dollars:.0f}/contract)")
st.sidebar.caption(f"Wing   = {wing} pts  (${wing*100:.0f}/lot)")
st.sidebar.caption(f"Max loss ≈ {max_loss:.1f} pts  (${max_loss*100:.0f}/lot)")

# ── Strike thresholds ────────────────────────────────────────────────────────
last_date = df.index[-1].strftime("%B %d, %Y")
st.subheader(f"Strike Thresholds  —  SPX closed at {current_spx:,.2f} on {last_date}")
cols = st.columns(3)
for col, (label, (threshold, color)) in zip(cols, delta_levels.items()):
    upper = current_spx * (1 + threshold / 100)
    lower = current_spx * (1 - threshold / 100)
    col.markdown(f"#### {label}  <span style='color:{color}'>●</span>", unsafe_allow_html=True)
    col.metric("Upper", f"{upper:,.2f}  (+{threshold:.2f}%)")
    col.metric("Lower", f"{lower:,.2f}  (−{threshold:.2f}%)")

st.divider()

# ── Metrics header ────────────────────────────────────────────────────────────
st.subheader(f"EV by Delta Level  —  {move_label}  |  {total} trading days")

cols = st.columns(3)
for col, (label, (threshold, color)) in zip(cols, delta_levels.items()):
    outside = (abs_pct > threshold).sum()
    inside = total - outside
    p_inside = inside / total
    p_outside = outside / total
    ev_pts = (p_inside * credit) - (p_outside * max_loss)
    ev_dollar = ev_pts * 100
    pt_equiv = threshold / 100 * current_spx

    col.markdown(f"#### {label}")
    col.metric("Threshold", f"±{threshold:.2f}%  (≈ ±{pt_equiv:,.0f} pts)")
    col.metric("Days inside", f"{inside}  ({p_inside*100:.1f}%)")
    col.metric("EV per contract", f"${ev_dollar:+.0f}")

st.divider()

# ── EV formula breakdown ──────────────────────────────────────────────────────
with st.expander("EV Breakdown", expanded=False):
    b1, b2, b3 = st.columns(3)
    b1.metric("Credit", f"${credit_fill:.2f} fill  (${credit_dollars:.0f}/contract)")
    b2.metric("Wing width", f"{wing} pts  (${wing*100:.0f}/lot)")
    b3.metric("Max loss", f"{max_loss:.2f} pts  ≈ ${max_loss*100:.0f}/lot")

    # Show formula for each level
    for label, (threshold, _) in delta_levels.items():
        outside = (abs_pct > threshold).sum()
        inside = total - outside
        p_inside = inside / total
        p_outside = outside / total
        ev_pts = (p_inside * credit) - (p_outside * max_loss)
        st.latex(
            rf"\text{{EV ({label})}} = {p_inside:.3f} \times {credit:.2f} - {p_outside:.3f} \times {max_loss:.2f} = {ev_pts:+.4f} \text{{ pts}}"
        )

# ── Chart ─────────────────────────────────────────────────────────────────────
st.subheader(f"SPX {move_label} Moves (%) with Delta-Level Thresholds")

# Use the widest threshold (0.20 delta) to colour inside/outside
primary_threshold = list(delta_levels.values())[0][0]
inside_mask = abs_pct <= primary_threshold
colors = inside_mask.map({True: "#26a69a", False: "#ef5350"})

fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    row_heights=[0.65, 0.35],
    vertical_spacing=0.04,
)

fig.add_trace(
    go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name="SPX",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ),
    row=1, col=1,
)

fig.add_trace(
    go.Bar(
        x=df.index,
        y=df[move_col],
        name=f"{move_label} %",
        marker_color=colors,
        showlegend=False,
    ),
    row=2, col=1,
)

# Threshold lines for all three delta levels
for label, (threshold, color) in delta_levels.items():
    for sign in [1, -1]:
        fig.add_hline(
            y=sign * threshold,
            line_dash="dash",
            line_color=color,
            line_width=1.5,
            annotation_text=label if sign == 1 else "",
            annotation_position="right",
            row=2, col=1,
        )

fig.add_hline(y=0, line_color="gray", line_width=0.5, row=2, col=1)

fig.update_layout(
    xaxis_rangeslider_visible=False,
    height=650,
    template="plotly_dark",
    legend=dict(orientation="h", y=1.02),
    margin=dict(t=30, b=30),
)
fig.update_yaxes(title_text="SPX Price", row=1, col=1)
fig.update_yaxes(title_text=f"{move_label} (%)", row=2, col=1)

st.plotly_chart(fig, use_container_width=True)

# ── Daily table ───────────────────────────────────────────────────────────────
st.subheader("Daily Data")
threshold_options = {label: val for label, (val, _) in delta_levels.items()}
selected_threshold_label = st.radio(
    "Highlight outside", list(threshold_options.keys()), horizontal=True
)
highlight_threshold = threshold_options[selected_threshold_label]

daily = df[["Open", "Close", "C2C Pts", "C2C %", "O2C Pts", "O2C %"]].sort_index(ascending=False).copy()
outside_mask = abs_pct[daily.index] > highlight_threshold

def highlight_outside(row):
    color = "background-color: rgba(239, 83, 80, 0.25)"
    return [color] * len(row) if outside_mask[row.name] else [""] * len(row)

daily.columns = ["Open", "Close", "C→C pts", "C→C %", "O→C pts", "O→C %"]
st.dataframe(daily.style.apply(highlight_outside, axis=1), use_container_width=True)
