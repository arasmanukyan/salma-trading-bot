"""
Portfolio Management Module
Handles SQLite database, balance tracking, trade execution, and Telegram notifications.
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
import pytz
import requests
import re

from formatting import fmt_price

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Trade record."""
    id: Optional[int]
    timestamp: str
    market: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: float
    price: float
    commission: float
    net_amount: float
    balance_after: float
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class Position:
    """Current position."""
    market: str
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    highest_price: float = 0.0  # For trailing stop
    stop_loss: float = 0.0
    take_profit: float = 0.0


class PortfolioManager:
    """Manages portfolio, trades, and balances with SQLite persistence."""

    def __init__(self, config: dict):
        self.config = config
        self.db_path = config.get('database', {}).get('path', 'trading_bot.db')
        self.timezone = pytz.timezone(config.get('general', {}).get('timezone', 'Europe/Istanbul'))

        # Market configurations
        self.markets = config.get('markets', {})

        # Telegram config
        self.telegram_config = config.get('telegram', {})
        self.telegram_token = None
        self.telegram_chat_id = None
        self._load_telegram_config()

        # Initialize database
        self._init_database()

        # Load or create balances
        self._load_balances()

    def _load_telegram_config(self):
        """Load Telegram bot token and chat_id from config file or config.json."""
        # First try the external config file
        config_path = self.telegram_config.get('config_path', '')
        if config_path:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Extract token and chat_id from the Python file
                    token_match = re.search(r'["\']?bot_token["\']?\s*[:=]\s*["\']([^"\']+)["\']', content)
                    chat_match = re.search(r'["\']?chat_id["\']?\s*[:=]\s*["\']([^"\']+)["\']', content)

                    if token_match:
                        self.telegram_token = token_match.group(1)
                    if chat_match:
                        self.telegram_chat_id = chat_match.group(1)

                logger.info("Loaded Telegram config from external file")
            except Exception as e:
                logger.warning(f"Could not load Telegram config from {config_path}: {e}")

        # Fallback to config.json
        if not self.telegram_token:
            self.telegram_token = self.telegram_config.get('fallback_token', '')
        if not self.telegram_chat_id:
            self.telegram_chat_id = self.telegram_config.get('fallback_chat_id', '')

        if self.telegram_token and self.telegram_chat_id:
            logger.info("Telegram notifications enabled")
        else:
            logger.warning("Telegram not configured - notifications disabled")

    def _init_database(self):
        """Initialize SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Trades table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    net_amount REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    pnl REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0
                )
            ''')

            # Balances table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS balances (
                    market TEXT PRIMARY KEY,
                    currency TEXT NOT NULL,
                    initial_balance REAL NOT NULL,
                    current_balance REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')

            # Positions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    highest_price REAL DEFAULT 0,
                    UNIQUE(market, symbol)
                )
            ''')

            # Migration: add highest_price column if not exists
            cursor.execute("PRAGMA table_info(positions)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'highest_price' not in columns:
                cursor.execute('ALTER TABLE positions ADD COLUMN highest_price REAL DEFAULT 0')
                logger.info("Migrated database: added highest_price column to positions table")

            # Daily stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    starting_balance REAL NOT NULL,
                    ending_balance REAL NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    gross_pnl REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0
                )
            ''')

            conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    def _load_balances(self):
        """Load balances from database or create initial ones."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT market, currency, initial_balance, current_balance FROM balances')
            rows = cursor.fetchall()

            self.balances = {}
            for market, currency, initial, current in rows:
                self.balances[market] = {
                    'currency': currency,
                    'initial_balance': initial,
                    'current_balance': current
                }

            # Create missing balances from config
            for market, mconfig in self.markets.items():
                if market not in self.balances:
                    initial = mconfig.get('initial_balance', 0)
                    currency = mconfig.get('currency', 'USD')
                    self.balances[market] = {
                        'currency': currency,
                        'initial_balance': initial,
                        'current_balance': initial
                    }
                    self._save_balance(market)

            logger.info(f"Loaded balances: {self.balances}")

    def _save_balance(self, market: str):
        """Save balance to database."""
        bal = self.balances[market]
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO balances (market, currency, initial_balance, current_balance, updated_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (market, bal['currency'], bal['initial_balance'], bal['current_balance'],
                  datetime.now(self.timezone).isoformat()))
            conn.commit()

    def get_balance(self, market: str) -> float:
        """Get current balance for a market."""
        return self.balances.get(market, {}).get('current_balance', 0.0)

    def get_initial_balance(self, market: str) -> float:
        """Get initial balance for a market."""
        return self.balances.get(market, {}).get('initial_balance', 0.0)

    def calculate_commission(self, market: str, amount: float) -> float:
        """Calculate commission for a trade."""
        mconfig = self.markets.get(market, {})
        rate = mconfig.get('commission_rate', 0.001)
        min_comm = mconfig.get('min_commission', 0.0)
        commission = max(amount * rate, min_comm)
        return round(commission, 2)

    def can_open_position(self, market: str, symbol: str, price: float, quantity: float) -> Tuple[bool, str]:
        """Check if we can open a new position."""
        # Check if position already exists
        if self.has_position(market, symbol):
            return False, f"Position already exists for {symbol}"

        # Check balance
        cost = price * quantity
        commission = self.calculate_commission(market, cost)
        total_cost = cost + commission
        balance = self.get_balance(market)

        if total_cost > balance:
            return False, f"Insufficient balance: need {total_cost:.2f}, have {balance:.2f}"

        # Check max positions
        max_positions = self.config.get('strategy', {}).get('position_sizing', {}).get('max_positions', 5)
        current_positions = len(self.get_open_positions(market))
        if current_positions >= max_positions:
            return False, f"Max positions ({max_positions}) reached"

        # Check max position size
        max_pct = self.config.get('strategy', {}).get('position_sizing', {}).get('max_position_pct', 0.2)
        max_position_value = balance * max_pct
        if cost > max_position_value:
            return False, f"Position size {cost:.2f} exceeds max {max_pct*100}% of balance ({max_position_value:.2f})"

        return True, "OK"

    def execute_buy(self, market: str, symbol: str, price: float, quantity: float,
                    stop_loss: float = None, take_profit: float = None) -> Optional[Trade]:
        """Execute a buy order."""
        can_trade, reason = self.can_open_position(market, symbol, price, quantity)
        if not can_trade:
            logger.warning(f"Cannot buy {symbol}: {reason}")
            return None

        cost = price * quantity
        commission = self.calculate_commission(market, cost)
        net_amount = cost + commission

        # Update balance
        self.balances[market]['current_balance'] -= net_amount
        self._save_balance(market)

        # Record trade
        trade = Trade(
            id=None,
            timestamp=datetime.now(self.timezone).isoformat(),
            market=market,
            symbol=symbol,
            side='BUY',
            quantity=quantity,
            price=price,
            commission=commission,
            net_amount=net_amount,
            balance_after=self.balances[market]['current_balance']
        )

        trade_id = self._save_trade(trade)
        trade.id = trade_id

        # Save position
        self._save_position(market, symbol, quantity, price, stop_loss, take_profit)

        # Send Telegram notification
        self._send_telegram_notification(trade, pnl=0)

        logger.info(f"BUY executed: {symbol} x{quantity} @ {price} (commission: {commission})")
        return trade

    def execute_sell(self, market: str, symbol: str, price: float, quantity: float = None) -> Optional[Trade]:
        """Execute a sell order (closes position)."""
        position = self.get_position(market, symbol)
        if not position:
            logger.warning(f"No position to sell for {symbol}")
            return None

        # Determine quantity to sell
        if quantity is None or quantity >= position.quantity:
            quantity = position.quantity
            close_full = True
        else:
            close_full = False

        proceeds = price * quantity
        commission = self.calculate_commission(market, proceeds)
        net_amount = proceeds - commission

        # Calculate PnL
        entry_price = position.entry_price
        pnl = (price - entry_price) * quantity
        pnl_pct = ((price - entry_price) / entry_price) * 100

        # Update balance
        self.balances[market]['current_balance'] += net_amount
        self._save_balance(market)

        # Record trade
        trade = Trade(
            id=None,
            timestamp=datetime.now(self.timezone).isoformat(),
            market=market,
            symbol=symbol,
            side='SELL',
            quantity=quantity,
            price=price,
            commission=commission,
            net_amount=net_amount,
            balance_after=self.balances[market]['current_balance'],
            pnl=pnl,
            pnl_pct=pnl_pct
        )

        trade_id = self._save_trade(trade)
        trade.id = trade_id

        # Update or remove position
        if close_full:
            self._remove_position(market, symbol)
        else:
            self._update_position_quantity(market, symbol, position.quantity - quantity)

        # Send Telegram notification
        self._send_telegram_notification(trade, pnl)

        logger.info(f"SELL executed: {symbol} x{quantity} @ {price} (PnL: {pnl:.2f}, {pnl_pct:.2f}%)")
        return trade

    def _save_trade(self, trade: Trade) -> int:
        """Save trade to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (timestamp, market, symbol, side, quantity, price,
                                  commission, net_amount, balance_after, pnl, pnl_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (trade.timestamp, trade.market, trade.symbol, trade.side,
                  trade.quantity, trade.price, trade.commission, trade.net_amount,
                  trade.balance_after, trade.pnl, trade.pnl_pct))
            conn.commit()
            return cursor.lastrowid

    def _save_position(self, market: str, symbol: str, quantity: float, entry_price: float,
                       stop_loss: float = None, take_profit: float = None, highest_price: float = 0.0):
        """Save position to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO positions (market, symbol, quantity, entry_price, entry_time, stop_loss, take_profit, highest_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (market, symbol, quantity, entry_price,
                  datetime.now(self.timezone).isoformat(), stop_loss, take_profit, highest_price))
            conn.commit()

    def _update_position_quantity(self, market: str, symbol: str, new_quantity: float):
        """Update position quantity."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE positions SET quantity = ? WHERE market = ? AND symbol = ?
            ''', (new_quantity, market, symbol))
            conn.commit()

    def update_position_highest_price(self, market: str, symbol: str, highest_price: float):
        """Update position highest price for trailing stop."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE positions SET highest_price = ? WHERE market = ? AND symbol = ?
            ''', (highest_price, market, symbol))
            conn.commit()

    def _remove_position(self, market: str, symbol: str):
        """Remove position from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM positions WHERE market = ? AND symbol = ?', (market, symbol))
            conn.commit()

    def get_position(self, market: str, symbol: str) -> Optional[Position]:
        """Get current position for a symbol."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT quantity, entry_price, entry_time, stop_loss, take_profit, highest_price
                FROM positions WHERE market = ? AND symbol = ?
            ''', (market, symbol))
            row = cursor.fetchone()

            if row:
                quantity, entry_price, entry_time, stop_loss, take_profit, highest_price = row
                return Position(
                    market=market,
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=entry_price,
                    current_price=entry_price,  # Will be updated externally
                    unrealized_pnl=0,
                    unrealized_pnl_pct=0,
                    highest_price=highest_price or 0.0,
                    stop_loss=stop_loss or 0.0,
                    take_profit=take_profit or 0.0
                )
        return None

    def has_position(self, market: str, symbol: str) -> bool:
        """Check if position exists."""
        return self.get_position(market, symbol) is not None

    def get_open_positions(self, market: str) -> List[Position]:
        """Get all open positions for a market."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT symbol, quantity, entry_price, entry_time, stop_loss, take_profit, highest_price
                FROM positions WHERE market = ?
            ''', (market,))
            rows = cursor.fetchall()

            positions = []
            for row in rows:
                symbol, quantity, entry_price, entry_time, stop_loss, take_profit, highest_price = row
                positions.append(Position(
                    market=market,
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=entry_price,
                    current_price=entry_price,
                    unrealized_pnl=0,
                    unrealized_pnl_pct=0,
                    highest_price=highest_price or 0.0,
                    stop_loss=stop_loss or 0.0,
                    take_profit=take_profit or 0.0
                ))
            return positions

    def update_position_prices(self, market: str, prices: Dict[str, float]):
        """Update current prices for positions and calculate unrealized PnL."""
        positions = self.get_open_positions(market)
        for pos in positions:
            if pos.symbol in prices:
                pos.current_price = prices[pos.symbol]
                pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.quantity
                pos.unrealized_pnl_pct = ((pos.current_price - pos.entry_price) / pos.entry_price) * 100

    def get_trade_history(self, market: str = None, limit: int = 100) -> List[Trade]:
        """Get trade history."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if market:
                cursor.execute('''
                    SELECT id, timestamp, market, symbol, side, quantity, price,
                           commission, net_amount, balance_after, pnl, pnl_pct
                    FROM trades WHERE market = ? ORDER BY timestamp DESC LIMIT ?
                ''', (market, limit))
            else:
                cursor.execute('''
                    SELECT id, timestamp, market, symbol, side, quantity, price,
                           commission, net_amount, balance_after, pnl, pnl_pct
                    FROM trades ORDER BY timestamp DESC LIMIT ?
                ''', (limit,))

            rows = cursor.fetchall()
            trades = []
            for row in rows:
                trades.append(Trade(*row))
            return trades

    def get_portfolio_summary(self) -> Dict:
        """Get complete portfolio summary."""
        summary = {}
        total_initial = 0
        total_current = 0

        for market in self.balances:
            bal = self.balances[market]
            positions = self.get_open_positions(market)

            position_value = sum(p.quantity * p.current_price for p in positions)
            unrealized_pnl = sum(p.unrealized_pnl for p in positions)

            total_initial += bal['initial_balance']
            total_current += bal['current_balance'] + position_value

            summary[market] = {
                'currency': bal['currency'],
                'initial_balance': bal['initial_balance'],
                'cash_balance': bal['current_balance'],
                'position_value': position_value,
                'total_value': bal['current_balance'] + position_value,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': (bal['current_balance'] + position_value) - bal['initial_balance'],
                'total_pnl_pct': ((bal['current_balance'] + position_value) - bal['initial_balance']) / bal['initial_balance'] * 100,
                'open_positions': len(positions)
            }

        summary['TOTAL'] = {
            'initial_balance': total_initial,
            'current_value': total_current,
            'total_pnl': total_current - total_initial,
            'total_pnl_pct': (total_current - total_initial) / total_initial * 100 if total_initial > 0 else 0
        }

        return summary

    def _send_telegram_notification(self, trade: Trade, pnl: float = 0):
        """Send trade notification to Telegram."""
        if not self.telegram_token or not self.telegram_chat_id:
            return

        if not self.telegram_config.get('enabled', True):
            return

        try:
            bal = self.balances[trade.market]
            currency = bal['currency']

            if trade.side == 'BUY':
                emoji = "🟢"
                action = "ALIM"
                pnl_text = ""
            else:
                emoji = "🔴" if pnl < 0 else "🟢"
                action = "SATIM"
                pnl_text = f"\n💰 <b>Net Kâr/Zarar:</b> {pnl:+.2f} {currency} ({trade.pnl_pct:+.2f}%)"

            message = f"""{emoji} <b>{action} İşlemi</b>

📊 <b>Sembol:</b> {trade.symbol}
📈 <b>Yön:</b> {trade.side}
📦 <b>Miktar:</b> {trade.quantity:.6f}
💵 <b>Fiyat:</b> {fmt_price(trade.price)} {currency}
💸 <b>Komisyon:</b> {trade.commission:.2f} {currency}
{pnl_text}
💼 <b>Güncel Bakiye:</b> {trade.balance_after:.2f} {currency}
🕐 <b>Zaman:</b> {trade.timestamp[:19]}"""

            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"Telegram notification sent for {trade.symbol} {trade.side}")
            else:
                logger.warning(f"Telegram notification failed: {response.text}")

        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}")

    def send_summary_message(self):
        """Send portfolio summary to Telegram."""
        if not self.telegram_token or not self.telegram_chat_id:
            return

        try:
            summary = self.get_portfolio_summary()

            message = "📊 <b>Portföy Özeti</b>\n\n"

            for market, data in summary.items():
                if market == 'TOTAL':
                    continue
                pnl_emoji = "🟢" if data['total_pnl'] >= 0 else "🔴"
                message += f"{pnl_emoji} <b>{market} ({data['currency']})</b>\n"
                message += f"  Başlangıç: {data['initial_balance']:,.2f}\n"
                message += f"  Nakit: {data['cash_balance']:,.2f}\n"
                message += f"  Pozisyon: {data['position_value']:,.2f}\n"
                message += f"  Toplam: {data['total_value']:,.2f}\n"
                message += f"  K/Z: {data['total_pnl']:+,.2f} ({data['total_pnl_pct']:+.2f}%)\n"
                message += f"  Açık Poz: {data['open_positions']}\n\n"

            total = summary['TOTAL']
            total_emoji = "🟢" if total['total_pnl'] >= 0 else "🔴"
            message += f"{total_emoji} <b>GENEL TOPLAM</b>\n"
            message += f"  Başlangıç: {total['initial_balance']:,.2f}\n"
            message += f"  Değer: {total['current_value']:,.2f}\n"
            message += f"  K/Z: {total['total_pnl']:+,.2f} ({total['total_pnl_pct']:+.2f}%)"

            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                'chat_id': self.telegram_chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            requests.post(url, json=payload, timeout=10)
            logger.info("Portfolio summary sent to Telegram")

        except Exception as e:
            logger.error(f"Error sending summary: {e}")


if __name__ == "__main__":
    # Test portfolio manager
    import json

    with open('config.json', 'r') as f:
        config = json.load(f)

    portfolio = PortfolioManager(config)

    print("Portfolio Summary:")
    summary = portfolio.get_portfolio_summary()
    print(json.dumps(summary, indent=2, default=str))

    # Test buy
    print("\nTesting BUY...")
    trade = portfolio.execute_buy('BIST', 'THYAO.IS', 100.0, 100)
    if trade:
        print(f"Trade: {trade}")

    print("\nPortfolio after buy:")
    print(json.dumps(portfolio.get_portfolio_summary(), indent=2, default=str))

    # Test sell
    print("\nTesting SELL...")
    trade = portfolio.execute_sell('BIST', 'THYAO.IS', 105.0)
    if trade:
        print(f"Trade: {trade}")

    print("\nPortfolio after sell:")
    print(json.dumps(portfolio.get_portfolio_summary(), indent=2, default=str))