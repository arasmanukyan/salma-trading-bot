import argparse
import pandas as pd
from lightweight_charts import Chart
from data_feed import DataFeed
from indicators import calculate_indicators

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='TCELL.IS')
    args = parser.parse_args()
    feed = DataFeed()
    df = feed.fetch_ohlcv(args.symbol, interval='1h')
    df = calculate_indicators(df)
    chart = Chart(toolbox=True)
    chart.set(df[['time', 'open', 'high', 'low', 'close', 'volume']])
    salma_line = chart.create_line('SALMA', color='#00ff88', width=2)
    salma_line.set(df[['time', 'salma']].rename(columns={'salma': 'SALMA'}).dropna())
    chart.show(block=True)

if __name__ == '__main__':
    main()
