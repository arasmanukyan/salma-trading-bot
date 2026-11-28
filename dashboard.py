import os
import re
import json
import sqlite3
import logging
import subprocess

from flask import Flask, render_template_string, request, redirect, url_for

from formatting import fmt_price

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DASHBOARD")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SALMA_DB_PATH", os.path.join(BASE_DIR, "trading_bot.db"))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__)
app.add_template_filter(fmt_price, "fmt_price")

HTML = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SALMA + TRF Canli Bot Paneli</title>
<meta http-equiv="refresh" content="30">
<style>
*{box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;padding:24px}
h1{color:#ff7b29;margin:0 0 4px;font-size:clamp(18px,2.4vw,26px)}
h2{font-size:clamp(15px,2vw,20px)}
.sub{color:#8b949e;margin-bottom:24px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}
.card h3{margin:0 0 8px 0;color:#8b949e;font-size:13px;text-transform:uppercase;letter-spacing:.02em}
.val{font-size:26px;font-weight:700;color:#fff;word-break:break-word}
.pos{color:#3fb950} .neg{color:#f85149}
table{width:100%;border-collapse:collapse;background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;margin-bottom:24px}
th,td{padding:12px 14px;text-align:left;border-bottom:1px solid #21262d;font-size:14px;vertical-align:middle}
th{background:#1f242c;color:#8b949e;white-space:nowrap}
.btn-close{background:#da3633;color:#fff;border:0;border-radius:6px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.btn-close:hover{background:#f85149}
.lbl-short{display:none}
form.inline{margin:0}
@media (max-width:768px){
  body{padding:12px}
  h1{font-size:18px}
  .sub{font-size:12px;margin-bottom:16px}
  .grid{grid-template-columns:repeat(2,1fr);gap:10px}
  .card{padding:12px}
  .card h3{font-size:11px}
  .val{font-size:18px}
  table.responsive{background:transparent;border:0}
  table.responsive thead{display:none}
  table.responsive, table.responsive tbody, table.responsive tr, table.responsive td{display:block;width:100%}
  table.responsive tr{background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:12px;padding:6px 10px}
  table.responsive td{border:0;padding:8px 4px;display:flex;justify-content:space-between;align-items:center;gap:12px;text-align:right}
  table.responsive td::before{content:attr(data-label);color:#8b949e;font-weight:600;text-align:left;flex:0 0 auto}
  table.responsive td.td-action{display:block;padding:6px 4px 4px}
  table.responsive td.td-action::before{content:"";display:none}
  table.responsive td.td-empty{display:block;text-align:center}
  table.responsive td.td-empty::before{content:"";display:none}
  .btn-close{width:100%;min-height:42px;font-size:16px}
  .lbl-full{display:none}
  .lbl-short{display:inline}
}
</style></head><body>
<h1>SALMA v3.0 + Twin Range Filter - Canlı Kontrol Paneli</h1>
<div class="sub">BIST Limit: Maks 3 şlem (100.000 TL) | Kripto Limit: Maks 10 şlem (1.000 $) | Otomatik Yenileme: 30 sn</div>
<div class="grid">
  <div class="card"><h3>BIST Kasa (TRY)</h3><div class="val">{{ "%.2f"|format(bist_bal) }} TL</div></div>
  <div class="card"><h3>Kripto Kasa (USDT)</h3><div class="val">{{ "%.2f"|format(crypto_bal) }} $</div></div>
  <div class="card"><h3>BIST Açık Pozisyon</h3><div class="val">{{ bist_pos }} / 3</div></div>
  <div class="card"><h3>Kripto Açık Pozisyon</h3><div class="val">{{ crypto_pos }} / 10</div></div>
</div>
<h2>Açık Pozisyonlar</h2>
<table class="responsive">
<thead><tr><th>Piyasa</th><th>Sembol</th><th>Miktar</th><th>Giriş Fiyatı</th><th>Güncel Fiyat</th><th>Anlık Kâr/Zarar</th><th>Giriş Zamanı</th><th>İşlem</th></tr></thead>
<tbody>
{% for p in positions %}
{% set cls = 'pos' if p.pnl_amount >= 0 else 'neg' %}
{% set sign = '+' if p.pnl_amount >= 0 else '-' %}
<tr>
  <td data-label="Piyasa">{{ p.market }}</td>
  <td data-label="Sembol"><b>{{ p.symbol }}</b></td>
  <td data-label="Miktar">{{ "%.4f"|format(p.quantity) }}</td>
  <td data-label="Giriş Fiyatı">{{ p.entry_price|fmt_price }}</td>
  <td data-label="Güncel Fiyat">{{ p.current_price|fmt_price }}</td>
  <td data-label="Anlık Kâr/Zarar" class="{{ cls }}">{{ sign }}{{ "%.2f"|format(p.pnl_amount|abs) }} {{ p.currency }} ({{ sign }}%{{ "%.2f"|format(p.pnl_pct|abs) }})</td>
  <td data-label="Giriş Zamanı">{{ p.entry_time }}</td>
  <td data-label="İşlem" class="td-action">
    <form class="inline" method="post" action="{{ url_for('close_position') }}" onsubmit="return confirm('{{ p.symbol }} pozisyonunu kapatmak istediginize emin misiniz?');">
      <input type="hidden" name="market" value="{{ p.market }}">
      <input type="hidden" name="symbol" value="{{ p.symbol }}">
      <button type="submit" class="btn-close"><span class="lbl-full">Pozisyonu Kapat</span><span class="lbl-short">Kapat</span></button>
    </form>
  </td>
</tr>
{% else %}<tr><td class="td-empty" colspan="8" style="color:#8b949e">Henüz açık pozisyon yok. Tarama devam ediyor...</td></tr>{% endfor %}
</tbody>
</table>
<h2>Son Kapanan şlemler</h2>
<table class="responsive">
<thead><tr><th>Piyasa</th><th>Sembol</th><th>şlem</th><th>Fiyat</th><th>Kâr/Zarar</th><th>Zaman</th></tr></thead>
<tbody>
{% for t in trades %}
<tr>
  <td data-label="Piyasa">{{ t.market }}</td>
  <td data-label="Sembol"><b>{{ t.symbol }}</b></td>
  <td data-label="şlem">{{ t.side }}</td>
  <td data-label="Fiyat">{{ t.price|fmt_price }}</td>
  <td data-label="Kâr/Zarar" class="{{ 'pos' if (t.pnl or 0) >= 0 else 'neg' }}">{{ "%.2f"|format(t.pnl or 0) }}</td>
  <td data-label="Zaman">{{ t.timestamp }}</td>
</tr>
{% else %}<tr><td class="td-empty" colspan="6" style="color:#8b949e">Henüz tamamlanan işlem kaydı yok.</td></tr>{% endfor %}
</tbody>
</table>
<h2>Canlı Trend Radarı (TRF Long + SALMA Yeşil Adaylar)</h2>
<table class="responsive">
<thead><tr><th>Piyasa</th><th>Sembol</th><th>Son Fiyat</th><th>SALMA</th><th>TRF</th><th>Tarama Saati</th></tr></thead>
<tbody>
{% for r in radar %}
<tr>
  <td data-label="Piyasa">{{ r[0] }}</td>
  <td data-label="Sembol"><b>{{ r[1] }}</b></td>
  <td data-label="Son Fiyat">{{ r[2]|fmt_price }}</td>
  <td data-label="SALMA" class="pos">{{ r[3] }}</td>
  <td data-label="TRF" class="pos">LONG</td>
  <td data-label="Tarama Saati">{{ r[5] }}</td>
</tr>
{% else %}<tr><td class="td-empty" colspan="6" style="color:#8b949e">Bir sonraki taramada aktif yükseliş trendindeki hisseler/coinler burada listelenecek.</td></tr>{% endfor %}
</tbody>
</table>
</body></html>"""


_feed = None


def _load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_feed():
    """Lazily create a single DataFeed instance (Binance / KuCoin / yfinance). Returns None on failure."""
    global _feed
    if _feed is not None:
        return _feed or None
    try:
        from data_feed import DataFeed
        _feed = DataFeed(_load_config())
    except Exception as e:
        logger.warning(f"DataFeed baslatilamadi: {e}")
        _feed = False
    return _feed or None


def _get_portfolio():
    """Create a PortfolioManager bound to this dashboard's DB file."""
    from portfolio import PortfolioManager
    cfg = _load_config()
    cfg.setdefault("database", {})["path"] = DB_PATH
    return PortfolioManager(cfg), cfg


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


def _send_manual_sell_tg(cfg, trade, price):
    """Send a dedicated 🔴 MANUEL SATIŞ notification to Telegram."""
    tg = cfg.get("telegram", {})
    token = tg.get("fallback_token")
    chat = tg.get("fallback_chat_id")
    if not token or not chat:
        return
    currency = {"BIST": "TRY", "CRYPTO": "USDT"}.get(trade.market, "")
    text = (
        f"🔴 <b>MANUEL SATIŞ</b>\n"
        f"📊 <b>Sembol:</b> {trade.symbol}\n"
        f"📦 <b>Miktar:</b> {trade.quantity:.6f}\n"
        f"💵 <b>Fiyat:</b> {fmt_price(price)} {currency}\n"
        f"💰 <b>Kâr/Zarar:</b> {trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)\n"
        f"💼 <b>Güncel Bakiye:</b> {trade.balance_after:.2f} {currency}"
    )
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram manuel satis bildirimi gonderilemedi: {e}")


def _persist_db():
    """Push the updated trading_bot.db to GitHub main if GITHUB_TOKEN is set.

    Locally (no token) the SQLite write is already on disk, so nothing else is needed.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return False
    try:
        def _run(args, env=None):
            return subprocess.run(args, capture_output=True, text=True, timeout=90,
                                  cwd=BASE_DIR, env=env)

        remote = (_run(["git", "remote", "get-url", "origin"]).stdout or "").strip()
        m = re.search(r"github\.com[:/](.+?/.+?)(?:\.git)?$", remote)
        if not m:
            logger.warning("GITHUB_TOKEN var ama origin GitHub URL'i bulunamadi.")
            return False
        slug = m.group(1)
        if slug.endswith(".git"):
            slug = slug[:-4]
        auth_url = f"https://x-access-token:{token}@github.com/{slug}.git"

        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": "salma-bot",
            "GIT_AUTHOR_EMAIL": "bot@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "salma-bot",
            "GIT_COMMITTER_EMAIL": "bot@users.noreply.github.com",
        })
        _run(["git", "add", "trading_bot.db"], env)
        _run(["git", "commit", "-m", "Manuel pozisyon kapatma - trading_bot.db guncellendi"], env)
        push = _run(["git", "push", auth_url, "HEAD:main"], env)
        if push.returncode != 0:
            # Remote likely advanced (hourly scanner). Best-effort rebase + retry.
            _run(["git", "fetch", auth_url, "main"], env)
            _run(["git", "rebase", "FETCH_HEAD"], env)
            push = _run(["git", "push", auth_url, "HEAD:main"], env)
        if push.returncode == 0:
            logger.info("trading_bot.db GitHub main'e gonderildi.")
            return True
        logger.warning(f"DB push basarisiz: {push.stderr.strip()[:300]}")
    except Exception as e:
        logger.warning(f"DB persist hatasi: {e}")
    return False


@app.route("/")
def index():
    con = sqlite3.connect(DB_PATH)
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


@app.route("/close_position", methods=["POST"])
def close_position():
    market = (request.form.get("market") or "").strip().upper()
    symbol = (request.form.get("symbol") or "").strip()
    if not market or not symbol:
        return redirect(url_for("index"), code=303)

    pm, cfg = _get_portfolio()
    if not pm.has_position(market, symbol):
        return redirect(url_for("index"), code=303)

    # Live market price (fallback to entry price on failure)
    price = None
    feed = _get_feed()
    if feed is not None:
        try:
            price = feed.get_latest_price(market, symbol)
        except Exception:
            price = None
    if price is None or price <= 0:
        pos = pm.get_position(market, symbol)
        price = float(pos.entry_price) if pos else None
    if price is None:
        return redirect(url_for("index"), code=303)

    # Suppress PortfolioManager's generic Telegram message; we send a dedicated one.
    saved_token = pm.telegram_token
    pm.telegram_token = None
    try:
        trade = pm.execute_sell(market, symbol, price)
    finally:
        pm.telegram_token = saved_token

    if trade:
        _send_manual_sell_tg(cfg, trade, price)
        _persist_db()

    return redirect(url_for("index"), code=303)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
