from flask import Flask, render_template_string
import sqlite3

from formatting import fmt_price

app = Flask(__name__)
app.add_template_filter(fmt_price, "fmt_price")

HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>SALMA + TRF Canli Bot Paneli</title>
<meta http-equiv="refresh" content="15">
<style>
body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;margin:0;padding:24px}
h1{color:#ff7b29;margin-bottom:4px} .sub{color:#8b949e;margin-bottom:24px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}
.card h3{margin:0 0 8px 0;color:#8b949e;font-size:13px;text-transform:uppercase}
.val{font-size:26px;font-weight:700;color:#fff}
.pos{color:#3fb950} .neg{color:#f85149}
table{width:100%;border-collapse:collapse;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;margin-bottom:24px}
th,td{padding:12px 14px;text-align:left;border-bottom:1px solid #21262d;font-size:14px}
th{background:#1f242c;color:#8b949e}
</style></head><body>
<h1>SALMA v3.0 + Twin Range Filter - Canlı Kontrol Paneli</h1>
<div class="sub">BIST Limit: Maks 3 şlem (100.000 TL) | Kripto Limit: Maks 10 şlem (1.000 $) | Otomatik Yenileme: 15 sn</div>
<div class="grid">
  <div class="card"><h3>BIST Kasa (TRY)</h3><div class="val">{{ "%.2f"|format(bist_bal) }} TL</div></div>
  <div class="card"><h3>Kripto Kasa (USDT)</h3><div class="val">{{ "%.2f"|format(crypto_bal) }} $</div></div>
  <div class="card"><h3>BIST Açık Pozisyon</h3><div class="val">{{ bist_pos }} / 3</div></div>
  <div class="card"><h3>Kripto Açık Pozisyon</h3><div class="val">{{ crypto_pos }} / 10</div></div>
</div>
<h2>Açık Pozisyonlar</h2>
<table><tr><th>Piyasa</th><th>Sembol</th><th>Miktar</th><th>Giriş Fiyatı</th><th>Güncel Fiyat</th><th>Anlık Kâr/Zarar</th><th>Giriş Zamanı</th></tr>
{% for p in positions %}
{% set cls = 'pos' if p.pnl_amount >= 0 else 'neg' %}
{% set sign = '+' if p.pnl_amount >= 0 else '-' %}
<tr><td>{{ p.market }}</td><td><b>{{ p.symbol }}</b></td><td>{{ "%.4f"|format(p.quantity) }}</td><td>{{ p.entry_price|fmt_price }}</td><td>{{ p.current_price|fmt_price }}</td><td class="{{ cls }}">{{ sign }}{{ "%.2f"|format(p.pnl_amount|abs) }} {{ p.currency }} ({{ sign }}%{{ "%.2f"|format(p.pnl_pct|abs) }})</td><td>{{ p.entry_time }}</td></tr>
{% else %}<tr><td colspan="7" style="color:#8b949e">Henüz açık pozisyon yok. Tarama devam ediyor...</td></tr>{% endfor %}
</table>
<h2>Son Kapanan şlemler</h2>
<table><tr><th>Piyasa</th><th>Sembol</th><th>şlem</th><th>Fiyat</th><th>Kâr/Zarar</th><th>Zaman</th></tr>
{% for t in trades %}
<tr><td>{{ t.market }}</td><td><b>{{ t.symbol }}</b></td><td>{{ t.side }}</td><td>{{ t.price|fmt_price }}</td>
<td class="{{ 'pos' if (t.pnl or 0) >= 0 else 'neg' }}">{{ "%.2f"|format(t.pnl or 0) }}</td><td>{{ t.timestamp }}</td></tr>
{% else %}<tr><td colspan="6" style="color:#8b949e">Henüz tamamlanan işlem kaydı yok.</td></tr>{% endfor %}
</table><h2>Canlı Trend Radarı (TRF Long + SALMA Yeşil Adaylar)</h2>
<table><tr><th>Piyasa</th><th>Sembol</th><th>Son Fiyat</th><th>SALMA</th><th>TRF</th><th>Tarama Saati</th></tr>
{% for r in radar %}
<tr><td>{{ r[0] }}</td><td><b>{{ r[1] }}</b></td><td>{{ r[2]|fmt_price }}</td><td class="pos">{{ r[3] }}</td><td class="pos">LONG</td><td>{{ r[5] }}</td></tr>
{% else %}<tr><td colspan="6" style="color:#8b949e">Bir sonraki taramada aktif yükseliş trendindeki hisseler/coinler burada listelenecek.</td></tr>{% endfor %}
</table></body></html>"""

_feed = None


def _get_feed():
    """Lazily create a single DataFeed instance (KuCoin/yfinance). Returns None on failure."""
    global _feed
    if _feed is not None:
        return _feed or None
    try:
        import json as _json
        from data_feed import DataFeed
        with open("config.json", encoding="utf-8") as f:
            cfg = _json.load(f)
        _feed = DataFeed(cfg)
    except Exception:
        _feed = False
    return _feed or None


def _build_positions(rows, currencies):
    """Enrich open positions with live price and unrealized PnL.

    Falls back to the entry price if a live quote cannot be fetched.
    """
    feed = _get_feed()
    result = []
    for p in rows:
        entry = float(p["entry_price"] or 0)
        qty = float(p["quantity"] or 0)

        current = None
        if feed is not None:
            try:
                current = feed.get_latest_price(p["market"], p["symbol"])
            except Exception:
                current = None
        if current is None or current <= 0:
            current = entry

        if entry > 0:
            pnl_amount = (current - entry) * qty
            pnl_pct = ((current - entry) / entry) * 100
        else:
            pnl_amount = 0.0
            pnl_pct = 0.0

        result.append({
            "market": p["market"],
            "symbol": p["symbol"],
            "quantity": qty,
            "entry_price": entry,
            "entry_time": p["entry_time"],
            "current_price": current,
            "pnl_amount": pnl_amount,
            "pnl_pct": pnl_pct,
            "currency": currencies.get(p["market"], ""),
        })
    return result


@app.route("/")
def index():
    con = sqlite3.connect("trading_bot.db")
    con.row_factory = sqlite3.Row  # Enable column name access
    cur = con.cursor()
    bal_rows = cur.execute("SELECT market, currency, current_balance FROM balances").fetchall()
    bals = {r["market"]: r["current_balance"] for r in bal_rows}
    currencies = {r["market"]: r["currency"] for r in bal_rows}
    raw_positions = cur.execute("SELECT * FROM positions").fetchall()
    trades = cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 30").fetchall()
    radar = cur.execute("SELECT * FROM radar").fetchall()
    con.close()

    positions = _build_positions(raw_positions, currencies)
    bist_pos = sum(1 for p in positions if p["market"] == "BIST")
    crypto_pos = sum(1 for p in positions if p["market"] == "CRYPTO")
    return render_template_string(HTML, bist_bal=bals.get("BIST", 100000.0), crypto_bal=bals.get("CRYPTO", 1000.0), bist_pos=bist_pos, crypto_pos=crypto_pos, positions=positions, trades=trades, radar=radar)

if __name__ == "__main__":
    import os; app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

