"""
Main Trading Bot Module
Implements the trading strategy using SALMA v3.0, Twin Range Filter, RSI, RVOL, and ATR.
"""

import json
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import pandas as pd
import pytz

from data_feed import DataFeed
from indicators import calculate_all_indicators
from portfolio import PortfolioManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Trading signal."""
    timestamp: datetime
    market: str
    symbol: str
    signal_type: str  # 'BUY' or 'SELL'
    price: float
    reason: str
    salma_value: float
    salma_color: int
    trf_signal: int
    score: int = 0
    rsi: float = 0
    rvol: float = 0
    trend_age: int = 0


class TradingBot:
    """Main trading bot implementing the strategy."""

    def __init__(self, config_path: str = 'config.json'):
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.timezone = pytz.timezone(self.config.get('general', {}).get('timezone', 'Europe/Istanbul'))
        self.data_feed = DataFeed(self.config)
        self.portfolio = PortfolioManager(self.config)

        # Strategy parameters
        self.strategy_config = self.config.get('strategy', {})
        self.require_trf_long = self.strategy_config.get('require_trf_long_first', True)
        self.max_trend_age = self.strategy_config.get('max_trend_age_bars', 6)
        self.salma_pullback_pct = self.strategy_config.get('salma_pullback_proximity_pct', 1.5)

        # Filter thresholds
        self.indicators_cfg = self.config.get('indicators', {})
        self.rsi_min = self.indicators_cfg.get('RSI', {}).get('min', 50)
        self.rsi_max = self.indicators_cfg.get('RSI', {}).get('max', 72)
        self.rvol_min = self.indicators_cfg.get('RVOL', {}).get('min', 1.2)

        # Stop loss config
        self.sl_cfg = self.strategy_config.get('stop_loss', {'type': 'ATR', 'atr_multiplier': 2.0, 'max_pct': 0.05})
        self.ts_cfg = self.strategy_config.get('trailing_stop', {'enabled': True, 'activation_pct': 0.04, 'atr_multiplier': 1.5})

        # Indicator parameters
        self.salma_params = self.config.get('indicators', {}).get('SALMA', {})
        self.trf_params = self.config.get('indicators', {}).get('TwinRangeFilter', {})

        # Backtest results
        self.backtest_trades = []
        self.backtest_equity = []

    def is_market_open(self, market: str) -> bool:
        """Check if market is open for trading (weekend protection for BIST)"""
        now = datetime.now(self.timezone)
        weekday = now.weekday()  # 0=Monday, 6=Sunday

        market_cfg = self.config.get('markets', {}).get(market, {})
        trading_days = market_cfg.get("trading_hours", {}).get("days", [0,1,2,3,4])

        return weekday in trading_days

    def calculate_signal_score(self, latest, prev, df) -> int:
        """
        Calculate signal score (0-100) based on:
        - Trend freshness (trend_age): newer = higher score
        - RVOL: higher = higher score
        - RSI balance: closer to 60 (middle of 50-72) = higher score
        - SALMA proximity: closer to SALMA = higher score (for pullback entries)
        - Re-acceleration bonus
        """
        score = 0

        # 1. Trend freshness (0-30 points)
        trend_age = latest.get("trend_age", 0)
        if trend_age > 0:
            if trend_age <= 1:
                score += 30  # Fresh crossover
            elif trend_age <= 3:
                score += 25  # Very fresh trend
            elif trend_age <= 6:
                score += 15  # Fresh trend
            else:
                score += 5   # Older trend

        # 2. RVOL (0-25 points)
        rvol = latest.get("rvol", 1.0)
        if rvol >= 3.0:
            score += 25
        elif rvol >= 2.0:
            score += 20
        elif rvol >= 1.5:
            score += 15
        elif rvol >= 1.2:
            score += 10

        # 3. RSI balance (0-20 points) - optimal around 60
        rsi = latest.get("rsi", 50)
        if 50 <= rsi <= 72:
            # Distance from center (61) - closer is better
            dist_from_optimal = abs(rsi - 61)
            if dist_from_optimal <= 3:
                score += 20
            elif dist_from_optimal <= 6:
                score += 15
            elif dist_from_optimal <= 11:
                score += 10
            else:
                score += 5

        # 4. SALMA proximity (0-15 points) - closer to SALMA is better for pullback
        proximity = latest.get("salma_proximity_pct", 100)
        if proximity <= 0.5:
            score += 15
        elif proximity <= 1.0:
            score += 12
        elif proximity <= 1.5:
            score += 10
        elif proximity <= 2.5:
            score += 5

        # 5. Re-acceleration bonus (0-10 points)
        if latest.get("reaccelerated", False):
            score += 10

        return min(score, 100)

    def check_entry_signal(self, latest, prev, df) -> tuple:
        """
        Check if entry conditions are met.
        Returns: (bool, str) - (signal_triggered, signal_type)
        signal_type: "fresh_crossover" or "pullback_reacceleration"
        """
        # Base conditions: TRF Long + SALMA Green
        trf_long = int(latest.get("trf_long", 0)) == 1
        salma_green = int(latest.get("salma_color", 0)) == 1

        if not (trf_long and salma_green):
            return (False, "")

        trend_age = latest.get("trend_age", 0)

        # Condition 1: Fresh crossover (trend_age <= max_trend_age)
        # This includes the salma_red_to_green transition
        if trend_age > 0 and trend_age <= self.max_trend_age:
            # Verify it's a genuine fresh signal - either fresh red_to_green or early trend
            if latest.get("salma_red_to_green", False) or trend_age <= 2:
                return (True, "fresh_crossover")

        # Condition 2: Pullback & re-acceleration
        # Price pulled back to near SALMA (within 1.5%) and then re-accelerated
        if latest.get("reaccelerated", False):
            # Verify the pullback happened recently (within last few bars)
            pulled_back_recent = False
            for i in range(min(5, len(df))):
                idx = -(i+1)
                if df.iloc[idx].get("near_salma", False):
                    pulled_back_recent = True
                    break
            if pulled_back_recent:
                return (True, "pullback_reacceleration")

        return (False, "")

    def check_filters(self, latest) -> tuple:
        """Check RSI and RVOL filters"""
        rsi = latest.get("rsi", 50)
        rvol = latest.get("rvol", 1.0)

        if rsi < self.rsi_min or rsi > self.rsi_max:
            return (False, f"RSI {rsi:.1f} outside [{self.rsi_min}-{self.rsi_max}]")

        if rvol < self.rvol_min:
            return (False, f"RVOL {rvol:.2f} < {self.rvol_min}")

        return (True, "OK")

    def calculate_position_size(self, market: str, price: float) -> float:
        """
        Calculate fixed position size based on initial balance slices.
        BIST: initial_balance / 3 (100000/3)
        CRYPTO: initial_balance / 10 (1000/10)
        """
        initial_balance = self.portfolio.get_initial_balance(market)
        slices = self.config.get('markets', {}).get(market, {}).get('fixed_position_slices', 1)

        position_value = initial_balance / slices
        qty = position_value / price

        if market == 'BIST':
            qty = int(qty)  # Whole shares
        else:
            qty = round(qty, 6)  # 6 decimals for crypto

        return qty

    def calculate_stop_loss(self, market: str, entry_price: float, atr: float, highest_price: float = None) -> float:
        """
        Calculate stop loss price.
        - ATR-based: entry - (atr * multiplier)
        - Cap at max_pct (default 5%)
        """
        sl_type = self.sl_cfg.get("type", "ATR")
        atr_mult = self.sl_cfg.get("atr_multiplier", 2.0)
        max_pct = self.sl_cfg.get("max_pct", 0.05)

        if sl_type == "ATR" and atr > 0:
            sl_price = entry_price - (atr * atr_mult)
        else:
            sl_price = entry_price * (1 - max_pct)

        # Cap at max percentage loss
        max_sl_price = entry_price * (1 - max_pct)
        sl_price = max(sl_price, max_sl_price)  # Use the higher (less loss) stop

        return sl_price

    def calculate_trailing_stop(self, highest_price: float, atr: float) -> float:
        """Calculate trailing stop price after activation"""
        if not self.ts_cfg.get("enabled", True):
            return 0
        ts_mult = self.ts_cfg.get("atr_multiplier", 1.5)
        if atr > 0:
            return highest_price - (atr * ts_mult)
        return 0

    def analyze_symbol(self, market: str, symbol: str, df: pd.DataFrame) -> List[Signal]:
        """
        Analyze a symbol and generate signals with scoring.

        Strategy:
        1. TRF Long + SALMA Green required
        2. Entry on: fresh crossover (trend_age <= 6) OR pullback & re-acceleration
        3. Filters: RSI 50-72, RVOL >= 1.2
        4. Score candidates 0-100, pick highest scoring
        5. Exit: SALMA Green->Red OR ATR stop loss OR trailing stop
        """
        signals = []

        if df.empty or len(df) < 60:
            return signals

        # Calculate indicators
        df = calculate_all_indicators(df, self.salma_params, self.trf_params)

        # Check if we have a position
        has_position = self.portfolio.has_position(market, symbol)
        position = self.portfolio.get_position(market, symbol) if has_position else None

        # Get latest values (use last CLOSED candle for signals)
        latest = df.iloc[-2]  # Last closed 1h candle
        prev = df.iloc[-3] if len(df) > 2 else latest
        current_price = float(df.iloc[-1]["close"])  # Current forming candle
        atr = float(latest.get("atr", 0))

        # --- BUY LOGIC ---
        if not has_position:
            # Weekend protection for BIST
            if market == "BIST" and not self.is_market_open("BIST"):
                return signals

            # Check entry signal
            signal_triggered, signal_type = self.check_entry_signal(latest, prev, df)
            if not signal_triggered:
                return signals

            # Check quality filters
            filters_pass, filter_msg = self.check_filters(latest)
            if not filters_pass:
                logger.debug(f"{symbol} filtered out: {filter_msg}")
                return signals

            # Calculate signal score
            score = self.calculate_signal_score(latest, prev, df)

            # Calculate position size (fixed slices from initial balance)
            qty = self.calculate_position_size(market, current_price)
            if qty <= 0:
                return signals

            # Check if we can actually afford it
            can_trade, reason = self.portfolio.can_open_position(market, symbol, current_price, qty)
            if not can_trade:
                logger.debug(f"Cannot buy {symbol}: {reason}")
                return signals

            signal = Signal(
                timestamp=latest.name.to_pydatetime() if hasattr(latest.name, 'to_pydatetime') else latest.name,
                market=market,
                symbol=symbol,
                signal_type='BUY',
                price=current_price,
                reason=f"{signal_type} (score={score})",
                salma_value=latest['salma'],
                salma_color=int(latest['salma_color']),
                trf_signal=int(latest['trf_signal']),
                score=score,
                rsi=latest.get('rsi', 0),
                rvol=latest.get('rvol', 0),
                trend_age=int(latest.get('trend_age', 0))
            )
            signals.append(signal)
            logger.info(f"BUY SIGNAL: {symbol} @ {current_price:.4f} | Score: {score}/100 | {signal_type} | RSI={latest.get('rsi',0):.1f} RVOL={latest.get('rvol',0):.2f} TrendAge={int(latest.get('trend_age',0))}")

        # --- SELL LOGIC ---
        elif has_position and position:
            entry_price = position.entry_price
            pnl_ratio = (current_price - entry_price) / entry_price

            # Track highest price for trailing stop
            if not hasattr(position, 'highest_price') or position.highest_price < current_price:
                position.highest_price = current_price

            # Check trailing stop activation
            ts_activated = False
            trailing_stop_price = 0
            if self.ts_cfg.get("enabled", True):
                activation_pct = self.ts_cfg.get("activation_pct", 0.04)
                if pnl_ratio >= activation_pct:
                    ts_activated = True
                    trailing_stop_price = self.calculate_trailing_stop(position.highest_price, atr)

            # Determine effective stop loss
            if ts_activated and trailing_stop_price > 0:
                effective_sl = trailing_stop_price
                sl_reason = f"Trailing Stop ({trailing_stop_price:.4f})"
            else:
                effective_sl = self.calculate_stop_loss(market, entry_price, atr, position.highest_price if hasattr(position, 'highest_price') else None)
                sl_reason = f"Stop Loss ({effective_sl:.4f})"

            # Check exit conditions
            exit_triggered = False
            exit_reason = ""

            # 1. SALMA Green to Red
            if latest.get("salma_green_to_red", False):
                exit_triggered = True
                exit_reason = "SALMA Yeşil->Kırmızı"

            # 2. Stop Loss / Trailing Stop hit
            elif current_price <= effective_sl:
                exit_triggered = True
                exit_reason = sl_reason

            if exit_triggered:
                signal = Signal(
                    timestamp=latest.name.to_pydatetime() if hasattr(latest.name, 'to_pydatetime') else latest.name,
                    market=market,
                    symbol=symbol,
                    signal_type='SELL',
                    price=current_price,
                    reason=exit_reason,
                    salma_value=latest['salma'],
                    salma_color=int(latest['salma_color']),
                    trf_signal=int(latest['trf_signal'])
                )
                signals.append(signal)
                logger.info(f"SELL SIGNAL: {symbol} @ {current_price:.4f} - {exit_reason}")

        return signals

    def run_analysis(self, markets: List[str] = None) -> Dict[str, List[Signal]]:
        """Run analysis on all configured symbols and rank by score."""
        if markets is None:
            markets = [m for m, c in self.config.get('markets', {}).items() if c.get('enabled', True)]

        all_signals = {}
        all_candidates = []

        for market in markets:
            logger.info(f"Analyzing {market} market...")
            symbols = self.config.get('markets', {}).get(market, {}).get('symbols', [])

            for symbol in symbols:
                try:
                    df = self.data_feed.fetch_market_data(market, symbol)
                    if df.empty:
                        logger.warning(f"No data for {symbol}")
                        continue

                    signals = self.analyze_symbol(market, symbol, df)
                    if signals:
                        all_signals.setdefault(market, []).extend(signals)
                        # Collect for ranking
                        for sig in signals:
                            if sig.signal_type == 'BUY':
                                all_candidates.append(sig)

                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")

        # Rank all BUY candidates by score
        all_candidates.sort(key=lambda x: x.score, reverse=True)

        # Apply position limits per market
        final_signals = {}
        for market in markets:
            market_candidates = [c for c in all_candidates if c.market == market]
            open_cnt = len(self.portfolio.get_open_positions(market))
            max_pos = self.config.get('markets', {}).get(market, {}).get('max_positions', 3)
            available_slots = max_pos - open_cnt

            final_signals[market] = market_candidates[:available_slots]

            for sig in final_signals[market]:
                logger.info(f"  TOP PICK: {sig.symbol} | Score: {sig.score}/100 | RSI: {sig.rsi:.1f} | RVOL: {sig.rvol:.2f} | TrendAge: {sig.trend_age}")

        return final_signals

    def execute_signals(self, signals: Dict[str, List[Signal]]) -> List:
        """Execute trading signals."""
        executed = []

        for market, market_signals in signals.items():
            for signal in market_signals:
                if signal.signal_type == 'BUY':
                    # Calculate quantity based on fixed position sizing
                    qty = self.calculate_position_size(market, signal.price)

                    if qty > 0:
                        trade = self.portfolio.execute_buy(
                            market, signal.symbol, signal.price, qty
                        )
                        if trade:
                            executed.append(trade)

                elif signal.signal_type == 'SELL':
                    trade = self.portfolio.execute_sell(market, signal.symbol, signal.price)
                    if trade:
                        executed.append(trade)

        return executed

    def run_backtest(self, market: str, symbol: str, initial_balance: float = None) -> Dict:
        """
        Run backtest on historical data with new strategy logic.

        Returns:
        - Dictionary with backtest results
        """
        logger.info(f"Running backtest for {market} {symbol}...")

        # Fetch data
        df = self.data_feed.fetch_market_data(market, symbol)
        if df.empty:
            return {'error': 'No data'}

        # Calculate indicators
        df = calculate_all_indicators(df, self.salma_params, self.trf_params)

        # Backtest simulation
        balance = initial_balance or self.config.get('markets', {}).get(market, {}).get('initial_balance', 10000)
        initial = balance
        position = None
        trades = []
        equity_curve = []
        max_drawdown = 0
        peak = balance

        for i in range(len(df)):
            row = df.iloc[i]
            current_price = row['close']
            timestamp = row.name
            atr = row.get('atr', 0)

            # Use previous bar for signals (simulate real-time)
            if i == 0:
                continue
            latest = df.iloc[i-1]
            prev = df.iloc[i-2] if i > 1 else latest

            salma_color = latest['salma_color']
            trf_long = latest['trf_long']
            salma_red_to_green = latest['salma_red_to_green']
            salma_green_to_red = latest['salma_green_to_red']
            trend_age = latest.get('trend_age', 0)
            reaccelerated = latest.get('reaccelerated', False)
            near_salma = latest.get('near_salma', False)
            rsi = latest.get('rsi', 50)
            rvol = latest.get('rvol', 1.0)

            # Check filters
            filters_pass = (self.rsi_min <= rsi <= self.rsi_max) and (rvol >= self.rvol_min)

            # BUY
            if position is None:
                # Weekend protection for BIST
                if market == "BIST":
                    # Simplified: skip weekend bars in backtest
                    ts = timestamp
                    if hasattr(ts, 'weekday') and ts.weekday() >= 5:
                        continue

                # Check entry signal
                signal_triggered = False
                signal_type = ""

                trf_long_active = int(trf_long) == 1
                salma_green = int(salma_color) == 1

                if trf_long_active and salma_green:
                    if trend_age > 0 and trend_age <= self.max_trend_age:
                        if salma_red_to_green or trend_age <= 2:
                            signal_triggered = True
                            signal_type = "fresh_crossover"
                    elif reaccelerated:
                        # Check recent pullback
                        pulled_back_recent = False
                        for j in range(max(0, i-5), i):
                            if df.iloc[j].get('near_salma', False):
                                pulled_back_recent = True
                                break
                        if pulled_back_recent and filters_pass:
                            signal_triggered = True
                            signal_type = "pullback_reacceleration"

                if signal_triggered and filters_pass:
                    # Calculate position size (fixed slices)
                    initial_bal = self.config.get('markets', {}).get(market, {}).get('initial_balance', 10000)
                    slices = self.config.get('markets', {}).get(market, {}).get('fixed_position_slices', 1)
                    position_value = initial_bal / slices
                    quantity = position_value / current_price

                    if market == 'BIST':
                        quantity = int(quantity)
                    else:
                        quantity = round(quantity, 6)

                    if quantity > 0:
                        cost = current_price * quantity
                        commission = max(cost * self.config['markets'][market]['commission_rate'],
                                       self.config['markets'][market]['min_commission'])
                        total_cost = cost + commission

                        if total_cost <= balance:
                            balance -= total_cost
                            position = {
                                'symbol': symbol,
                                'quantity': quantity,
                                'entry_price': current_price,
                                'entry_time': timestamp,
                                'entry_commission': commission,
                                'highest_price': current_price,
                                'signal_type': signal_type
                            }
                            trades.append({
                                'type': 'BUY',
                                'time': timestamp,
                                'price': current_price,
                                'quantity': quantity,
                                'commission': commission,
                                'balance': balance,
                                'signal_type': signal_type
                            })

            # SELL
            elif position is not None:
                # Update highest price
                if current_price > position['highest_price']:
                    position['highest_price'] = current_price

                pnl_ratio = (current_price - position['entry_price']) / position['entry_price']

                # Check trailing stop
                ts_activated = False
                trailing_stop_price = 0
                if self.ts_cfg.get("enabled", True):
                    activation_pct = self.ts_cfg.get("activation_pct", 0.04)
                    if pnl_ratio >= activation_pct:
                        ts_activated = True
                        trailing_stop_price = self.calculate_trailing_stop(position['highest_price'], atr)

                # Effective stop
                if ts_activated and trailing_stop_price > 0:
                    effective_sl = trailing_stop_price
                else:
                    effective_sl = self.calculate_stop_loss(market, position['entry_price'], atr, position['highest_price'])

                exit_triggered = False
                exit_reason = ""

                if salma_green_to_red:
                    exit_triggered = True
                    exit_reason = "SALMA Green->Red"
                elif current_price <= effective_sl:
                    exit_triggered = True
                    exit_reason = f"Stop Loss ({effective_sl:.4f})"

                if exit_triggered:
                    proceeds = current_price * position['quantity']
                    commission = max(proceeds * self.config['markets'][market]['commission_rate'],
                                   self.config['markets'][market]['min_commission'])
                    net_proceeds = proceeds - commission

                    pnl = (current_price - position['entry_price']) * position['quantity']
                    pnl_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                    balance += net_proceeds
                    trades.append({
                        'type': 'SELL',
                        'time': timestamp,
                        'price': current_price,
                        'quantity': position['quantity'],
                        'commission': commission,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'balance': balance,
                        'exit_reason': exit_reason
                    })
                    position = None

            # Track equity
            position_value = 0
            if position:
                position_value = position['quantity'] * current_price

            total_equity = balance + position_value
            equity_curve.append({
                'time': timestamp,
                'equity': total_equity,
                'balance': balance,
                'position_value': position_value
            })

            # Track drawdown
            if total_equity > peak:
                peak = total_equity
            drawdown = (peak - total_equity) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Close any open position at the end
        if position is not None:
            last_price = df.iloc[-1]['close']
            proceeds = last_price * position['quantity']
            commission = max(proceeds * self.config['markets'][market]['commission_rate'],
                           self.config['markets'][market]['min_commission'])
            net_proceeds = proceeds - commission

            pnl = (last_price - position['entry_price']) * position['quantity']
            pnl_pct = ((last_price - position['entry_price']) / position['entry_price']) * 100

            balance += net_proceeds
            trades.append({
                'type': 'SELL (EOD)',
                'time': df.iloc[-1].name,
                'price': last_price,
                'quantity': position['quantity'],
                'commission': commission,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'balance': balance,
                'exit_reason': 'End of Data'
            })

        # Calculate statistics
        completed_trades = [t for t in trades if t['type'] in ('SELL', 'SELL (EOD)')]
        winning_trades = [t for t in completed_trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in completed_trades if t.get('pnl', 0) < 0]

        total_pnl = balance - initial
        total_pnl_pct = (total_pnl / initial) * 100

        win_rate = len(winning_trades) / len(completed_trades) * 100 if completed_trades else 0
        avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        profit_factor = abs(sum(t['pnl'] for t in winning_trades) / sum(t['pnl'] for t in losing_trades)) if losing_trades else float('inf')

        results = {
            'symbol': symbol,
            'market': market,
            'initial_balance': initial,
            'final_balance': balance,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'total_trades': len(completed_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'trades': trades,
            'equity_curve': equity_curve
        }

        logger.info(f"Backtest complete: {symbol} - PnL: {total_pnl:.2f} ({total_pnl_pct:.2f}%), Trades: {len(completed_trades)}, Win Rate: {win_rate:.1f}%")

        return results

    def run_full_backtest(self, markets: List[str] = None) -> Dict:
        """Run backtest on all symbols."""
        if markets is None:
            markets = [m for m, c in self.config.get('markets', {}).items() if c.get('enabled', True)]

        all_results = {}

        for market in markets:
            logger.info(f"Backtesting {market} market...")
            symbols = self.config.get('markets', {}).get(market, {}).get('symbols', [])
            market_results = {}

            for symbol in symbols:
                try:
                    result = self.run_backtest(market, symbol)
                    market_results[symbol] = result
                except Exception as e:
                    logger.error(f"Backtest error for {symbol}: {e}")
                    market_results[symbol] = {'error': str(e)}

            all_results[market] = market_results

        # Print summary
        self._print_backtest_summary(all_results)

        return all_results

    def _print_backtest_summary(self, results: Dict):
        """Print formatted backtest summary."""
        print("\n" + "="*80)
        print("BACKTEST RESULTS SUMMARY")
        print("="*80)

        for market, market_results in results.items():
            print(f"\n{'='*80}")
            print(f"MARKET: {market}")
            print(f"{'='*80}")
            print(f"{'Symbol':<15} {'Trades':>6} {'Win%':>6} {'PnL%':>8} {'MaxDD%':>7} {'PF':>6} {'Final Bal':>12}")
            print("-"*80)

            for symbol, result in market_results.items():
                if 'error' in result:
                    print(f"{symbol:<15} {'ERROR':>6} {result['error']}")
                else:
                    print(f"{symbol:<15} {result['total_trades']:>6} {result['win_rate']:>5.1f}% "
                          f"{result['total_pnl_pct']:>7.2f}% {result['max_drawdown']:>6.2f}% "
                          f"{result['profit_factor']:>6.2f} {result['final_balance']:>12.2f}")

        print("\n" + "="*80)


def main():
    parser = argparse.ArgumentParser(description='Al-Sat Trading Bot')
    parser.add_argument('--config', default='config.json', help='Config file path')
    parser.add_argument('--mode', choices=['live', 'backtest', 'analyze'], default='analyze',
                        help='Run mode')
    parser.add_argument('--market', choices=['BIST', 'CRYPTO', 'ALL'], default='ALL',
                        help='Market to trade')
    parser.add_argument('--symbol', help='Specific symbol to analyze')

    args = parser.parse_args()

    bot = TradingBot(args.config)

    markets = ['BIST', 'CRYPTO'] if args.market == 'ALL' else [args.market]

    if args.mode == 'backtest':
        if args.symbol:
            # Single symbol backtest
            for market in markets:
                if args.symbol in bot.config.get('markets', {}).get(market, {}).get('symbols', []):
                    result = bot.run_backtest(market, args.symbol)
                    print(json.dumps(result, indent=2, default=str))
                    break
        else:
            bot.run_full_backtest(markets)

    elif args.mode == 'analyze':
        signals = bot.run_analysis(markets)
        print("\n" + "="*60)
        print("CURRENT SIGNALS (Ranked by Score)")
        print("="*60)
        for market, market_signals in signals.items():
            for sig in market_signals:
                print(f"{sig.timestamp} | {market} | {sig.symbol} | {sig.signal_type} @ {sig.price:.4f} | Score: {sig.score}/100 | {sig.reason}")

        if not signals or all(len(v) == 0 for v in signals.values()):
            print("No signals generated.")

    elif args.mode == 'live':
        print("Live trading mode - not implemented in demo")
        print("Use --mode analyze for signal generation")
        print("Use --mode backtest for historical testing")


if __name__ == "__main__":
    main()