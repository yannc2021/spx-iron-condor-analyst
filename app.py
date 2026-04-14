import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date, timedelta
import math
from math import erfc, sqrt

st.set_page_config(page_title="SPX Iron Condor Analyst", layout="wide")
st.title("SPX Iron Condor EV Analyst")

# ── Sidebar ──────────────────────────────────────────────────────────────────
if st.sidebar.button("Refresh Data", use_container_width=True):
    st.cache_data.clear()

st.sidebar.header("Strategy Settings")

today = date.today()

# ── Date Range (main page) ────────────────────────────────────────────────────
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

dr_cols = st.columns([6, 1])
with dr_cols[0]:
    preset = st.radio("Date range", list(presets.keys()), index=4, horizontal=True, label_visibility="collapsed")
with dr_cols[1]:
    if preset == "Custom":
        start_date = st.date_input("Start date", value=today - timedelta(days=365))
        end_date = st.date_input("End date", value=today)

if preset != "Custom":
    start_date, end_date = presets[preset]

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
@st.cache_data(ttl=900)
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

@st.cache_data(ttl=900)
def load_vix() -> float:
    raw = yf.download("^VIX", period="5d", interval="1d", progress=False)
    raw.columns = raw.columns.get_level_values(0)
    return float(raw["Close"].iloc[-1])

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

with st.spinner("Fetching VIX…"):
    try:
        current_vix = load_vix()
    except Exception:
        current_vix = None

move_col = "C2C %" if "Close → Close" in move_basis else "O2C %"
move_label = "Close-to-Close" if "Close → Close" in move_basis else "Open-to-Close"

current_spx = df["Close"].iloc[-1]
credit_dollars = credit_fill * 100  # per-contract value
credit = credit_fill  # fill price == SPX points (1 SPX pt = $100/contract)
max_loss = wing - credit

total = len(df)
abs_pct = df[move_col].abs()

# ── Historical delta-calibrated thresholds (for EV analysis) ──────────────────
# 0.20 delta → 80th pct of |% moves| (20% of days exceed it)
# 0.15 delta → 85th pct, 0.10 delta → 90th pct
hist_delta_levels = {
    "~0.20 delta": (abs_pct.quantile(0.80), "#ff9800"),
    "~0.15 delta": (abs_pct.quantile(0.85), "#29b6f6"),
    "~0.10 delta": (abs_pct.quantile(0.90), "#ab47bc"),
}

# ── VIX-based strike thresholds ───────────────────────────────────────────────
# Daily 1σ move = SPX × (VIX/100) / √252
# σ multiples from standard normal: 0.20δ → 0.842σ, 0.15δ → 1.036σ, 0.10δ → 1.282σ
VIX_SIGMA = {
    "~0.20 delta": (0.842, "#ff9800"),
    "~0.15 delta": (1.036, "#29b6f6"),
    "~0.10 delta": (1.282, "#ab47bc"),
}

# ── Sidebar: show point equivalents at current SPX ────────────────────────────
st.sidebar.divider()
st.sidebar.caption(f"**At current SPX {current_spx:,.0f}:**")
st.sidebar.caption(f"Credit = ${credit_fill:.2f} fill  (${credit_dollars:.0f}/contract)")
st.sidebar.caption(f"Wing   = {wing} pts  (${wing*100:.0f}/lot)")
st.sidebar.caption(f"Max loss ≈ {max_loss:.1f} pts  (${max_loss*100:.0f}/lot)")

# ── Strike thresholds (VIX-based) ────────────────────────────────────────────
last_date = df.index[-1].strftime("%B %d, %Y")

if current_vix is not None:
    daily_1sigma_pts = current_spx * (current_vix / 100) / math.sqrt(252)
    daily_1sigma_pct = (current_vix / 100) / math.sqrt(252) * 100

    st.subheader(f"Strike Thresholds  —  {last_date}  |  SPX {current_spx:,.2f}  |  VIX {current_vix:.2f}  |  1σ ≈ {daily_1sigma_pts:,.0f} pts ({daily_1sigma_pct:.2f}%)")
    cols = st.columns(3)
    for col, (label, (sigma_mult, color)) in zip(cols, VIX_SIGMA.items()):
        move_pts = daily_1sigma_pts * sigma_mult
        move_pct = daily_1sigma_pct * sigma_mult
        upper = current_spx + move_pts
        lower = current_spx - move_pts
        col.markdown(f"#### {label}  <span style='color:{color}'>●</span>", unsafe_allow_html=True)
        col.metric("Upper strike", f"{upper:,.2f}  (+{move_pct:.2f}%)")
        col.metric("Lower strike", f"{lower:,.2f}  (−{move_pct:.2f}%)")
        col.caption(f"±{move_pts:,.0f} pts from current close")

    with st.expander("How are these strikes calculated?"):
        st.markdown(f"""
**Step 1 — Daily 1σ move from VIX**

VIX is the market's annualized implied volatility for SPX (expressed as a percentage).
To convert it to a single-day 1-standard-deviation move:

$$\\text{{Daily 1}}\\sigma = SPX \\times \\frac{{VIX}}{{100}} \\div \\sqrt{{252}}$$

At current values: **{current_spx:,.0f} × {current_vix/100:.4f} ÷ √252 ≈ ±{daily_1sigma_pts:,.0f} pts ({daily_1sigma_pct:.2f}%)**

**Step 2 — σ multiples for each delta level**

Under a log-normal (Black-Scholes) model, short-strike delta maps to a σ multiple via the
standard-normal inverse CDF:

| Delta | σ multiple | Meaning |
|-------|-----------|---------|
| ~0.20δ | 0.842σ | 20% of days expected to close beyond this level |
| ~0.15δ | 1.036σ | 15% of days expected to close beyond this level |
| ~0.10δ | 1.282σ | 10% of days expected to close beyond this level |

Each strike = SPX ± (σ multiple × daily 1σ pts).
""")
else:
    st.warning("VIX unavailable — strike thresholds cannot be calculated.")

st.divider()

# ── Metrics header ────────────────────────────────────────────────────────────
st.subheader(f"EV by Delta Level  —  {move_label}  |  {total} trading days")

cols = st.columns(3)
for col, (label, (threshold, color)) in zip(cols, hist_delta_levels.items()):
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

    for label, (threshold, _) in hist_delta_levels.items():
        outside = (abs_pct > threshold).sum()
        inside = total - outside
        p_inside = inside / total
        p_outside = outside / total
        ev_pts = (p_inside * credit) - (p_outside * max_loss)
        st.latex(
            rf"\text{{EV ({label})}} = {p_inside:.3f} \times {credit:.2f} - {p_outside:.3f} \times {max_loss:.2f} = {ev_pts:+.4f} \text{{ pts}}"
        )

# ── Optimization Table ───────────────────────────────────────────────────────
st.subheader("Strike Optimization Table")

if current_vix is not None:
    daily_1sigma_pct_ev = (current_vix / 100) / sqrt(252) * 100

    sigma_levels = [0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.25, 2.50]

    rows = []
    for sigma in sigma_levels:
        move_pct = sigma * daily_1sigma_pct_ev
        move_pts = move_pct / 100 * current_spx
        delta = 0.5 * erfc(sigma / sqrt(2))

        win_days = (abs_pct < move_pct).sum()
        lose_days = total - win_days
        win_rate = win_days / total
        lose_rate = lose_days / total

        ev_dollar = (win_rate * credit - lose_rate * max_loss) * 100
        breakeven_credit = (lose_rate * max_loss) / win_rate if win_rate > 0 else float("nan")

        rows.append({
            "Delta": delta,
            "% Move": move_pct,
            "±Pts": move_pts,
            "Upper Strike": current_spx + move_pts,
            "Lower Strike": current_spx - move_pts,
            "Win Rate": win_rate,
            "B/E Credit": breakeven_credit,
            "EV": ev_dollar,
        })

    opt_df = pd.DataFrame(rows)

    best_ev_idx = opt_df["EV"].idxmax()

    ev_numeric = opt_df["EV"]

    def style_opt_table(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        styles.loc[best_ev_idx] = "background-color: rgba(38, 166, 154, 0.25); font-weight: bold"
        for i in df.index:
            color = "color: #26a69a" if ev_numeric[i] >= 0 else "color: #ef5350"
            cur = styles.loc[i, "EV"]
            styles.loc[i, "EV"] = f"{cur}; {color}" if cur else color
        return styles

    display_df = opt_df.copy()
    display_df["Delta"] = display_df["Delta"].map(lambda x: f"{x:.3f}")
    display_df["% Move"] = display_df["% Move"].map(lambda x: f"±{x:.2f}%")
    display_df["±Pts"] = display_df["±Pts"].map(lambda x: f"±{x:,.0f}")
    display_df["Upper Strike"] = display_df["Upper Strike"].map(lambda x: f"{x:,.2f}")
    display_df["Lower Strike"] = display_df["Lower Strike"].map(lambda x: f"{x:,.2f}")
    display_df["Win Rate"] = display_df["Win Rate"].map(lambda x: f"{x*100:.1f}%")
    display_df["B/E Credit"] = display_df["B/E Credit"].map(lambda x: f"${x:.2f}")
    display_df["EV"] = display_df["EV"].map(lambda x: f"${x:+.0f}")

    st.caption(f"Based on VIX {current_vix:.2f}  |  Credit fill ${credit_fill:.2f}  |  Wing {wing} pts  |  {total} historical days  —  highlighted row = best EV")
    st.dataframe(
        display_df.style.apply(style_opt_table, axis=None),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.warning("VIX unavailable — optimization table requires VIX.")

st.divider()

# ── Chart ─────────────────────────────────────────────────────────────────────
st.subheader(f"SPX {move_label} Moves (%) with Delta-Level Thresholds")

# Use the widest threshold (0.20 delta) to colour inside/outside
primary_threshold = list(hist_delta_levels.values())[0][0]
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
for label, (threshold, color) in hist_delta_levels.items():
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
threshold_options = {label: val for label, (val, _) in hist_delta_levels.items()}
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
