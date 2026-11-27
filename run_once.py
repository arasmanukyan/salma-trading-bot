import os, json
from live_runner import LiveRunner

cfg_path = "config.json" if os.path.exists("config.json") else "config.template.json"
cfg = json.load(open(cfg_path, encoding="utf-8"))
if os.getenv("TELEGRAM_TOKEN"):
    cfg.setdefault("telegram", {})["bot_token"] = os.getenv("TELEGRAM_TOKEN")
if os.getenv("TELEGRAM_CHAT_ID"):
    cfg.setdefault("telegram", {})["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
json.dump(cfg, open("config.json", "w", encoding="utf-8"), indent=2)

runner = LiveRunner()
runner.scan_once()
print("GitHub Actions tekil tarama tamamlandi.")
