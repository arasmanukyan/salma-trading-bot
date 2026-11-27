import time, threading, logging, json, urllib.request, urllib.parse
from datetime import datetime
import ccxt, yfinance as yf, pandas as pd
from bot import TradingBot
from indicators import calculate_all_indicators

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
    ex = ccxt.binance({"enableRateLimit": True})
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
            lines.append(f"- [{p.market}] <b>{p.symbol}</b> | Adet: {p.quantity:.4f} | Giriş: {p.entry_price:.4f}")
        return "\n".join(lines)

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

    def check_stop_loss_and_signals(self, market, symbol, df, max_pos, pos_pct):
        pm = self.bot.portfolio
        df = calculate_all_indicators(df, self.bot.salma_params, self.bot.trf_params)
        latest = df.iloc[-2]  # Son kapanmis 1 saatlik mum
        prev = df.iloc[-3]    # Bir onceki kapanmis mum
        price = float(df.iloc[-1]["close"])
        if int(latest["trf_long"]) == 1 and int(latest["salma_color"]) == 1:
            self.radar_rows.append((market, symbol, price, "YEŞL", 1, datetime.now().strftime("%H:%M:%S")))
        sl_pct = self.cfg.get("strategy", {}).get("stop_loss_pct", 0.05)

        if pm.has_position(market, symbol):
            pos = pm.get_position(market, symbol)
            pnl_ratio = (price - pos.entry_price) / pos.entry_price
            if latest["salma_green_to_red"] or pnl_ratio <= -sl_pct:
                reason = "STOP-LOSS (-5%)" if pnl_ratio <= -sl_pct else "SALMA Yeşil->Kırmızı"
                trade = pm.execute_sell(market, symbol, price)
                if trade:
                    self.send_tg(f"🔴 <b>SATIŞ [{market}] {symbol}</b>\nFiyat: {price:.4f}\nSebep: {reason}")
        else:
            open_cnt = len(pm.get_open_positions(market))
            if open_cnt >= max_pos:
                return
            fresh_signal = (latest["trf_long"] == 1) and (latest["salma_red_to_green"] or (prev["salma_red_to_green"] and int(latest["salma_color"]) == 1))
            if fresh_signal:
                bal = pm.get_balance(market)
                qty = (bal * pos_pct) / price
                if market == "BIST":
                    qty = int(qty)
                if qty > 0:
                    trade = pm.execute_buy(market, symbol, price, qty)
                    if trade:
                        self.send_tg(f"🟢 <b>ALIM [{market}] {symbol}</b>\nFiyat: {price:.4f} | Adet: {qty}\nKalan Kasa: {pm.get_balance(market):,.2f}")

    def save_radar(self, rows):
        import sqlite3
        con = sqlite3.connect("trading_bot.db")
        con.execute("DELETE FROM radar")
        con.executemany("INSERT INTO radar VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def scan_once(self):
        self.radar_rows = []
        log.info("=== TAM PYASA TARAMASI BAŞLADI ===")
        # 1. CRYPTO (7/24 Tum Binance USDT)
        try:
            cry_syms = get_all_binance_usdt_symbols(min_vol_usdt=1000000)
            log.info(f"Binance aktif USDT parite sayısı: {len(cry_syms)}")
            for sym in cry_syms:
                try:
                    df = self.bot.data_feed.fetch_crypto_data(sym)
                    if df is not None and len(df) >= 60:
                        self.check_stop_loss_and_signals("CRYPTO", sym, df, max_pos=10, pos_pct=0.10)
                except Exception:
                    continue
        except Exception as e:
            log.error(f"Kripto tarama hatasi: {e}")

        # 2. BIST (Tum BIST hisseleri)
        try:
            bist_syms = get_all_bist_symbols()
            log.info(f"BIST hisse sayısı: {len(bist_syms)}")
            for sym in bist_syms:
                try:
                    df = self.bot.data_feed.fetch_bist_data(sym)
                    if df is not None and len(df) >= 60:
                        self.check_stop_loss_and_signals("BIST", sym, df, max_pos=3, pos_pct=0.25)
                except Exception:
                    continue
        except Exception as e:
            log.error(f"BIST tarama hatasi: {e}")
        self.save_radar(self.radar_rows[:15])
        log.info(f"=== TARAMA TAMAMLANDI (Radarda {len(self.radar_rows)} aktif trend adayı var) ===")

    def run_forever(self):
        threading.Thread(target=self.telegram_listener, daemon=True).start()
        self.send_tg("🚀 <b>SALMA + TRF Canlı Bot Başlatıldı!</b>\nTüm BIST (maks 3) ve tüm Binance USDT (maks 10) taranıyor.")
        while True:
            self.scan_once()
            now = datetime.now()
            wait_sec = (60 - now.minute) * 60 - now.second + 15
            log.info(f"Sonraki saat başı taramasına {wait_sec} saniye bekleniyor...")
            time.sleep(max(60, wait_sec))

if __name__ == "__main__":
    LiveEngine().run_forever()
