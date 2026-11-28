"""
Data Feed Module
Handles fetching market data from BIST (yfinance) and Crypto (ccxt/Binance Spot).

Crypto uses Binance Spot as the primary public data source. If the host IP is
region-restricted (HTTP 451), it automatically falls back to Binance's public
market-data mirror at https://data-api.binance.vision/api/v3. KuCoin is kept as
a last-resort fallback so that open positions whose pair is not listed on
Binance (e.g. STAY/USDT) keep being tracked for exits.
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

# Public Binance market-data mirror (works from region-restricted IPs).
DATA_API_VISION = "https://data-api.binance.vision/api/v3"

# Stable-coin base pairs to exclude from the tradable universe.
STABLE_USDT_PAIRS = {
    "USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "USDP/USDT",
    "EUR/USDT", "BUSD/USDT", "DAI/USDT",
}

# Leveraged tokens to exclude (e.g. BTCUP/USDT, BTCDOWN/USDT).
LEVERAGED_SUBSTRINGS = ("UP/USDT", "DOWN/USDT", "BULL/USDT", "BEAR/USDT")


def create_binance_exchange(data_vision: bool = False) -> "ccxt.binance":
    """Create a Binance spot exchange (optionally on the public data mirror)."""
    ex = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if data_vision:
        ex.urls["api"]["public"] = DATA_API_VISION
    return ex


def is_binance_region_block(exc: Exception) -> bool:
    """Detect Binance HTTP 451 / regional-restriction errors."""
    msg = str(exc).lower()
    return (
        "451" in msg
        or "restricted location" in msg
        or "service unavailable from a restricted" in msg
    )


def filter_binance_usdt_spot(markets: dict, tickers: dict,
                             min_vol_usdt: float = 1_000_000) -> List[str]:
    """Filter Binance markets to active spot /USDT pairs by quote volume."""
    syms = []
    for sym, m in markets.items():
        if not m.get("active") or not m.get("spot"):
            continue
        if not sym.endswith("/USDT"):
            continue
        if sym in STABLE_USDT_PAIRS:
            continue
        if any(w in sym for w in LEVERAGED_SUBSTRINGS):
            continue
        qv = (tickers.get(sym, {}) or {}).get("quoteVolume") or 0
        if qv >= min_vol_usdt:
            syms.append(sym)
    return sorted(set(syms))


class DataFeed:
    """Unified data feed for BIST and Crypto markets."""

    def __init__(self, config: dict):
        self.config = config
        self.timezone = pytz.timezone(config.get('general', {}).get('timezone', 'Europe/Istanbul'))
        self.lookback_days = config.get('general', {}).get('data_lookback_days', 60)
        self.timeframe = config.get('general', {}).get('timeframe', '1h')

        # Binance Spot = primary public data source for crypto
        self.binance = create_binance_exchange(data_vision=False)
        # KuCoin = last-resort fallback for open positions not listed on Binance
        self.kucoin = ccxt.kucoin({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'},
        })
        self._binance_vision = False

    # ------------------------------------------------------------------ #
    # Binance helpers (with automatic region-block fallback)
    # ------------------------------------------------------------------ #
    def _switch_to_vision(self):
        if not self._binance_vision:
            logger.warning(
                "Binance bolgesel kisitlama (HTTP 451) tespit edildi; "
                "data-api.binance.vision public endpoint'ine geciliyor."
            )
            self.binance.urls["api"]["public"] = DATA_API_VISION
            self._binance_vision = True

    def _binance_retry(self, fn):
        """Run fn(self.binance) and retry once on the data mirror if 451."""
        try:
            return fn(self.binance)
        except Exception as e:
            if is_binance_region_block(e):
                self._switch_to_vision()
                return fn(self.binance)
            raise

    def load_binance_markets(self):
        return self._binance_retry(lambda ex: ex.load_markets())

    def fetch_binance_tickers(self):
        return self._binance_retry(lambda ex: ex.fetch_tickers())

    def fetch_binance_usdt_symbols(self, min_vol_usdt: float = 1_000_000) -> List[str]:
        """Active Binance Spot /USDT pairs (excluding stablecoins & leveraged tokens)."""
        try:
            markets = self.load_binance_markets()
            tickers = self.fetch_binance_tickers()
        except Exception as e:
            logger.error(f"Binance sembol listesi alinamadi: {e}")
            return []
        return filter_binance_usdt_spot(markets, tickers, min_vol_usdt)

    # ------------------------------------------------------------------ #
    # Lookback
    # ------------------------------------------------------------------ #
    def _get_lookback_start(self) -> datetime:
        """Calculate start date for data fetching."""
        end = datetime.now(self.timezone)
        start = end - timedelta(days=self.lookback_days)
        return start

    # ------------------------------------------------------------------ #
    # BIST
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    # Crypto
    # ------------------------------------------------------------------ #
    def _fetch_ohlcv(self, exchange, symbol: str) -> pd.DataFrame:
        """Fetch full OHLCV history from a given ccxt exchange."""
        tf_map = {'1h': '1h', '4h': '4h', '1d': '1d', '15m': '15m', '5m': '5m'}
        ccxt_tf = tf_map.get(self.timeframe, '1h')

        since = int((datetime.now(self.timezone) - timedelta(days=self.lookback_days)).timestamp() * 1000)

        all_ohlcv = []
        limit = 1000  # Binance max limit per request

        while True:
            ohlcv = exchange.fetch_ohlcv(symbol, ccxt_tf, since=since, limit=limit)
            if not ohlcv:
                break

            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1

            # Rate limiting
            time.sleep(exchange.rateLimit / 1000)

            # Break if we got less than limit (end of data)
            if len(ohlcv) < limit:
                break

        if not all_ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df.set_index('timestamp')
        df = df.tz_convert(self.timezone)

        # Remove duplicates and sort
        df = df[~df.index.duplicated(keep='last')].sort_index()
        return df

    def _fetch_binance_ohlcv(self, symbol: str) -> pd.DataFrame:
        return self._binance_retry(lambda ex: self._fetch_ohlcv(ex, symbol))

    def fetch_crypto_data(self, symbol: str) -> pd.DataFrame:
        """
        Fetch Crypto OHLCV — Binance Spot primary, KuCoin fallback.

        The KuCoin fallback keeps tracking open positions whose pair is not
        listed on Binance (e.g. STAY/USDT). New-buy screening still uses only
        the Binance universe via fetch_binance_usdt_symbols().

        Parameters:
        - symbol: Trading pair (e.g., "BTC/USDT")

        Returns:
        - DataFrame with OHLCV data
        """
        logger.info(f"Fetching Crypto data for {symbol} (Binance primary)")

        try:
            df = self._fetch_binance_ohlcv(symbol)
            if df is not None and not df.empty:
                logger.info(f"Fetched {len(df)} candles for {symbol} (Binance)")
                return df
        except Exception as e:
            logger.warning(f"Binance verisi alinamadi ({symbol}): {e}; KuCoin fallback denenecek.")

        # Fallback: KuCoin (symbols not listed on Binance, e.g. STAY/USDT)
        try:
            df = self._fetch_ohlcv(self.kucoin, symbol)
            if df is not None and not df.empty:
                logger.info(f"Fetched {len(df)} candles for {symbol} (KuCoin fallback)")
                return df
        except Exception as e:
            logger.error(f"KuCoin fallback de basarisiz ({symbol}): {e}")

        logger.warning(f"No crypto data returned for {symbol}")
        return pd.DataFrame()

    # ------------------------------------------------------------------ #
    # Generic dispatch / latest price
    # ------------------------------------------------------------------ #
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

    def get_crypto_latest_price(self, symbol: str) -> Optional[float]:
        """Latest crypto price — Binance primary, KuCoin fallback."""
        try:
            ticker = self._binance_retry(lambda ex: ex.fetch_ticker(symbol))
            if ticker and ticker.get('last'):
                return float(ticker['last'])
        except Exception as e:
            logger.warning(f"Binance ticker alinamadi ({symbol}): {e}; KuCoin fallback.")
        try:
            ticker = self.kucoin.fetch_ticker(symbol)
            return float(ticker['last'])
        except Exception as e:
            logger.error(f"Error getting latest crypto price for {symbol}: {e}")
        return None

    def get_latest_price(self, market: str, symbol: str) -> Optional[float]:
        """Get the latest price for a symbol."""
        try:
            if market.upper() == 'BIST':
                ticker = yf.Ticker(symbol)
                data = ticker.history(period='1d', interval='1m')
                if not data.empty:
                    return float(data['Close'].iloc[-1])
            elif market.upper() == 'CRYPTO':
                return self.get_crypto_latest_price(symbol)
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
    print("\nTesting Crypto data fetch (Binance)...")
    crypto_data = feed.fetch_crypto_data("BTC/USDT")
    print(f"BTC/USDT: {len(crypto_data)} candles")
    if not crypto_data.empty:
        print(crypto_data.tail(3))
