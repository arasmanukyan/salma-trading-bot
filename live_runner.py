import time, threading, logging, json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import ccxt, yfinance as yf, pandas as pd
from bot import TradingBot
from indicators import calculate_all_indicators
from formatting import fmt_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("LIVE_BOT")

# Genis BIST hisse evreni (BIST100 + Ana Pazar aktif hisseler)
BIST_ALL_URL = "https://raw.githubusercontent.com/AhmetOzer/bist-hisseleri/main/hisseler.json"
FALLBACK_BIST = [
    "THYAO.IS","ASELS.IS","GARAN.IS","AKBNK.IS","SISE.IS","TUPRS.IS","KCHOL.IS","EREGL.IS","FROTO.IS","BIMAS.IS",
    "YKBNK.IS","ISCTR.IS","SAHOL.IS","TCELL.IS","TTKOM.IS","ENKAI.IS","PGSUS.IS","TAVHL.IS","TOASO.IS","ARCLK.IS",
    "PETKM.IS","KOZAL.IS","KOZAA.IS","IPEKE.IS","SASA.IS","HEKTS.IS","ASTOR.IS","KONTR.IS","SMRTG.IS","EUPWR.IS",
    "GESAN.IS","ALFAS.IS","MIATK.IS","REEDR.IS","VESTL.IS","ZOREN.IS","ODAS.IS","AKSEN.IS","ALARK.IS","OYAKC.IS",
    "CIMSA.IS","AKCNS.IS","MGROS.IS","SOKM.IS","AEFES.IS","CCOLA.IS","ULKER.IS","DOAS.IS","OTKAR.IS","TTRAK.IS",
    "EKGYO.IS","ISGYO.IS","TRGYO.IS","HALKB.IS","VAKBN.IS","TSKB.IS","SKBNK.IS","GUBRF.IS","BAGFS.IS","BRSAN.IS",
    "BRYAT.IS","BORLS.IS","MAVI.IS","LOGO.IS","ARDYZ.IS","YEOTK.IS","CWENE.IS","ENERY.IS","IZENR.IS","KLSER.IS"
]

def get_all_bist_symbols():
    try:
        req = urllib.request.urlopen(BIST_ALL_URL, timeout=10)
        data = json.loads(req.read().decode("utf-8"))
        syms = [f"{x['kod'] if isinstance(x, dict) else x}.IS" for x in data]
        if len(syms) > 50:
            return sorted(list(set(syms)))
    except Exception:
        pass
    return FALLBACK_BIST

def get_all_binance_usdt_symbols(min_vol_usdt=1000000):
    ex = ccxt.kucoin({"enableRateLimit": True})
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    skip_words = ("UP/", "DOWN/", "BULL/", "BEAR/", "USDC/", "FDUSD/", "TUSD/", "USDP/", "EUR/", "TRY/")
    syms = []
    for sym, m in markets.items():
        if not m.get("active") or not m.get("spot") or not sym.endswith("/USDT"):
            continue
        if any(w in sym for w in skip_words):
            continue
        qv = tickers.get(sym, {}).get("quoteVolume") or 0
        if qv >= min_vol_usdt:
            syms.append(sym)
    return sorted(syms)


class LiveEngine:
    def __init__(self):
        self.cfg = json.load(open("config.json", encoding="utf-8"))
        self.bot = TradingBot("config.json")
        self.token = self.cfg.get("telegram", {}).get("fallback_token", "")
        self.chat_id = str(self.cfg.get("telegram", {}).get("fallback_chat_id", ""))
        self.last_update_id = 0

        # Load strategy config
        self.strategy_cfg = self.cfg.get("strategy", {})
        self.indicators_cfg = self.cfg.get("indicators", {})
        self.markets_cfg = self.cfg.get("markets", {})

        # Filter thresholds
        self.rsi_min = self.indicators_cfg.get("RSI", {}).get("min", 50)
        self.rsi_max = self.indicators_cfg.get("RSI", {}).get("max", 72)
        self.rvol_min = self.indicators_cfg.get("RVOL", {}).get("min", 1.2)
        self.max_trend_age = self.strategy_cfg.get("max_trend_age_bars", 6)
        self.salma_pullback_pct = self.strategy_cfg.get("salma_pullback_proximity_pct", 1.5)

        # Stop loss config
        self.sl_cfg = self.strategy_cfg.get("stop_loss", {"type": "ATR", "atr_multiplier": 2.0, "max_pct": 0.05})
        self.ts_cfg = self.strategy_cfg.get("trailing_stop", {"enabled": True, "activation_pct": 0.04, "atr_multiplier": 1.5})

    def send_tg(self, text):
        if not self.token or not self.chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}).encode()
            urllib.request.urlopen(url, data=data, timeout=10)
        except Exception as e:
            log.warning(f"Telegram gonderim hatasi: {e}")

    def build_status_text(self):
        pm = self.bot.portfolio
        bist_bal = pm.get_balance("BIST")
        cry_bal = pm.get_balance("CRYPTO")
        bist_pos = pm.get_open_positions("BIST")
        cry_pos = pm.get_open_positions("CRYPTO")
        lines = [
            "<b>SALMA v3.0 + TRF Canlı Bot Durumu</b>",
            f"BIST Kasa: <b>{bist_bal:,.2f} TRY</b> ({len(bist_pos)}/3 açık)",
            f"Kripto Kasa: <b>{cry_bal:,.2f} USDT</b> ({len(cry_pos)}/10 açık)",
            f"Panel: http://127.0.0.1:5000"
        ]
        return "\n".join(lines)

    def build_positions_text(self):
        pm = self.bot.portfolio
        all_pos = pm.get_open_positions("BIST") + pm.get_open_positions("CRYPTO")
        if not all_pos:
            return "Şu an açık pozisyon bulunmuyor."
        lines = ["<b>Açık Pozisyonlar:</b>"]
        for p in all_pos:
            lines.append(f"- [{p.market}] <b>{p.symbol}</b> | Adet: {p.quantity:.4f} | Giriş: {fmt_price(p.entry_price)}")
        return "\n".join(lines)

    def is_market_open(self, market: str) -> bool:
        """Check if market is open for trading (weekend protection for BIST)"""
        now = datetime.now(timezone(timedelta(hours=3)))  # Istanbul time
        weekday = now.weekday()  # 0=Monday, 6=Sunday

        market_cfg = self.markets_cfg.get(market, {})
        trading_days = market_cfg.get("trading_hours", {}).get("days", [0,1,2,3,4])

        return weekday in trading_days

    def calculate_signal_score(self, latest, prev, df) -> int:
        """
        Calculate signal score (0-100) based on:
        - Trend freshness (trend_age): newer = higher score
        - RVOL: higher = higher score
        - RSI balance: closer to 60 (middle of 50-72) = higher score
        - SALMA proximity: closer to SALMA = higher score (for pullback entries)
        - Re-acceleration bonus
        """
        score = 0

        # 1. Trend freshness (0-30 points)
        trend_age = latest.get("trend_age", 0)
        if trend_age > 0:
            if trend_age <= 1:
                score += 30  # Fresh crossover
            elif trend_age <= 3:
                score += 25  # Very fresh trend
            elif trend_age <= 6:
                score += 15  # Fresh trend
            else:
                score += 5   # Older trend

        # 2. RVOL (0-25 points)
        rvol = latest.get("rvol", 1.0)
        if rvol >= 3.0:
            score += 25
        elif rvol >= 2.0:
            score += 20
        elif rvol >= 1.5:
            score += 15
        elif rvol >= 1.2:
            score += 10

        # 3. RSI balance (0-20 points) - optimal around 60
        rsi = latest.get("rsi", 50)
        if 50 <= rsi <= 72:
            # Distance from center (61) - closer is better
            dist_from_optimal = abs(rsi - 61)
            if dist_from_optimal <= 3:
                score += 20
            elif dist_from_optimal <= 6:
                score += 15
            elif dist_from_optimal <= 11:
                score += 10
            else:
                score += 5

        # 4. SALMA proximity (0-15 points) - closer to SALMA is better for pullback
        proximity = latest.get("salma_proximity_pct", 100)
        if proximity <= 0.5:
            score += 15
        elif proximity <= 1.0:
            score += 12
        elif proximity <= 1.5:
            score += 10
        elif proximity <= 2.5:
            score += 5

        # 5. Re-acceleration bonus (0-10 points)
        if latest.get("reaccelerated", False):
            score += 10

        return min(score, 100)

    def check_entry_signal(self, latest, prev, df) -> tuple:
        """
        Check if entry conditions are met.
        Returns: (bool, str) - (signal_triggered, signal_type)
        signal_type: "fresh_crossover" or "pullback_reacceleration"
        """
        # Base conditions: TRF Long + SALMA Green
        trf_long = int(latest.get("trf_long", 0)) == 1
        salma_green = int(latest.get("salma_color", 0)) == 1

        if not (trf_long and salma_green):
            return (False, "")

        trend_age = latest.get("trend_age", 0)

        # Condition 1: Fresh crossover (trend_age <= max_trend_age)
        # This includes the salma_red_to_green transition
        if trend_age > 0 and trend_age <= self.max_trend_age:
            # Verify it's a genuine fresh signal - either fresh red_to_green or early trend
            if latest.get("salma_red_to_green", False) or trend_age <= 2:
                return (True, "fresh_crossover")

        # Condition 2: Pullback & re-acceleration
        # Price pulled back to near SALMA (within 1.5%) and then re-accelerated
        if latest.get("reaccelerated", False):
            # Verify the pullback happened recently (within last few bars)
            pulled_back_recent = False
            for i in range(min(5, len(df))):
                idx = -(i+1)
                if df.iloc[idx].get("near_salma", False):
                    pulled_back_recent = True
                    break
            if pulled_back_recent:
                return (True, "pullback_reacceleration")

        return (False, "")

    def check_filters(self, latest) -> tuple:
        """Check RSI and RVOL filters"""
        rsi = latest.get("rsi", 50)
        rvol = latest.get("rvol", 1.0)

        if rsi < self.rsi_min or rsi > self.rsi_max:
            return (False, f"RSI {rsi:.1f} outside [{self.rsi_min}-{self.rsi_max}]")

        if rvol < self.rvol_min:
            return (False, f"RVOL {rvol:.2f} < {self.rvol_min}")

        return (True, "OK")

    def calculate_position_size(self, market: str, price: float) -> float:
        """
        Calculate fixed position size based on initial balance slices.
        BIST: initial_balance / 3 (100000/3)
        CRYPTO: initial_balance / 10 (1000/10)
        """
        pm = self.bot.portfolio
        initial_balance = pm.get_initial_balance(market)
        slices = self.markets_cfg.get(market, {}).get("fixed_position_slices", 1)

        position_value = initial_balance / slices
        qty = position_value / price

        if market == "BIST":
            qty = int(qty)  # Whole shares
        else:
            qty = round(qty, 6)  # 6 decimals for crypto

        return qty

    def calculate_stop_loss(self, market: str, entry_price: float, atr: float, highest_price: float = None) -> float:
        """
        Calculate stop loss price.
        - ATR-based: entry - (atr * multiplier)
        - Cap at max_pct (default 5%)
        - If trailing stop activated: highest_price - (atr * ts_multiplier)
        """
        sl_type = self.sl_cfg.get("type", "ATR")
        atr_mult = self.sl_cfg.get("atr_multiplier", 2.0)
        max_pct = self.sl_cfg.get("max_pct", 0.05)

        if sl_type == "ATR":
            sl_price = entry_price - (atr * atr_mult)
        else:
            sl_price = entry_price * (1 - max_pct)

        # Cap at max percentage loss
        max_sl_price = entry_price * (1 - max_pct)
        sl_price = max(sl_price, max_sl_price)  # Use the higher (less loss) stop

        return sl_price

    def calculate_trailing_stop(self, market: str, highest_price: float, atr: float) -> float:
        """Calculate trailing stop price after activation"""
        if not self.ts_cfg.get("enabled", True):
            return 0
        ts_mult = self.ts_cfg.get("atr_multiplier", 1.5)
        return highest_price - (atr * ts_mult)

    def check_stop_loss_and_signals(self, market: str, symbol: str, df: pd.DataFrame):
        pm = self.bot.portfolio
        df = calculate_all_indicators(df, self.bot.salma_params, self.bot.trf_params)

        # Use last CLOSED candle for signals (iloc[-2])
        latest = df.iloc[-2]
        prev = df.iloc[-3] if len(df) > 2 else latest
        current_price = float(df.iloc[-1]["close"])  # Current forming candle
        atr = float(latest.get("atr", 0))

        # Add to radar if TRF Long + SALMA Green
        if int(latest.get("trf_long", 0)) == 1 and int(latest.get("salma_color", 0)) == 1:
            self.radar_rows.append((
                market, symbol, current_price, "YEŞL",
                int(latest.get("trend_age", 0)),
                datetime.now(timezone(timedelta(hours=3))).strftime("%H:%M:%S")
            ))

        # --- MANAGE EXISTING POSITION ---
        if pm.has_position(market, symbol):
            pos = pm.get_position(market, symbol)
            entry_price = pos.entry_price
            pnl_ratio = (current_price - entry_price) / entry_price
            pnl_pct = pnl_ratio * 100

            # Track highest price for trailing stop
            if not hasattr(pos, 'highest_price') or pos.highest_price < current_price:
                pos.highest_price = current_price

            # Check trailing stop activation
            ts_activated = False
            trailing_stop_price = 0
            if self.ts_cfg.get("enabled", True):
                activation_pct = self.ts_cfg.get("activation_pct", 0.04)
                if pnl_ratio >= activation_pct:
                    ts_activated = True
                    trailing_stop_price = self.calculate_trailing_stop(market, pos.highest_price, atr)

            # Determine effective stop loss
            if ts_activated and trailing_stop_price > 0:
                effective_sl = trailing_stop_price
                sl_reason = f"Trailing Stop ({fmt_price(trailing_stop_price)})"
            else:
                effective_sl = self.calculate_stop_loss(market, entry_price, atr, pos.highest_price if hasattr(pos, 'highest_price') else None)
                sl_reason = f"Stop Loss ({fmt_price(effective_sl)})"

            # Check exit conditions
            exit_triggered = False
            exit_reason = ""

            # 1. SALMA Green to Red
            if latest.get("salma_green_to_red", False):
                exit_triggered = True
                exit_reason = "SALMA Yeşil->Kırmızı"

            # 2. Stop Loss / Trailing Stop hit
            elif current_price <= effective_sl:
                exit_triggered = True
                exit_reason = sl_reason

            if exit_triggered:
                trade = pm.execute_sell(market, symbol, current_price)
                if trade:
                    self.send_tg(
                        f"🔴 <b>SATIŞ [{market}] {symbol}</b>\n"
                        f"Fiyat: {fmt_price(current_price)}\n"
                        f"Sebep: {exit_reason}\n"
                        f"K/Z: {trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)"
                    )
            return

        # --- CHECK NEW ENTRY ---
        # Weekend protection for BIST
        if market == "BIST" and not self.is_market_open("BIST"):
            return

        open_cnt = len(pm.get_open_positions(market))
        max_pos = self.markets_cfg.get(market, {}).get("max_positions", 3)
        if open_cnt >= max_pos:
            return

        # Check entry signal
        signal_triggered, signal_type = self.check_entry_signal(latest, prev, df)
        if not signal_triggered:
            return

        # Check quality filters
        filters_pass, filter_msg = self.check_filters(latest)
        if not filters_pass:
            log.debug(f"{symbol} filtered out: {filter_msg}")
            return

        # Calculate signal score
        score = self.calculate_signal_score(latest, prev, df)

        # Store candidate for ranking
        qty = self.calculate_position_size(market, current_price)
        if qty <= 0:
            return

        # Check if we can actually afford it
        can_trade, reason = pm.can_open_position(market, symbol, current_price, qty)
        if not can_trade:
            log.debug(f"Cannot buy {symbol}: {reason}")
            return

        self.candidates.append({
            "market": market,
            "symbol": symbol,
            "price": current_price,
            "qty": qty,
            "score": score,
            "signal_type": signal_type,
            "rsi": latest.get("rsi", 0),
            "rvol": latest.get("rvol", 0),
            "trend_age": latest.get("trend_age", 0),
            "atr": atr
        })

    def save_radar(self, rows):
        import sqlite3
        con = sqlite3.connect("trading_bot.db")
        con.execute("DELETE FROM radar")
        con.executemany("INSERT INTO radar VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def scan_once(self):
        self.radar_rows = []
        self.candidates = []
        log.info("=== TAM PYASA TARAMASI BAŞLADI ===")

        # 1. CRYPTO (7/24 Tum Binance USDT)
        try:
            cry_syms = get_all_binance_usdt_symbols(min_vol_usdt=1000000)
            log.info(f"Binance aktif USDT parite sayısı: {len(cry_syms)}")
            for sym in cry_syms:
                try:
                    df = self.bot.data_feed.fetch_crypto_data(sym)
                    if df is not None and len(df) >= 60:
                        self.check_stop_loss_and_signals("CRYPTO", sym, df)
                except Exception:
                    continue
        except Exception as e:
            log.error(f"Kripto tarama hatasi: {e}")

        # 2. BIST (Tum BIST hisseleri) - Only on trading days
        if self.is_market_open("BIST"):
            try:
                bist_syms = get_all_bist_symbols()
                log.info(f"BIST hisse sayısı: {len(bist_syms)}")
                for sym in bist_syms:
                    try:
                        df = self.bot.data_feed.fetch_bist_data(sym)
                        if df is not None and len(df) >= 60:
                            self.check_stop_loss_and_signals("BIST", sym, df)
                    except Exception:
                        continue
            except Exception as e:
                log.error(f"BIST tarama hatasi: {e}")
        else:
            log.info("BIST: Hafta sonu - tarama atlandı (sadece Kripto 7/24)")

        # Rank candidates by score and execute top ones
        self.candidates.sort(key=lambda x: x["score"], reverse=True)

        log.info(f"Toplam {len(self.candidates)} aday bulundu, skorlara göre sıralanıyor...")
        for c in self.candidates[:10]:
            log.info(f"  {c['symbol']}: Skor={c['score']} | Tip={c['signal_type']} | RSI={c['rsi']:.1f} | RVOL={c['rvol']:.2f} | TrendYaş={c['trend_age']} | ATR={c['atr']:.4f}")

        # Execute buys for top candidates up to available slots per market
        for market in ["CRYPTO", "BIST"]:
            market_candidates = [c for c in self.candidates if c["market"] == market]
            open_cnt = len(self.bot.portfolio.get_open_positions(market))
            max_pos = self.markets_cfg.get(market, {}).get("max_positions", 3)
            available_slots = max_pos - open_cnt

            for c in market_candidates[:available_slots]:
                trade = self.bot.portfolio.execute_buy(market, c["symbol"], c["price"], c["qty"])
                if trade:
                    self.send_tg(
                        f"🟢 <b>ALIM [{market}] {c['symbol']}</b>\n"
                        f"Fiyat: {fmt_price(c['price'])} | Adet: {c['qty']}\n"
                        f"Sinyal: {c['signal_type']} | Skor: {c['score']}/100\n"
                        f"RSI: {c['rsi']:.1f} | RVOL: {c['rvol']:.2f} | TrendYaş: {c['trend_age']}\n"
                        f"Kalan Kasa: {self.bot.portfolio.get_balance(market):,.2f}"
                    )

        self.save_radar(self.radar_rows[:15])
        log.info(f"=== TARAMA TAMAMLANDI (Radarda {len(self.radar_rows)} aktif trend adayı, {len(self.candidates)} skorlanmış aday) ===")

    def run_forever(self):
        threading.Thread(target=self.telegram_listener, daemon=True).start()
        self.send_tg("🚀 <b>SALMA + TRF Canlı Bot Başlatıldı!</b>\nTüm BIST (maks 3) ve tüm Binance USDT (maks 10) taranıyor.\nYeni: Sinyal skorlaması, ATR stop, trailing stop, hafta sonu koruması.")
        while True:
            self.scan_once()
            now = datetime.now()
            wait_sec = (60 - now.minute) * 60 - now.second + 15
            log.info(f"Sonraki saat başı taramasına {wait_sec} saniye bekleniyor...")
            time.sleep(max(60, wait_sec))

    def telegram_listener(self):
        if not self.token:
            log.warning("Telegram token tanimli degil, komut dinleyici pasif.")
            return
        log.info("Telegram komut dinleyici aktif (/durum, /pozisyonlar, /tara)")
        while True:
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates?offset={self.last_update_id + 1}&timeout=20"
                res = json.loads(urllib.request.urlopen(url, timeout=25).read().decode("utf-8"))
                for upd in res.get("result", []):
                    self.last_update_id = upd["update_id"]
                    msg = upd.get("message", {})
                    txt = (msg.get("text") or "").strip().lower()
                    cid = str(msg.get("chat", {}).get("id", ""))
                    if not self.chat_id and cid:
                        self.chat_id = cid
                        self.cfg["telegram"]["fallback_chat_id"] = cid
                        json.dump(self.cfg, open("config.json", "w", encoding="utf-8"), indent=4)
                    if txt == "/durum":
                        self.send_tg(self.build_status_text())
                    elif txt == "/pozisyonlar":
                        self.send_tg(self.build_positions_text())
                    elif txt == "/tara":
                        self.send_tg("Manuel piyasa taraması başlatılıyor...")
                        threading.Thread(target=self.scan_once, daemon=True).start()
            except Exception:
                time.sleep(5)


if __name__ == "__main__":
    LiveEngine().run_forever()