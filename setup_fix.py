import json, re, pathlib

cfg = json.load(open("config.json", encoding="utf-8"))
skip = {".venv", "venv", "node_modules", "__pycache__", ".git"}
files = [
    p for p in pathlib.Path.home().joinpath("Desktop").rglob("*")
    if p.is_file() and p.suffix in {".py", ".env", ".json"} and not any(s in p.parts for s in skip)
]
txt = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in files)
tok = re.search(r'(\d{8,11}:[A-Za-z0-9_-]{30,})', txt)
cid = re.search(r'(?:chat_id|CHAT_ID|ADMIN_ID)\s*[:=]\s*["\']?(-?\d{6,15})["\']?', txt)
if tok: cfg["telegram"]["fallback_token"] = tok.group(1)
if cid: cfg["telegram"]["fallback_chat_id"] = cid.group(1)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), indent=4)

d = open("dashboard.py", encoding="utf-8", errors="ignore").read()
fixes = {
    "Canl? Kontrol": "Canlı Kontrol", "?lem": "şlem", "A??k": "Açık", "A??K": "AÇIK",
    "Giri? Fiyat?": "Giriş Fiyatı", "Giri? Zaman?": "Giriş Zamanı", "Hen?z a??k": "Henüz açık",
    "ba??": "başı", "Hen?z": "Henüz", "i?lem kayd?": "işlem kaydı", "K?r/Zarar": "Kâr/Zarar"
}
for k, v in fixes.items():
    d = d.replace(k, v)
open("dashboard.py", "w", encoding="utf-8").write(d)
print("Tamamlandi | Telegram Token:", bool(tok), "| ChatID:", bool(cid))
