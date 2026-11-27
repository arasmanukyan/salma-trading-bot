"""
Main Trading Bot Module
Implements the trading strategy using SALMA v3.0 and Twin Range Filter.
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

        # Indicator parameters
        self.salma_params = self.config.get('indicators', {}).get('SALMA', {})
        self.trf_params = self.config.get('indicators', {}).get('TwinRangeFilter', {})

        # Backtest results
        self.backtest_trades = []
        self.backtest_equity = []

    def analyze_symbol(self, market: str, symbol: str, df: pd.DataFrame) -> List[Signal]:
        """
        Analyze a symbol and generate signals.

        Strategy:
        1. Wait for Twin Range Filter to give Long signal (trf_long_signal == True)
        2. After TRF Long, wait for SALMA to turn from red to green (salma_red_to_green == True)
        3. Buy on the close of that candle
        4. While in position, sell when SALMA turns from green to red (salma_green_to_red == True)
        """
        signals = []

        if df.empty or len(df) < 50:
            return signals

        # Calculate indicators
        df = calculate_all_indicators(df, self.salma_params, self.trf_params)

        # Check if we have a position
        has_position = self.portfolio.has_position(market, symbol)
        position = self.portfolio.get_position(market, symbol) if has_position else None

        # Get latest values
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        current_price = latest['close']
        salma_color = latest['salma_color']
        salma_prev_color = latest['salma_prev_color']
        trf_long = latest['trf_long']
        trf_long_signal = latest['trf_long_signal']
        salma_red_to_green = latest['salma_red_to_green']
        salma_green_to_red = latest['salma_green_to_red']

        # Log current state
        color_str = "GREEN" if salma_color == 1 else "RED" if salma_color == -1 else "NEUTRAL"
        trf_str = "LONG" if trf_long == 1 else "SHORT" if trf_long == -1 else "NEUTRAL"
        logger.debug(f"{symbol}: Price={current_price:.2f}, SALMA={color_str}, TRF={trf_str}, Pos={'YES' if has_position else 'NO'}")

        # --- BUY LOGIC ---
        if not has_position:
            # Check if TRF gave a long signal recently (within last few bars)
            trf_long_recent = False
            lookback = 30  # Check last 5 bars for TRF long signal
            for i in range(min(lookback, len(df))):
                if df.iloc[-(i+1)]['trf_long_signal']:
                    trf_long_recent = True
                    break

            # Entry condition: TRF Long signal occurred AND SALMA turns red to green
            if (self.require_trf_long and (trf_long == 1) and salma_red_to_green) or \
               (not self.require_trf_long and salma_red_to_green):

                # Calculate position size
                balance = self.portfolio.get_balance(market)
                max_pct = self.strategy_config.get('position_sizing', {}).get('max_position_pct', 0.2)
                max_position_value = balance * max_pct
                quantity = max_position_value / current_price

                # Round quantity appropriately
                if market == 'BIST':
                    quantity = int(quantity)  # Whole shares for BIST
                else:
                    quantity = round(quantity, 6)  # 6 decimals for crypto

                if quantity > 0:
                    can_trade, reason = self.portfolio.can_open_position(market, symbol, current_price, quantity)
                    if can_trade:
                        signal = Signal(
                            timestamp=latest.name.to_pydatetime() if hasattr(latest.name, 'to_pydatetime') else latest.name,
                            market=market,
                            symbol=symbol,
                            signal_type='BUY',
                            price=current_price,
                            reason=f"TRF Long + SALMA Red->Green (TRF_recent={trf_long_recent})",
                            salma_value=latest['salma'],
                            salma_color=salma_color,
                            trf_signal=latest['trf_signal']
                        )
                        signals.append(signal)
                        logger.info(f"BUY SIGNAL: {symbol} @ {current_price:.4f} - {signal.reason}")
                    else:
                        logger.debug(f"Cannot buy {symbol}: {reason}")

        # --- SELL LOGIC ---
        elif has_position and position:
            # Exit condition: SALMA turns green to red
            if salma_green_to_red:
                signal = Signal(
                    timestamp=latest.name.to_pydatetime() if hasattr(latest.name, 'to_pydatetime') else latest.name,
                    market=market,
                    symbol=symbol,
                    signal_type='SELL',
                    price=current_price,
                    reason="SALMA Green->Red",
                    salma_value=latest['salma'],
                    salma_color=salma_color,
                    trf_signal=latest['trf_signal']
                )
                signals.append(signal)
                logger.info(f"SELL SIGNAL: {symbol} @ {current_price:.4f} - {signal.reason}")

        return signals

    def run_analysis(self, markets: List[str] = None) -> Dict[str, List[Signal]]:
        """Run analysis on all configured symbols."""
        if markets is None:
            markets = [m for m, c in self.config.get('markets', {}).items() if c.get('enabled', True)]

        all_signals = {}

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

                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")

        return all_signals

    def execute_signals(self, signals: Dict[str, List[Signal]]) -> List:
        """Execute trading signals."""
        executed = []

        for market, market_signals in signals.items():
            for signal in market_signals:
                if signal.signal_type == 'BUY':
                    # Calculate quantity based on position sizing
                    balance = self.portfolio.get_balance(market)
                    max_pct = self.strategy_config.get('position_sizing', {}).get('max_position_pct', 0.2)
                    max_position_value = balance * max_pct
                    quantity = max_position_value / signal.price

                    if market == 'BIST':
                        quantity = int(quantity)
                    else:
                        quantity = round(quantity, 6)

                    if quantity > 0:
                        trade = self.portfolio.execute_buy(
                            market, signal.symbol, signal.price, quantity
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
        Run backtest on historical data.

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

            salma_color = row['salma_color']
            salma_prev_color = row['salma_prev_color'] if i > 0 else salma_color
            trf_long = row['trf_long']
            trf_long_signal = row['trf_long_signal']
            salma_red_to_green = row['salma_red_to_green']
            salma_green_to_red = row['salma_green_to_red']

            # Check TRF long in recent bars
            trf_long_recent = False
            lookback = 30
            for j in range(max(0, i-lookback+1), i+1):
                if df.iloc[j]['trf_long_signal']:
                    trf_long_recent = True
                    break

            # BUY
            if position is None:
                if (self.require_trf_long and (trf_long == 1) and salma_red_to_green) or \
                   (not self.require_trf_long and salma_red_to_green):

                    # Calculate position size
                    max_pct = self.strategy_config.get('position_sizing', {}).get('max_position_pct', 0.2)
                    max_position_value = balance * max_pct
                    quantity = max_position_value / current_price

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
                                'entry_commission': commission
                            }
                            trades.append({
                                'type': 'BUY',
                                'time': timestamp,
                                'price': current_price,
                                'quantity': quantity,
                                'commission': commission,
                                'balance': balance
                            })

            # SELL
            elif position is not None and salma_green_to_red:
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
                    'balance': balance
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
                'balance': balance
            })

        # Calculate statistics
        completed_trades = [t for t in trades if t['type'] == 'SELL' or t['type'] == 'SELL (EOD)']
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
        print("CURRENT SIGNALS")
        print("="*60)
        for market, market_signals in signals.items():
            for sig in market_signals:
                print(f"{sig.timestamp} | {market} | {sig.symbol} | {sig.signal_type} @ {sig.price:.4f} | {sig.reason}")

        if not signals:
            print("No signals generated.")

    elif args.mode == 'live':
        print("Live trading mode - not implemented in demo")
        print("Use --mode analyze for signal generation")
        print("Use --mode backtest for historical testing")


if __name__ == "__main__":
    main()