%%writefile app.py
import streamlit as st
import ccxt
import ccxt.async_support as ccxta
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import asyncio

# --- CONFIGURATION ---
st.set_page_config(
    page_title="SMC/ICT Trading Terminal", 
    page_icon="📈", 
    layout="wide"
)

# Inisialisasi Session State agar hasil scan tersimpan & tidak reset saat pindah tab
if "scan_results" not in st.session_state:
    st.session_state.scan_results = None
if "scanned_symbols_count" not in st.session_state:
    st.session_state.scanned_symbols_count = 0

# --- HEADER ---
st.title("⚡ SMC / ICT Institutional Trading Terminal")
st.caption("Order Flow Engine & Multi-Timeframe Market Narrative Inspector")
st.divider()

# --- SIDEBAR CONTROLS ---
st.sidebar.header("🎛️ Terminal Settings")

exchange_name = st.sidebar.selectbox("Exchange", ["binance", "bybit", "bitget", "bingx"])

custom_symbols_input = st.sidebar.text_input(
    "Scan Specific Pairs (Optional)", 
    placeholder="e.g. APTUSDT, PEPEUSDT, FETUSDT",
    help="Separate pairs with commas"
)

scan_limit = st.sidebar.slider("Top Volume Pairs to Scan", min_value=50, max_value=500, value=200, step=50)

strategy_tf_map = {
    "⚡ Scalping (1m - 3m - 15m)": {"HTF": "15m", "LTF": "3m", "Exec": "1m"},
    "🌊 Swing Trading (15m - 1h - 4h)": {"HTF": "4h", "LTF": "1h", "Exec": "15m"},
    "🚀 Investment / Position (1h - 1d - 1w)": {"HTF": "1w", "LTF": "1d", "Exec": "4h"}
}

selected_mode = st.sidebar.selectbox("Strategy Profile", list(strategy_tf_map.keys()))
min_conviction = st.sidebar.slider("Min. Conviction Score (%)", min_value=50, max_value=90, value=70, step=5)

tfs = strategy_tf_map[selected_mode]

# --- ASYNC ENGINE ---
async def fetch_ohlcv_async(exchange, symbol, timeframe, limit=100):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return symbol, timeframe, df
    except Exception:
        return symbol, timeframe, None

def analyze_ict_narrative(df):
    if df is None or len(df) < 20:
        return None

    latest = df.iloc[-1]
    prev_2 = df.iloc[-3]
    recent_20 = df.iloc[-20:-1]

    bsl_high = recent_20['high'].max()
    ssl_low = recent_20['low'].min()
    first_candle = df.iloc[-15]
    fcr_high, fcr_low = first_candle['high'], first_candle['low']

    sweep_bsl = (latest['high'] > bsl_high) and (latest['close'] < bsl_high)
    sweep_ssl = (latest['low'] < ssl_low) and (latest['close'] > ssl_low)

    candle_range = latest['high'] - latest['low']
    body_size = abs(latest['close'] - latest['open'])
    is_displacement = (body_size / candle_range > 0.70) if candle_range > 0 else False
    disp_direction = "BULLISH" if latest['close'] > latest['open'] else "BEARISH"

    fvg_type = None
    fvg_top, fvg_bottom = None, None
    if latest['low'] > prev_2['high']:
        fvg_type = "BULLISH FVG"
        fvg_bottom = prev_2['high']
        fvg_top = latest['low']
    elif latest['high'] < prev_2['low']:
        fvg_type = "BEARISH FVG"
        fvg_top = prev_2['low']
        fvg_bottom = latest['high']

    score = 0
    control = "NEUTRAL"
    bias = "WAIT"

    if sweep_ssl or sweep_bsl:
        score += 25

    if sweep_ssl or (disp_direction == "BULLISH" and is_displacement):
        control = "BUYER (WHALE)"
        bias = "LONG"
        score += 30
        if is_displacement: score += 20
    elif sweep_bsl or (disp_direction == "BEARISH" and is_displacement):
        control = "SELLER (WHALE)"
        bias = "SHORT"
        score += 30
        if is_displacement: score += 20

    if fvg_type is not None:
        score += 15

    if latest['close'] > fcr_high or latest['close'] < fcr_low:
        score += 10

    return {
        "df": df,
        "price": latest['close'],
        "bsl": bsl_high,
        "ssl": ssl_low,
        "fcr_high": fcr_high,
        "fcr_low": fcr_low,
        "sweep_bsl": sweep_bsl,
        "sweep_ssl": sweep_ssl,
        "displacement": is_displacement,
        "fvg_type": fvg_type,
        "fvg_zone": (fvg_bottom, fvg_top) if fvg_bottom else None,
        "control": control,
        "bias": bias,
        "score": score
    }

def calculate_ai_rrr(res_htf, res_exec):
    bias = res_htf['bias']
    curr_price = res_exec['price']
    
    if bias == "LONG":
        entry = res_exec['fvg_zone'][0] if res_exec['fvg_zone'] else curr_price
        sl = res_exec['ssl']
        target_bsl = res_htf['bsl']
        risk = abs(entry - sl)
        htf_reward = abs(target_bsl - entry)
        if risk == 0: return 2.0, entry, sl, entry + (curr_price * 0.02)
        calculated_rrr = round(htf_reward / risk, 2)
        ai_rrr = max(1.5, min(calculated_rrr, 4.5))
        tp = entry + (risk * ai_rrr)
        return ai_rrr, entry, sl, tp

    elif bias == "SHORT":
        entry = res_exec['fvg_zone'][1] if res_exec['fvg_zone'] else curr_price
        sl = res_exec['bsl']
        target_ssl = res_htf['ssl']
        risk = abs(sl - entry)
        htf_reward = abs(entry - target_ssl)
        if risk == 0: return 2.0, entry, sl, entry - (curr_price * 0.02)
        calculated_rrr = round(htf_reward / risk, 2)
        ai_rrr = max(1.5, min(calculated_rrr, 4.5))
        tp = entry - (risk * ai_rrr)
        return ai_rrr, entry, sl, tp
        
    return 2.0, curr_price, curr_price * 0.98, curr_price * 1.04

async def scan_top_and_custom_coins(exchange_id, limit_coins, custom_symbols_str):
    exchange_class = getattr(ccxta, exchange_id)
    async_exchange = exchange_class({'enableRateLimit': True})
    
    try:
        tickers = await async_exchange.fetch_tickers()
        usdt_pairs = [symbol for symbol in tickers.keys() if symbol.endswith('/USDT') or symbol.endswith('USDT')]
        sorted_by_vol = sorted(usdt_pairs, key=lambda x: tickers[x].get('quoteVolume', 0) or 0, reverse=True)[:limit_coins]
        
        target_coins = [s.replace('/', '') for s in sorted_by_vol]
        
        if custom_symbols_str.strip():
            custom_list = [c.strip().upper().replace('/', '') for c in custom_symbols_str.split(',') if c.strip()]
            for c_sym in custom_list:
                if not c_sym.endswith('USDT'):
                    c_sym += 'USDT'
                if c_sym not in target_coins:
                    target_coins.append(c_sym)

        tasks = []
        for symbol in target_coins:
            tasks.append(fetch_ohlcv_async(async_exchange, symbol, tfs['HTF']))
            tasks.append(fetch_ohlcv_async(async_exchange, symbol, tfs['Exec']))

        results = await asyncio.gather(*tasks)
        await async_exchange.close()
        return target_coins, results
    except Exception as e:
        await async_exchange.close()
        st.error(f"Exchange connection error: {e}")
        return [], []

def plot_smc_chart(df, res, symbol):
    df_plot = df.tail(60)
    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=df_plot['timestamp'],
        open=df_plot['open'], high=df_plot['high'],
        low=df_plot['low'], close=df_plot['close'],
        increasing_line_color='#26a69a', 
        decreasing_line_color='#ef5350',
        name="Price"
    ))

    fig.add_hline(y=res['bsl'], line_dash="dash", line_color="#26a69a", annotation_text=" BSL Target", annotation_position="top right")
    fig.add_hline(y=res['ssl'], line_dash="dash", line_color="#ef5350", annotation_text=" SSL Target", annotation_position="bottom right")

    if res['fvg_zone']:
        fvg_color = "rgba(38, 166, 154, 0.2)" if res['fvg_type'] == "BULLISH FVG" else "rgba(239, 83, 80, 0.2)"
        fig.add_hrect(
            y0=res['fvg_zone'][0], y1=res['fvg_zone'][1],
            fillcolor=fvg_color, opacity=0.8,
            line_width=0, annotation_text=f" {res['fvg_type']}"
        )

    fig.update_layout(
        title=dict(text=f"{symbol} — {tfs['Exec']} Structure & FVG"),
        template="plotly_dark",
        height=450,
        margin=dict(l=15, r=15, t=35, b=15),
        showlegend=False
    )
    return fig

# --- MAIN RUN ACTION ---
if st.button(f"🚀 EXECUTE MARKET SCAN ({scan_limit} PAIRS)", type="primary"):
    with st.spinner("⏳ Running Async Order Flow Engine..."):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        symbols, raw_data = loop.run_until_complete(scan_top_and_custom_coins(exchange_name, scan_limit, custom_symbols_input))

        coin_data = {}
        for sym, tf, df in raw_data:
            if sym not in coin_data: coin_data[sym] = {}
            if tf == tfs['HTF']: coin_data[sym]['HTF'] = df
            elif tf == tfs['Exec']: coin_data[sym]['Exec'] = df

        scan_results = []
        for sym, data in coin_data.items():
            if 'HTF' in data and 'Exec' in data:
                res_htf = analyze_ict_narrative(data['HTF'])
                res_exec = analyze_ict_narrative(data['Exec'])

                if res_htf and res_exec:
                    total_conviction = int((res_htf['score'] * 0.6) + (res_exec['score'] * 0.4))
                    if total_conviction >= min_conviction:
                        ai_rrr, entry, sl, tp = calculate_ai_rrr(res_htf, res_exec)
                        
                        scan_results.append({
                            "Symbol": sym,
                            "Bias": res_htf['bias'],
                            "Conviction Score": f"{total_conviction}%",
                            "Raw Score": total_conviction,
                            "AI RRR": f"1:{ai_rrr}",
                            "HTF Control": res_htf['control'],
                            "Displacement": "CONFIRMED ⚡" if res_exec['displacement'] else "NONE",
                            "Price": f"${res_exec['price']:.4f}",
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "res_exec": res_exec,
                            "res_htf": res_htf,
                            "df_exec": data['Exec']
                        })

        if scan_results:
            scan_results = sorted(scan_results, key=lambda x: x['Raw Score'], reverse=True)
            # Simpan hasil scan ke session_state agar tahan refresh saat interaksi UI
            st.session_state.scan_results = scan_results
            st.session_state.scanned_symbols_count = len(symbols)
        else:
            st.session_state.scan_results = []
            st.session_state.scanned_symbols_count = len(symbols)

# --- MENAMPILKAN HASIL DARI SESSION STATE ---
if st.session_state.scan_results is not None:
    scan_results = st.session_state.scan_results
    scanned_count = st.session_state.scanned_symbols_count

    if scan_results:
        # --- METRICS DASHBOARD ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scanned Pairs", scanned_count)
        m2.metric("High-Conviction Setups", len(scan_results))
        m3.metric("Strategy Mode", selected_mode.split()[1])
        m4.metric("Conviction Threshold", f"{min_conviction}%")

        st.divider()

        # --- TABS INTERFACE ---
        tab_matrix, tab_charts, tab_narrative = st.tabs([
            "📊 Live Signal Matrix", 
            "📈 Interactive Chart & Execution", 
            "🤖 AI Market Narrative"
        ])

        with tab_matrix:
            st.subheader("High-Conviction Signal Matrix")
            df_summary = pd.DataFrame([{
                "Symbol": r['Symbol'],
                "Direction Bias": r['Bias'],
                "Conviction Score": r['Conviction Score'],
                "AI Dynamic RRR": r['AI RRR'],
                "HTF Whale Control": r['HTF Control'],
                "Displacement": r['Displacement'],
                "Current Price": r['Price']
            } for r in scan_results])

            st.dataframe(df_summary, use_container_width=True)

        with tab_charts:
            st.subheader("Deep Structure Inspector")
            
            selected_symbol = st.selectbox(
                "Select Pair to Inspect:", 
                options=[r['Symbol'] for r in scan_results],
                key="pair_inspector_selectbox"  # Key unik untuk mempertahankan state
            )

            item = next(r for r in scan_results if r['Symbol'] == selected_symbol)
            res_e = item['res_exec']

            col_chart, col_board = st.columns([1.5, 1])

            with col_chart:
                fig = plot_smc_chart(item['df_exec'], res_e, item['Symbol'])
                st.plotly_chart(fig, use_container_width=True)

            with col_board:
                st.subheader(f"🎯 Execution: {item['Symbol']}")
                
                if item['Bias'] == "LONG":
                    st.success(f"**DIRECTION:** LONG 🟢")
                else:
                    st.error(f"**DIRECTION:** SHORT 🔴")
                
                st.write(f"**Optimal Entry (FVG):** `${item['entry']:.4f}`")
                st.write(f"**Stop Loss (SL):** `${item['sl']:.4f}`")
                st.write(f"**Take Profit (TP):** `${item['tp']:.4f}`")
                st.write(f"**AI Adaptive RRR:** `{item['AI RRR']}`")
                
                st.divider()
                st.write("**Institutional Narrative:**")
                st.caption(f"Whale Control via {res_e['control']}. Target HTF Liquidity: ${item['res_htf']['bsl'] if item['Bias'] == 'LONG' else item['res_htf']['ssl']:.4f}")

        with tab_narrative:
            top_3_symbols = ", ".join([r['Symbol'] for r in scan_results[:3]])
            st.info(f"""
            ### 🤖 Institutional Order Flow Summary
            Based on **{scanned_count} pairs scanned**, institutional activity indicates strongest **{scan_results[0]['Bias']}** expansion on: **{top_3_symbols}**.
            
            * **AMD Phase:** Manipulation (sweep) completed on HTF, transitioning into Distribution expansion.
            * **Displacement:** Confirmed with active Imbalance (FVG) for limit order entries.
            """)

    else:
        st.warning(f"No pairs matched Conviction Score $\ge$ {min_conviction}% in this cycle.")
