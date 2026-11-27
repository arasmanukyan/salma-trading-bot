import os, json
from datetime import datetime, timezone, timedelta
from live_runner import LiveEngine

cfg_path = "config.json" if os.path.exists("config.json") else "config.template.json"
cfg = json.load(open(cfg_path, encoding="utf-8"))
if os.getenv("TELEGRAM_TOKEN"):
    cfg.setdefault("telegram", {})["fallback_token"] = os.getenv("TELEGRAM_TOKEN")
if os.getenv("TELEGRAM_CHAT_ID"):
    cfg.setdefault("telegram", {})["fallback_chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
json.dump(cfg, open("config.json", "w", encoding="utf-8"), indent=2)

runner = LiveEngine()
runner.scan_once()

saat = datetime.now(timezone(timedelta(hours=3))).strftime("%H:%M")
radar = getattr(runner, "radar_rows", [])

satirlar = [f"📊 <b>SALMA Saatlik Tarama Özeti ({saat})</b>\n"]

if radar:
    satirlar.append(f"🔎 <b>Alım Fırsatları / Trend Radarı ({len(radar)} Aday):</b>")
    for market, sym, price, salma, trf, t in radar[:15]:
        satirlar.append(f"• [{market}] <b>{sym}</b> — Fiyat: <code>{price:.4f}</code>")
else:
    satirlar.append("🔎 <b>Alım Fırsatı:</b> Şu an kriterlere uyan aday yok.")

pm = runner.bot.portfolio
bist_pos = pm.get_open_positions("BIST")
cry_pos = pm.get_open_positions("CRYPTO")
satirlar.append(f"\n💼 <b>Açık Pozisyonlar:</b> BIST {len(bist_pos)}/3 | Kripto {len(cry_pos)}/10")

for p in (bist_pos + cry_pos):
    satirlar.append(f"  └ [{p.market}] <b>{p.symbol}</b> (Giriş: {p.entry_price:.4f})")

runner.send_tg("\n".join(satirlar))
print("GitHub Actions tekil tarama ve Telegram raporu tamamlandi.")
