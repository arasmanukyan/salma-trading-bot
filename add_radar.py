import sqlite3

# 1. Veritabanina radar tablosu ekle
con = sqlite3.connect("trading_bot.db")
con.execute("CREATE TABLE IF NOT EXISTS radar (market TEXT, symbol TEXT, price REAL, salma_color TEXT, trf_long INTEGER, updated_at TEXT)")
con.commit()
con.close()

# 2. live_runner.py icine radar kayit mantigi ekle
lr = open("live_runner.py", encoding="utf-8").read()
if "INSERT INTO radar" not in lr:
    old_fn = "    def scan_once(self):"
    new_fn = """    def save_radar(self, rows):
        import sqlite3
        con = sqlite3.connect("trading_bot.db")
        con.execute("DELETE FROM radar")
        con.executemany("INSERT INTO radar VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def scan_once(self):
        self.radar_rows = []"""
    lr = lr.replace(old_fn, new_fn)
    lr = lr.replace(
        'price = float(df.iloc[-1]["close"])',
        'price = float(df.iloc[-1]["close"])\n        if int(latest["trf_long"]) == 1 and latest["salma_color"] == "green":\n            self.radar_rows.append((market, symbol, price, "YEŞL", 1, datetime.now().strftime("%H:%M:%S")))'
    )
    lr = lr.replace(
        'log.info("=== TARAMA TAMAMLANDI ===")',
        'self.save_radar(self.radar_rows[:15])\n        log.info(f"=== TARAMA TAMAMLANDI (Radarda {len(self.radar_rows)} aktif trend adayı var) ===")'
    )
    open("live_runner.py", "w", encoding="utf-8").write(lr)

# 3. dashboard.py icine radar tablosunu ekle
db = open("dashboard.py", encoding="utf-8").read()
if "Canlı Sinyal Radarı" not in db:
    db = db.replace(
        "</body></html>",
        """<h2>Canlı Trend Radarı (TRF Long + SALMA Yeşil Adaylar)</h2>
<table><tr><th>Piyasa</th><th>Sembol</th><th>Son Fiyat</th><th>SALMA</th><th>TRF</th><th>Tarama Saati</th></tr>
{% for r in radar %}
<tr><td>{{ r[0] }}</td><td><b>{{ r[1] }}</b></td><td>{{ "%.4f"|format(r[2]) }}</td><td class="pos">{{ r[3] }}</td><td class="pos">LONG</td><td>{{ r[5] }}</td></tr>
{% else %}<tr><td colspan="6" style="color:#8b949e">Bir sonraki taramada aktif yükseliş trendindeki hisseler/coinler burada listelenecek.</td></tr>{% endfor %}
</table></body></html>"""
    )
    db = db.replace(
        'trades = cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 30").fetchall()',
        'trades = cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 30").fetchall()\n    radar = cur.execute("SELECT * FROM radar").fetchall()'
    )
    db = db.replace(
        "trades=trades)",
        "trades=trades, radar=radar)"
    )
    open("dashboard.py", "w", encoding="utf-8").write(db)

print("Radar guncellemesi tamamlandi!")
