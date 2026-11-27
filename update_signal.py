import json, sqlite3

# 1. live_runner.py guncelle: Son 2 kapanmis mumda taze sinyal + radar kaydi
lr = open("live_runner.py", encoding="utf-8").read()

old_block = """        latest = df.iloc[-1]
        price = float(latest["close"])"""

new_block = """        latest = df.iloc[-2]  # Son kapanmis 1 saatlik mum
        prev = df.iloc[-3]    # Bir onceki kapanmis mum
        price = float(df.iloc[-1]["close"])"""

lr = lr.replace(old_block, new_block)
lr = lr.replace(
    'if (latest["trf_long"] == 1) and latest["salma_red_to_green"]:',
    'fresh_signal = (latest["trf_long"] == 1) and (latest["salma_red_to_green"] or (prev["salma_red_to_green"] and latest["salma_color"] == "green"))\n            if fresh_signal:'
)
open("live_runner.py", "w", encoding="utf-8").write(lr)
print("live_runner.py guncellendi (Son kapanmis mum + 2 mum taze sinyal toleransi).")
