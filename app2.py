import datetime
import sqlite3
import MetaTrader5 as mt5
import pandas as pd
import requests
import streamlit as st

# --- CONFIGURAZIONE PAGINA STREAMLIT ---
st.set_page_config(
    page_title="Forex & Multi-Asset Decision Engine PRO",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Multi-Asset Decision Engine & Performance Analytics")
st.markdown(
    "Piattaforma Multi-Asset: **Scanner H4** | **MTF Trend** | **Performance"
    " Stats** | **Risk Execution**"
)

# --- WATCHLIST MULTI-ASSET ---
WATCHLIST = {
    "Forex": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY", "GBPJPY"],
    "Indici": ["US500", "USTECH", "DE30", "UK100"],
    "Commodity": ["XAUUSD", "XAGUSD", "WTI"],
    "Stock": ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN"],
}


# --- DATABASE SQLITE CON MIGRAZIONE AUTOMATICA ---
def init_db():
  conn = sqlite3.connect("trading_journal_pro.db")
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER,
            timestamp TEXT,
            symbol TEXT,
            order_type TEXT,
            volume REAL,
            price REAL,
            sl REAL,
            tp REAL,
            risk_percent REAL,
            confluence_score INTEGER,
            profit REAL DEFAULT 0.0,
            status TEXT DEFAULT 'ATTIVO'
        )
    """)

  # Migrazione schema automatica per evitare NameError/OperationalError
  cursor.execute("PRAGMA table_info(journal)")
  columns = [column[1] for column in cursor.fetchall()]

  if "ticket" not in columns:
    cursor.execute("ALTER TABLE journal ADD COLUMN ticket INTEGER")
  if "profit" not in columns:
    cursor.execute("ALTER TABLE journal ADD COLUMN profit REAL DEFAULT 0.0")

  conn.commit()
  conn.close()


init_db()


def log_trade_to_db(
    ticket,
    symbol,
    order_type,
    volume,
    price,
    sl,
    tp,
    risk_percent,
    confluence_score,
):
  conn = sqlite3.connect("trading_journal_pro.db")
  cursor = conn.cursor()
  now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  cursor.execute(
      """
        INSERT INTO journal (ticket, timestamp, symbol, order_type, volume, price, sl, tp, risk_percent, confluence_score, profit, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'ATTIVO')
    """,
      (
          ticket,
          now,
          symbol,
          order_type,
          volume,
          price,
          sl,
          tp,
          risk_percent,
          confluence_score,
      ),
  )
  conn.commit()
  conn.close()


def update_closed_trades_in_db():
  """Sincronizza lo stato delle posizioni chiuse su MT5 aggiornando il database."""
  conn = sqlite3.connect("trading_journal_pro.db")
  cursor = conn.cursor()

  cursor.execute("SELECT id, ticket FROM journal WHERE status = 'ATTIVO'")
  active_trades = cursor.fetchall()

  if not active_trades:
    conn.close()
    return

  history_deals = mt5.history_deals_get(
      datetime.datetime.now() - datetime.timedelta(days=30),
      datetime.datetime.now(),
  )

  if history_deals:
    deal_dict = {deal.position_id: deal.profit for deal in history_deals}
    for db_id, ticket in active_trades:
      if ticket and ticket in deal_dict:
        profit = deal_dict[ticket]
        cursor.execute(
            """
                    UPDATE journal SET profit = ?, status = 'CHIUSO' WHERE id = ?
                """,
            (profit, db_id),
        )

  conn.commit()
  conn.close()


# --- CONNESSIONE MT5 ---
def connect_mt5():
  if not mt5.initialize():
    return False, mt5.last_error()
  return True, mt5.terminal_info().name


connected, term_info = connect_mt5()

if not connected:
  st.error(
      f"❌ Impossibile connettersi a MetaTrader 5. Errore: {term_info}. Assicurati"
      " che MT5 sia aperto."
  )
  st.stop()
else:
  st.sidebar.success(f"Connesso a MT5: {term_info}")

update_closed_trades_in_db()

# --- MONITOR CONTO IN TEMPO REALE ---
account_info = mt5.account_info()
if account_info:
  col1, col2, col3, col4 = st.columns(4)
  col1.metric("Bilancio", f"€ {account_info.balance:,.2f}")
  col2.metric("Equity", f"€ {account_info.equity:,.2f}")
  col3.metric("Margine Libero", f"€ {account_info.margin_free:,.2f}")
  leverage = (
      account_info.leverage if hasattr(account_info, "leverage") else 100
  )
  col4.metric("Leva Conto", f"1:{leverage}")

st.divider()


# --- UTILS: PIP SIZE ---
def get_pip_size(s_info):
  if not s_info:
    return 0.0001
  if s_info.digits in (3, 5):
    return 10 * s_info.point
  elif s_info.digits in (1, 2):
    return s_info.point
  elif s_info.digits == 0:
    return 1.0
  return s_info.point


# --- MOTORE S/R E TARGET ---
def find_h4_reversal_sr(df_h4, current_price, pip_size=0.0001, left_right=2):
  pivot_highs, pivot_lows = [], []
  for i in range(left_right, len(df_h4) - left_right - 1):
    high_i, low_i = df_h4["high"].iloc[i], df_h4["low"].iloc[i]
    if all(
        high_i > df_h4["high"].iloc[i - j] for j in range(1, left_right + 1)
    ) and all(
        high_i > df_h4["high"].iloc[i + j] for j in range(1, left_right + 1)
    ):
      pivot_highs.append(high_i)
    if all(
        low_i < df_h4["low"].iloc[i - j] for j in range(1, left_right + 1)
    ) and all(
        low_i < df_h4["low"].iloc[i + j] for j in range(1, left_right + 1)
    ):
      pivot_lows.append(low_i)

  closest_resistance = (
      min([p for p in pivot_highs if p >= current_price])
      if [p for p in pivot_highs if p >= current_price]
      else None
  )
  closest_support = (
      max([p for p in pivot_lows if p <= current_price])
      if [p for p in pivot_lows if p <= current_price]
      else None
  )

  dist_res_pips = (
      (closest_resistance - current_price) / pip_size
      if closest_resistance
      else 999.0
  )
  dist_sup_pips = (
      (current_price - closest_support) / pip_size if closest_support else 999.0
  )

  is_at_support = dist_sup_pips <= 25.0
  is_at_resistance = dist_res_pips <= 25.0

  return {
      "closest_support": closest_support,
      "closest_resistance": closest_resistance,
      "is_at_support": is_at_support,
      "is_at_resistance": is_at_resistance,
      "is_near_sr": is_at_support or is_at_resistance,
  }


def find_h4_structural_target(
    df_h4, current_price, order_type_str, pip_size=0.0001, min_pips=25.0
):
  pivot_highs, pivot_lows = [], []
  left_right = 3
  for i in range(left_right, len(df_h4) - left_right - 1):
    high_i, low_i = df_h4["high"].iloc[i], df_h4["low"].iloc[i]
    if all(
        high_i > df_h4["high"].iloc[i - j] for j in range(1, left_right + 1)
    ) and all(
        high_i > df_h4["high"].iloc[i + j] for j in range(1, left_right + 1)
    ):
      pivot_highs.append(high_i)
    if all(
        low_i < df_h4["low"].iloc[i - j] for j in range(1, left_right + 1)
    ) and all(
        low_i < df_h4["low"].iloc[i + j] for j in range(1, left_right + 1)
    ):
      pivot_lows.append(low_i)

  if "BUY" in order_type_str:
    valid = [
        p
        for p in pivot_highs
        if (p - current_price) / pip_size >= min_pips
    ]
    return min(valid) if valid else None
  else:
    valid = [
        p
        for p in pivot_lows
        if (current_price - p) / pip_size >= min_pips
    ]
    return max(valid) if valid else None


@st.cache_data(ttl=1800)
def check_macro_news(symbol):
  curr_base = symbol[:3]
  curr_quote = symbol[3:] if len(symbol) >= 6 else "USD"
  try:
    url = "https://nfp.ourforecast.com/api/v1/events"
    res = requests.get(url, timeout=3)
    if res.status_code == 200:
      events = res.json()
      if [
          e
          for e in events
          if e.get("impact") == "High"
          and (e.get("country") in [curr_base, curr_quote])
      ]:
        return False, f"⚠️ Notizia High Impact oggi per {curr_base}/{curr_quote}"
  except Exception:
    pass
  return True, "✅ Nessuna Notizia High Impact Imminente"


def analyze_mtf_market(sym):
  if not mt5.symbol_select(sym, True):
    return None

  rates_d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 100)
  if rates_d1 is None or len(rates_d1) < 50:
    return None
  df_d1 = pd.DataFrame(rates_d1)
  df_d1["ema50"] = df_d1["close"].ewm(span=50, adjust=False).mean()
  d1_bullish = df_d1["close"].iloc[-2] > df_d1["ema50"].iloc[-2]

  rates_h4 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 150)
  if rates_h4 is None or len(rates_h4) < 50:
    return None
  df_h4 = pd.DataFrame(rates_h4)
  df_h4["ema50"] = df_h4["close"].ewm(span=50, adjust=False).mean()

  s_info = mt5.symbol_info(sym)
  pip_size = get_pip_size(s_info)

  last_candle = df_h4.iloc[-2]
  close_p, open_p, high_p, low_p, ema_val = (
      last_candle["close"],
      last_candle["open"],
      last_candle["high"],
      last_candle["low"],
      last_candle["ema50"],
  )
  h4_bullish = close_p > ema_val

  body_size, total_range = abs(close_p - open_p), high_p - low_p
  pattern_name, has_pattern = "Nessun pattern evidente", False
  if total_range > 0 and (body_size / total_range) < 0.35:
    if (min(open_p, close_p) - low_p) > (
        2 * body_size
    ):
      pattern_name, has_pattern = "🔨 Hammer / Pin Bar Rialzista (H4)", True
    elif (high_p - max(open_p, close_p)) > (2 * body_size):
      pattern_name, has_pattern = (
          "📐 Shooting Star / Pin Bar Ribassista (H4)",
          True,
      )

  sr_info = find_h4_reversal_sr(df_h4, close_p, pip_size=pip_size)
  news_safe, news_msg = check_macro_news(sym)

  return {
      "close": close_p,
      "d1_bullish": d1_bullish,
      "h4_bullish": h4_bullish,
      "pattern": pattern_name,
      "has_pattern": has_pattern,
      "sr": sr_info,
      "news_safe": news_safe,
      "news_msg": news_msg,
      "pip_size": pip_size,
      "df_rates": df_h4,
  }


def auto_manage_positions():
  positions = mt5.positions_get()
  if not positions:
    return 0
  updated_count = 0
  for pos in positions:
    symbol, ticket, entry_price, current_sl, pos_type = (
        pos.symbol,
        pos.ticket,
        pos.price_open,
        pos.sl,
        pos.type,
    )
    s_info = mt5.symbol_info(symbol)
    if not s_info or current_sl == 0:
      continue
    pip_size = get_pip_size(s_info)
    current_price = (
        s_info.bid if pos_type == mt5.ORDER_TYPE_BUY else s_info.ask
    )
    sl_dist = abs(entry_price - current_sl) / pip_size
    profit_pips = (
        (current_price - entry_price) / pip_size
        if pos_type == mt5.ORDER_TYPE_BUY
        else (entry_price - current_price) / pip_size
    )

    if profit_pips >= sl_dist:
      if pos_type == mt5.ORDER_TYPE_BUY and current_sl < entry_price:
        if (
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": entry_price + (1 * pip_size),
                "tp": pos.tp,
            }).retcode
            == mt5.TRADE_RETCODE_DONE
        ):
          updated_count += 1
      elif pos_type == mt5.ORDER_TYPE_SELL and current_sl > entry_price:
        if (
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": entry_price - (1 * pip_size),
                "tp": pos.tp,
            }).retcode
            == mt5.TRADE_RETCODE_DONE
        ):
          updated_count += 1
  return updated_count


# --- NAVIGAZIONE A TABS ---
tab_scanner, tab_execution, tab_management, tab_journal = st.tabs([
    "📡 Market Scanner (H4)",
    "🚀 Execution Engine & MTF",
    "⚙️ Posizioni & Trailing Manager",
    "📊 Journal & Performance Stats",
])


# ==========================================
# TAB 0: MARKET SCANNER
# ==========================================
with tab_scanner:
  st.subheader("📡 Scansione Automatica del Mercato & Selezione Opportunità")
  selected_categories = st.multiselect(
      "Seleziona Asset Class da Scansionare",
      list(WATCHLIST.keys()),
      default=["Forex", "Indici", "Commodity"],
  )
  if st.button("🔎 AVVIA SCANSIONE MERCATO", use_container_width=True):
    scan_symbols = [
        sym for cat in selected_categories for sym in WATCHLIST[cat]
    ]
    results = []
    progress_bar = st.progress(0)
    for idx, sym in enumerate(scan_symbols):
      progress_bar.progress((idx + 1) / len(scan_symbols))
      data = analyze_mtf_market(sym)
      if data is None:
        continue

      buy_score = sum([
          data["d1_bullish"] and data["h4_bullish"],
          data["sr"]["is_at_support"],
          data["has_pattern"],
          data["news_safe"],
      ])
      sell_score = sum([
          (not data["d1_bullish"]) and (not data["h4_bullish"]),
          data["sr"]["is_at_resistance"],
          data["has_pattern"],
          data["news_safe"],
      ])
      best_score = max(buy_score, sell_score)
      direction = "BUY 🟢" if buy_score >= sell_score else "SELL 🔴"

      results.append({
          "Simbolo": sym,
          "Direzione Consigliata": direction,
          "Confluenza Score": f"{best_score} / 4",
          "Prezzo Attuale": f"{data['close']:.5f}",
          "Pattern H4": data["pattern"],
          "Zona S/R": (
              "🎯 In Zona S/R" if data["sr"]["is_near_sr"] else "↔️ Fuori Zona"
          ),
          "News Guard": "✅ Pulito" if data["news_safe"] else "⚠️ Rischio",
          "raw_score": best_score,
      })
    progress_bar.empty()
    if results:
      df_results = (
          pd.DataFrame(results)
          .sort_values(by="raw_score", ascending=False)
          .drop(columns=["raw_score"])
      )
      st.success("Scansione completata!")
      st.dataframe(df_results, use_container_width=True)
    else:
      st.warning("Nessun dato disponibile sul terminale MT5.")


# ==========================================
# TAB 1: EXECUTION ENGINE
# ==========================================
with tab_execution:
  st.markdown(
      """
        <style>
        div[data-testid="stMetricLabel"] p { font-size: 12px !important; font-weight: 600 !important; color: #888888; }
        div[data-testid="stMetricValue"] div { font-size: 15px !important; font-weight: bold !important; }
        div[data-testid="stCheckbox"] label p { font-size: 13px !important; font-weight: 500; }
        </style>
    """,
      unsafe_allow_html=True,
  )

  all_symbols = [item for sublist in WATCHLIST.values() for item in sublist]
  col_select1, col_select2 = st.columns(2)
  with col_select1:
    symbol = st.selectbox("Seleziona Simbolo", all_symbols)
  with col_select2:
    order_type_str = st.radio("Direzione Trade", ["BUY (Long)", "SELL (Short)"])

  mtf_data = analyze_mtf_market(symbol)

  st.markdown("#### 🔍 Analisi Multi-Timeframe")
  if mtf_data:
    col_a1, col_a2, col_a3, col_a4 = st.columns(4)
    col_a1.metric(
        "Bias Macro D1",
        (
            "🟢 RIALZISTA (D1)"
            if mtf_data["d1_bullish"]
            else "🔴 RIBASSISTA (D1)"
        ),
    )
    col_a2.metric(
        "Trend H4",
        (
            "🟢 RIALZISTA (H4)"
            if mtf_data["h4_bullish"]
            else "🔴 RIBASSISTA (H4)"
        ),
    )
    col_a3.metric(
        "Pivot S/R H4",
        (
            "🎯 In Zona S/R"
            if mtf_data["sr"]["is_near_sr"]
            else "↔️ Fuori Zona Inversione"
        ),
    )
    col_a4.metric(
        "Filtro Macro News",
        "✅ PULITO" if mtf_data["news_safe"] else "⚠️ ATTENZIONE",
    )

  st.divider()
  st.subheader("📋 Checklist Operativa Multi-Timeframe")

  auto_trend_check = (
      (mtf_data["d1_bullish"] and mtf_data["h4_bullish"])
      if ("BUY" in order_type_str and mtf_data)
      else (
          (not mtf_data["d1_bullish"] and not mtf_data["h4_bullish"])
          if mtf_data
          else False
      )
  )
  auto_sr_check = (
      (
          mtf_data["sr"]["is_at_support"]
          if "BUY" in order_type_str
          else mtf_data["sr"]["is_at_resistance"]
      )
      if mtf_data
      else False
  )
  auto_pattern_check = mtf_data["has_pattern"] if mtf_data else False
  auto_news_check = mtf_data["news_safe"] if mtf_data else True

  col_check1, col_check2 = st.columns(2)
  with col_check1:
    check_trend = st.checkbox(
        "1. Allineamento Trend Multi-Timeframe (D1 + H4)",
        value=auto_trend_check,
    )
    check_sr = st.checkbox(
        "2. Zona S/R Inversione H4 (Test Supporto/Resistenza Swing)",
        value=auto_sr_check,
    )
  with col_check2:
    check_pattern = st.checkbox(
        "3. Pattern Price Action Pulito (H4)", value=auto_pattern_check
    )
    check_news = st.checkbox(
        "4. Nessun Dato Macro ad Alto Impatto", value=auto_news_check
    )

  confluence_score = sum([check_trend, check_sr, check_pattern, check_news])
  if confluence_score == 4:
    st.success(f"✅ **Confluenza Ottimale (4/4)** — Operatività consentita.")
    is_trade_allowed = True
  else:
    st.warning(
        f"⚠️ **Confluenza Incompleta ({confluence_score}/4)** — Esecuzione"
        " bloccata."
    )
    is_trade_allowed = False

  st.divider()
  default_sl_pips = 40.0
  if mtf_data:
    curr_p, p_size = mtf_data["close"], mtf_data["pip_size"]
    if "BUY" in order_type_str and mtf_data["sr"]["closest_support"]:
      default_sl_pips = round(
          (curr_p - (mtf_data["sr"]["closest_support"] - (10 * p_size)))
          / p_size,
          1,
      )
    elif "SELL" in order_type_str and mtf_data["sr"]["closest_resistance"]:
      default_sl_pips = round(
          ((mtf_data["sr"]["closest_resistance"] + (10 * p_size)) - curr_p)
          / p_size,
          1,
      )

  col_b1, col_b2 = st.columns(2)
  with col_b1:
    risk_percent = st.number_input(
        "Rischio Capitale (%)", 0.1, 2.0, 1.0, 0.1
    )
    sl_pips = st.number_input(
        "Stop Loss Strutturale (Pips + Buffer)",
        1.0,
        1000.0,
        float(default_sl_pips),
        5.0,
    )
  with col_b2:
    rr_ratio = st.number_input(
        "Rapporto Rischio/Rendimento Minimo (RR)", 1.0, 5.0, 2.0, 0.5
    )

  with st.form("trade_form"):
    submitted = st.form_submit_button(
        "🚀 ESEGUI ORDINE A MERCATO (PROTETTO)",
        disabled=not is_trade_allowed,
        use_container_width=True,
    )


# Esecuzione Ordine
def calculate_dynamic_lot(sym, risk_pct, sl_distance_points):
  acc = mt5.account_info()
  if not acc:
    return 0.01
  risk_money = acc.equity * (risk_pct / 100.0)
  s_info = mt5.symbol_info(sym)
  if not s_info or s_info.trade_tick_value <= 0 or sl_distance_points <= 0:
    return 0.01
  risk_per_lot = (
      sl_distance_points / s_info.trade_tick_size
  ) * s_info.trade_tick_value
  raw_lot = risk_money / risk_per_lot if risk_per_lot > 0 else 0.01
  return max(
      s_info.volume_min,
      min(
          round(
              round(raw_lot / s_info.volume_step) * s_info.volume_step, 2
          ),
          s_info.volume_max,
      ),
  )


if submitted and is_trade_allowed:
  if not mt5.symbol_select(symbol, True):
    st.error(f"Simbolo {symbol} non disponibile.")
  else:
    s_info = mt5.symbol_info(symbol)
    point, sl_points = s_info.point, sl_pips * (
        10 if s_info.digits in (3, 5) else 1
    )
    tp_points = sl_points * rr_ratio

    if "BUY" in order_type_str:
      o_type, price, sl, tp, o_label = (
          mt5.ORDER_TYPE_BUY,
          s_info.ask,
          s_info.ask - (sl_points * point),
          s_info.ask + (tp_points * point),
          "BUY",
      )
    else:
      o_type, price, sl, tp, o_label = (
          mt5.ORDER_TYPE_SELL,
          s_info.bid,
          s_info.bid + (sl_points * point),
          s_info.bid - (tp_points * point),
          "SELL",
      )

    lot_size = calculate_dynamic_lot(symbol, risk_percent, sl_points)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": o_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 778899,
        "comment": "Analytics Engine",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": (
            mt5.ORDER_FILLING_IOC
            if (s_info.filling_mode & 2)
            else mt5.ORDER_FILLING_FOK
        ),
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
      st.error(f"❌ Ordine fallito! Codice broker: {result.retcode}")
    else:
      st.success(
          f"✅ Ordine eseguito! Ticket: {result.order} | Lotto: {lot_size}"
      )
      log_trade_to_db(
          result.order,
          symbol,
          o_label,
          lot_size,
          price,
          sl,
          tp,
          risk_percent,
          confluence_score,
      )


# ==========================================
# TAB 2: ACTIVE POSITIONS
# ==========================================
with tab_management:
  st.subheader("📊 Monitor Posizioni & Trailing Stop Strutturale")
  if st.button("🔄 AUTO-GESTISCI POSIZIONI (BE + H1 Trailing)"):
    if auto_manage_positions() > 0:
      st.success("Posizioni aggiornate!")
      st.rerun()
    else:
      st.info("Nessun adeguamento necessario.")

  positions = mt5.positions_get()
  if positions is None or len(positions) == 0:
    st.info("Nessuna posizione aperta.")
  else:
    df_pos = pd.DataFrame([
        {
            "Ticket": p.ticket,
            "Simbolo": p.symbol,
            "Tipo": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "Lotti": p.volume,
            "Ingresso": p.price_open,
            "SL": p.sl,
            "TP": p.tp,
            "Profitto (€)": p.profit,
        }
        for p in positions
    ])
    st.dataframe(df_pos, use_container_width=True)


# ==========================================
# TAB 3: JOURNAL & PERFORMANCE STATS
# ==========================================
with tab_journal:
  st.subheader("📊 Pannello di Performance & Statistiche di Strategia")

  update_closed_trades_in_db()

  conn = sqlite3.connect("trading_journal_pro.db")
  df_journal = pd.read_sql_query(
      "SELECT * FROM journal ORDER BY id DESC", conn
  )
  conn.close()

  if df_journal.empty:
    st.info("Nessun trade registrato nel database.")
  else:
    df_closed = df_journal[df_journal["status"] == "CHIUSO"]

    total_trades = len(df_journal)
    closed_trades_count = len(df_closed)

    # Inizializziamo valori di default per evitare NameError
    win_rate = 0.0
    net_profit = 0.0
    profit_factor = 0.0

    if closed_trades_count > 0:
      winning_trades = df_closed[df_closed["profit"] > 0]
      losing_trades = df_closed[df_closed["profit"] <= 0]

      win_rate = (len(winning_trades) / closed_trades_count) * 100
      net_profit = df_closed["profit"].sum()

      gross_profit = (
          winning_trades["profit"].sum() if not winning_trades.empty else 0.0
      )
      gross_loss = (
          abs(losing_trades["profit"].sum()) if not losing_trades.empty else 0.0
      )

      profit_factor = (
          (gross_profit / gross_loss)
          if gross_loss > 0
          else (99.0 if gross_profit > 0 else 0.0)
      )

    # Metriche principali in alto
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Trade Chiusi", closed_trades_count)
    m2.metric("Win Rate", f"{win_rate:.1f}%")
    m3.metric("Profitto Netto", f"€ {net_profit:,.2f}")
    m4.metric("Profit Factor", f"{profit_factor:.2f}")
    m5.metric("Trade Totali (Anche Attivi)", total_trades)

    st.divider()

    st.markdown("##### 📈 Curva dei Profitti (Equity Curve)")
    if not df_closed.empty:
      df_closed_sorted = df_closed.sort_values(by="id")
      df_closed_sorted["Cumulative_Profit"] = df_closed_sorted[
          "profit"
      ].cumsum()
      st.line_chart(df_closed_sorted, x="timestamp", y="Cumulative_Profit")
    else:
      st.info(
          "Ancora nessun trade chiuso per tracciare la curva di rendimento."
      )

    st.divider()

    st.markdown("##### 📓 Storico Completo del Journal (SQLite)")
    st.dataframe(df_journal, use_container_width=True)
