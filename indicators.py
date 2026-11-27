"""
Technical Indicators Module
Contains SALMA v3.0 (Smooth and Lazy Moving Average) and Twin Range Filter implementations.
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional


def wma(series: pd.Series, length: int) -> pd.Series:
    """Weighted Moving Average"""
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average"""
    return series.ewm(span=length, adjust=False).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Rolling Moving Average (Wilder's Smoothing)"""
    return series.ewm(alpha=1/length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average"""
    return series.rolling(length).mean()


def stdev(series: pd.Series, length: int) -> pd.Series:
    """Standard Deviation"""
    return series.rolling(length).std(ddof=0)


def salma_v3(
    close: pd.Series,
    length: int = 45,
    extra_smooth: int = 1,
    sd_width: float = 0.3,
    sd_length: int = 5,
    **kwargs
) -> Tuple[pd.Series, pd.Series]:
    """
    SALMA v3.0 - Smooth and Lazy Moving Average

    Parameters:
    - close: Close price series
    - length: Main smoothing length (default 45)
    - extra_smooth: Additional smoothing factor (default 1)
    - sd_width: Standard deviation width multiplier (default 0.3)
    - sd_length: Standard deviation calculation length (default 5)

    Returns:
    - salma_line: The SALMA line values
    - salma_color: 1 for green (rising), -1 for red (falling), 0 for neutral
    """
    # First smoothing pass - Weighted Moving Average
    wma1 = wma(close, length)

    # Second smoothing pass - EMA on WMA
    ema1 = ema(wma1, length)

    # Third smoothing pass - RMA (Wilder's) on EMA
    rma1 = rma(ema1, length)

    # Extra smoothing if specified
    if extra_smooth > 0:
        for _ in range(extra_smooth):
            rma1 = rma(rma1, length)

    salma_line = rma1.copy()

    # Determine color based on SALMA slope
    salma_color = pd.Series(0, index=close.index, dtype=int)
    for i in range(1, len(close)):
        if pd.isna(salma_line.iloc[i]) or pd.isna(salma_line.iloc[i-1]):
            salma_color.iloc[i] = 0
            continue
        diff = salma_line.iloc[i] - salma_line.iloc[i-1]
        if diff > 0:
            salma_color.iloc[i] = 1
        elif diff < 0:
            salma_color.iloc[i] = -1
        else:
            salma_color.iloc[i] = salma_color.iloc[i-1]

    # Fill initial values
    salma_color.iloc[0] = 1 if salma_line.iloc[1] > salma_line.iloc[0] else -1

    return salma_line, salma_color


def twin_range_filter(
    close: pd.Series,
    fast_period: int = 12,
    fast_range: float = 1.0,
    slow_period: int = 4,
    slow_range: float = 2.0,
    **kwargs
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Twin Range Filter

    Parameters:
    - close: Close price series
    - fast_period: Fast filter period (default 12)
    - fast_range: Fast filter range multiplier (default 1.0)
    - slow_period: Slow filter period (default 4)
    - slow_range: Slow filter range multiplier (default 2.0)

    Returns:
    - trf_fast: Fast filter line
    - trf_slow: Slow filter line
    - trf_signal: 1 for Long, -1 for Short, 0 for Neutral
    """
    # Calculate True Range for both periods
    high_low = close.rolling(2).max() - close.rolling(2).min()

    # Fast filter
    fast_atr = high_low.rolling(fast_period).mean()
    fast_range_val = fast_atr * fast_range

    trf_fast = pd.Series(index=close.index, dtype=float)
    trf_fast.iloc[0] = close.iloc[0]
    fast_dir = pd.Series(0, index=close.index, dtype=int)
    fast_dir.iloc[0] = 1

    for i in range(1, len(close)):
        prev_fast = trf_fast.iloc[i-1]
        curr_close = close.iloc[i]
        range_val = fast_range_val.iloc[i] if not pd.isna(fast_range_val.iloc[i]) else 0

        if curr_close > prev_fast + range_val:
            trf_fast.iloc[i] = curr_close - range_val
            fast_dir.iloc[i] = 1
        elif curr_close < prev_fast - range_val:
            trf_fast.iloc[i] = curr_close + range_val
            fast_dir.iloc[i] = -1
        else:
            trf_fast.iloc[i] = prev_fast
            fast_dir.iloc[i] = fast_dir.iloc[i-1]

    # Slow filter
    slow_atr = high_low.rolling(slow_period).mean()
    slow_range_val = slow_atr * slow_range

    trf_slow = pd.Series(index=close.index, dtype=float)
    trf_slow.iloc[0] = close.iloc[0]
    slow_dir = pd.Series(0, index=close.index, dtype=int)
    slow_dir.iloc[0] = 1

    for i in range(1, len(close)):
        prev_slow = trf_slow.iloc[i-1]
        curr_close = close.iloc[i]
        range_val = slow_range_val.iloc[i] if not pd.isna(slow_range_val.iloc[i]) else 0

        if curr_close > prev_slow + range_val:
            trf_slow.iloc[i] = curr_close - range_val
            slow_dir.iloc[i] = 1
        elif curr_close < prev_slow - range_val:
            trf_slow.iloc[i] = curr_close + range_val
            slow_dir.iloc[i] = -1
        else:
            trf_slow.iloc[i] = prev_slow
            slow_dir.iloc[i] = slow_dir.iloc[i-1]

    # Generate signals: Long when both fast and slow are bullish
    trf_signal = pd.Series(0, index=close.index, dtype=int)
    for i in range(len(close)):
        if fast_dir.iloc[i] == 1 and slow_dir.iloc[i] == 1:
            trf_signal.iloc[i] = 1  # Long
        elif fast_dir.iloc[i] == -1 and slow_dir.iloc[i] == -1:
            trf_signal.iloc[i] = -1  # Short
        else:
            trf_signal.iloc[i] = 0  # Neutral

    return trf_fast, trf_slow, trf_signal


def calculate_all_indicators(
    df: pd.DataFrame,
    salma_params: dict = None,
    trf_params: dict = None
) -> pd.DataFrame:
    """
    Calculate all indicators and add them to the DataFrame.

    Parameters:
    - df: DataFrame with OHLCV data (must have 'close' column)
    - salma_params: Dict with SALMA parameters
    - trf_params: Dict with Twin Range Filter parameters

    Returns:
    - DataFrame with added indicator columns
    """
    if salma_params is None:
        salma_params = {'length': 45, 'extra_smooth': 1, 'sd_width': 0.3, 'sd_length': 5}

    if trf_params is None:
        trf_params = {'fast_period': 12, 'fast_range': 1.0, 'slow_period': 4, 'slow_range': 2.0}

    close = df['close']

    # SALMA v3.0
    salma_line, salma_color = salma_v3(close, **salma_params)
    df['salma'] = salma_line
    df['salma_color'] = salma_color  # 1=green, -1=red
    df['salma_prev_color'] = salma_color.shift(1)

    # Detect color changes
    df['salma_red_to_green'] = (df['salma_prev_color'] == -1) & (df['salma_color'] == 1)
    df['salma_green_to_red'] = (df['salma_prev_color'] == 1) & (df['salma_color'] == -1)

    # Twin Range Filter
    trf_fast, trf_slow, trf_signal = twin_range_filter(close, **trf_params)
    df['trf_fast'] = trf_fast
    df['trf_slow'] = trf_slow
    df['trf_signal'] = trf_signal  # 1=Long, -1=Short, 0=Neutral
    df['trf_long'] = (trf_signal == 1).astype(int)
    df['trf_long_prev'] = df['trf_long'].shift(1)
    df['trf_long_signal'] = (df['trf_long'] == 1) & (df['trf_long_prev'] == 0)

    return df


if __name__ == "__main__":
    # Quick test with sample data
    import yfinance as yf

    print("Testing indicators with sample data...")
    data = yf.download("THYAO.IS", period="60d", interval="1h", progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data = data.dropna()

    data = calculate_all_indicators(data)
    print(f"Data shape: {data.shape}")
    print(f"Columns: {data.columns.tolist()}")
    print(f"\nLast 5 rows:")
    print(data[['close', 'salma', 'salma_color', 'trf_fast', 'trf_slow', 'trf_signal']].tail())
    print(f"\nSALMA color changes (red->green): {data['salma_red_to_green'].sum()}")
    print(f"SALMA color changes (green->red): {data['salma_green_to_red'].sum()}")
    print(f"TRF Long signals: {data['trf_long_signal'].sum()}")
