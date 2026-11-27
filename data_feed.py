"""
Data Feed Module
Handles fetching market data from BIST (yfinance) and Crypto (ccxt/Binance).
"""

import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import pytz
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataFeed:
    """Unified data feed for BIST and Crypto markets."""

    def __init__(self, config: dict):
        self.config = config
        self.timezone = pytz.timezone(config.get('general', {}).get('timezone', 'Europe/Istanbul'))
        self.lookback_days = config.get('general', {}).get('data_lookback_days', 60)
        self.timeframe = config.get('general', {}).get('timeframe', '1h')

        # Initialize Binance for crypto
        self.binance = ccxt.kucoin({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot'
            }
        })

    def _get_lookback_start(self) -> datetime:
        """Calculate start date for data fetching."""
        end = datetime.now(self.timezone)
        start = end - timedelta(days=self.lookback_days)
        return start

    def fetch_bist_data(self, symbol: str) -> pd.DataFrame:
        """
        Fetch BIST data from Yahoo Finance.

        Parameters:
        - symbol: BIST symbol with .IS suffix (e.g., "THYAO.IS")

        Returns:
        - DataFrame with OHLCV data
        """
        try:
            start = self._get_lookback_start()
            end = datetime.now(self.timezone)

            # Yahoo Finance expects UTC
            start_utc = start.astimezone(pytz.UTC)
            end_utc = end.astimezone(pytz.UTC)

            logger.info(f"Fetching BIST data for {symbol} from {start_utc} to {end_utc}")

            ticker = yf.Ticker(symbol)
            data = ticker.history(
                start=start_utc,
                end=end_utc,
                interval=self.timeframe,
                auto_adjust=True,
                actions=False
            )

            if data.empty:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # Standardize column names
            data.columns = [c.lower() for c in data.columns]
            data = data[['open', 'high', 'low', 'close', 'volume']]
            data = data.dropna()

            # Ensure timezone awareness
            if data.index.tz is None:
                data.index = data.index.tz_localize('UTC').tz_convert(self.timezone)
            else:
                data.index = data.index.tz_convert(self.timezone)

            logger.info(f"Fetched {len(data)} candles for {symbol}")
            return data

        except Exception as e:
            logger.error(f"Error fetching BIST data for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_crypto_data(self, symbol: str) -> pd.DataFrame:
        """
        Fetch Crypto data from Binance via CCXT.

        Parameters:
        - symbol: Trading pair (e.g., "BTC/USDT")

        Returns:
        - DataFrame with OHLCV data
        """
        try:
            # Convert timeframe to CCXT format
            tf_map = {'1h': '1h', '4h': '4h', '1d': '1d', '15m': '15m', '5m': '5m'}
            ccxt_tf = tf_map.get(self.timeframe, '1h')

            # Calculate since timestamp (milliseconds)
            since = int((datetime.now(self.timezone) - timedelta(days=self.lookback_days)).timestamp() * 1000)

            logger.info(f"Fetching Crypto data for {symbol} since {since}")

            all_ohlcv = []
            limit = 1000  # Binance max limit per request

            while True:
                ohlcv = self.binance.fetch_ohlcv(symbol, ccxt_tf, since=since, limit=limit)
                if not ohlcv:
                    break

                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + 1

                # Rate limiting
                time.sleep(self.binance.rateLimit / 1000)

                # Break if we got less than limit (end of data)
                if len(ohlcv) < limit:
                    break

            if not all_ohlcv:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            # Create DataFrame
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df = df.set_index('timestamp')
            df = df.tz_convert(self.timezone)

            # Remove duplicates and sort
            df = df[~df.index.duplicated(keep='last')].sort_index()

            logger.info(f"Fetched {len(df)} candles for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error fetching Crypto data for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_market_data(self, market: str, symbol: str) -> pd.DataFrame:
        """
        Fetch data for a given market and symbol.

        Parameters:
        - market: 'BIST' or 'CRYPTO'
        - symbol: Trading symbol

        Returns:
        - DataFrame with OHLCV data
        """
        if market.upper() == 'BIST':
            return self.fetch_bist_data(symbol)
        elif market.upper() == 'CRYPTO':
            return self.fetch_crypto_data(symbol)
        else:
            logger.error(f"Unknown market: {market}")
            return pd.DataFrame()

    def fetch_all_symbols(self, market: str) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for all configured symbols in a market.

        Parameters:
        - market: 'BIST' or 'CRYPTO'

        Returns:
        - Dictionary of symbol -> DataFrame
        """
        symbols = self.config.get('markets', {}).get(market.upper(), {}).get('symbols', [])
        results = {}

        for symbol in symbols:
            logger.info(f"Fetching {market} data for {symbol}...")
            df = self.fetch_market_data(market, symbol)
            if not df.empty:
                results[symbol] = df
            time.sleep(0.5)  # Be nice to APIs

        return results

    def get_latest_price(self, market: str, symbol: str) -> Optional[float]:
        """Get the latest price for a symbol."""
        try:
            if market.upper() == 'BIST':
                ticker = yf.Ticker(symbol)
                data = ticker.history(period='1d', interval='1m')
                if not data.empty:
                    return float(data['Close'].iloc[-1])
            elif market.upper() == 'CRYPTO':
                ticker = self.binance.fetch_ticker(symbol)
                return float(ticker['last'])
        except Exception as e:
            logger.error(f"Error getting latest price for {symbol}: {e}")
        return None


if __name__ == "__main__":
    # Test data feed
    import json

    with open('config.json', 'r') as f:
        config = json.load(f)

    feed = DataFeed(config)

    # Test BIST
    print("Testing BIST data fetch...")
    bist_data = feed.fetch_bist_data("THYAO.IS")
    print(f"THYAO.IS: {len(bist_data)} candles")
    if not bist_data.empty:
        print(bist_data.tail(3))

    # Test Crypto
    print("\nTesting Crypto data fetch...")
    crypto_data = feed.fetch_crypto_data("BTC/USDT")
    print(f"BTC/USDT: {len(crypto_data)} candles")
    if not crypto_data.empty:
        print(crypto_data.tail(3))
