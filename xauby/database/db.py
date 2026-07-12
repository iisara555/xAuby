import os
import sqlite3
import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from xauby.storage.interface import IDatabaseRepository
from xauby.domain.models import Candle, Position
from xauby.runtime.paths import runtime_path

logger = logging.getLogger("lite_db")

SCHEMA_VERSION = 11
DEFAULT_DB_PATH = "core/xauby.db"


def resolve_db_path() -> str:
    """Single source of truth for SQLite path.

    Precedence: env ``SQLITE_DB_PATH`` -> instance-scoped runtime root
    (``XAUBY_HOME`` / ``XAUBY_INSTANCE_ID``, default ``core/xauby.db``).
    """
    return os.environ.get("SQLITE_DB_PATH") or runtime_path("xauby.db")


class LiteDB(IDatabaseRepository):
    def __init__(self, db_path: Optional[str] = None, readonly: bool = False):
        self.db_path = db_path or resolve_db_path()
        self.readonly = readonly
        if not readonly:
            self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.readonly:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=10.0,
            )
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000;")
        if self.readonly:
            conn.execute("PRAGMA query_only=ON;")
        else:
            conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def wal_checkpoint(self, mode: str = "TRUNCATE") -> None:
        """Run a WAL checkpoint to prevent unbounded WAL growth."""
        if self.readonly:
            return
        conn = None
        try:
            conn = self._get_connection()
            with conn:
                conn.execute(f"PRAGMA wal_checkpoint({mode});")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")
        finally:
            if conn is not None:
                conn.close()

    def vacuum(self) -> None:
        """Rebuild the database file to reclaim space after large deletes."""
        if self.readonly:
            return
        conn = self._get_connection()
        try:
            prev = conn.isolation_level
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.isolation_level = prev
        except Exception as e:
            logger.warning(f"VACUUM failed: {e}")
        finally:
            conn.close()

    def init_db(self):
        conn = self._get_connection()
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError as e:
                logger.warning(f"WAL pragma set skipped: {e}")

            with conn:
                user_version = conn.execute("PRAGMA user_version;").fetchone()[0]

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        timeframe TEXT NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        UNIQUE(symbol, timeframe, timestamp)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_prices_symbol_timeframe "
                    "ON prices(symbol, timeframe, timestamp DESC)"
                )

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS closed_trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        amount REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        entry_cost REAL NOT NULL,
                        gross_exit REAL NOT NULL,
                        entry_fee REAL DEFAULT 0.0,
                        exit_fee REAL DEFAULT 0.0,
                        total_fees REAL DEFAULT 0.0,
                        net_pnl REAL DEFAULT 0.0,
                        net_pnl_pct REAL DEFAULT 0.0,
                        trigger TEXT,
                        opened_at TEXT,
                        closed_at TEXT,
                        entry_regime TEXT,
                        exit_regime TEXT,
                        strategy_name TEXT
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_closed_trades_symbol "
                    "ON closed_trades(symbol, closed_at DESC)"
                )

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS trade_states (
                        symbol TEXT PRIMARY KEY,
                        state TEXT NOT NULL DEFAULT 'idle',
                        entry_price REAL DEFAULT 0.0,
                        stop_loss REAL DEFAULT 0.0,
                        take_profit REAL DEFAULT 0.0,
                        highest_price_seen REAL DEFAULT 0.0,
                        quantity REAL DEFAULT 0.0,
                        opened_at TEXT,
                        last_transition_at TEXT,
                        stop_loss_order_id TEXT
                        ,position_side TEXT NOT NULL DEFAULT 'LONG'
                        ,leverage REAL NOT NULL DEFAULT 1.0
                        ,margin_mode TEXT NOT NULL DEFAULT 'spot'
                        ,liquidation_price REAL NOT NULL DEFAULT 0.0
                        ,funding_paid REAL NOT NULL DEFAULT 0.0
                        ,management_mode TEXT NOT NULL DEFAULT 'strategy'
                        ,partial_tp_taken INTEGER NOT NULL DEFAULT 0
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        seq INTEGER NOT NULL,
                        ts TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        mode TEXT NOT NULL DEFAULT 'paper',
                        payload TEXT,
                        tick_id TEXT,
                        UNIQUE(run_id, seq)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_events_symbol_ts "
                    "ON events(symbol, ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_events_type "
                    "ON events(event_type, ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_events_run_id "
                    "ON events(run_id, seq ASC)"
                )

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gold_regimes (
                        symbol TEXT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        regime TEXT NOT NULL,
                        trend TEXT NOT NULL,
                        volatility TEXT NOT NULL,
                        macro_bias TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        details_json TEXT,
                        PRIMARY KEY (symbol, timestamp)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_gold_regimes_ts "
                    "ON gold_regimes(timestamp DESC)"
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_gold_regimes_symbol_ts "
                    "ON gold_regimes(symbol, timestamp)"
                )

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS regime_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        old_regime TEXT,
                        new_regime TEXT NOT NULL,
                        candles_confirmed INTEGER DEFAULT 0,
                        strategy_activated TEXT,
                        confidence REAL DEFAULT 0.0
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_regime_history_symbol "
                    "ON regime_history(symbol, timestamp DESC)"
                )
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_handoffs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        old_strategy TEXT,
                        new_strategy TEXT,
                        open_position_id TEXT,
                        notes TEXT
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_strategy_handoffs_symbol "
                    "ON strategy_handoffs(symbol, timestamp DESC)"
                )

                if user_version < 1:
                    try:
                        conn.execute(
                            "ALTER TABLE trade_states ADD COLUMN stop_loss_order_id TEXT;"
                        )
                    except sqlite3.OperationalError:
                        pass

                if user_version < 4:
                    try:
                        conn.execute("ALTER TABLE closed_trades ADD COLUMN entry_regime TEXT;")
                    except sqlite3.OperationalError:
                        pass
                    try:
                        conn.execute("ALTER TABLE closed_trades ADD COLUMN exit_regime TEXT;")
                    except sqlite3.OperationalError:
                        pass

                if user_version < 5:
                    try:
                        conn.execute("ALTER TABLE gold_regimes RENAME TO gold_regimes_old;")
                        conn.execute("""
                            CREATE TABLE gold_regimes (
                                symbol TEXT NOT NULL,
                                timestamp INTEGER NOT NULL,
                                regime TEXT NOT NULL,
                                trend TEXT NOT NULL,
                                volatility TEXT NOT NULL,
                                macro_bias TEXT NOT NULL,
                                confidence REAL NOT NULL,
                                PRIMARY KEY (symbol, timestamp)
                            )
                        """)
                        conn.execute(
                            "INSERT INTO gold_regimes (symbol, timestamp, regime, trend, volatility, macro_bias, confidence) "
                            "SELECT 'XAUTUSDT', timestamp, regime, trend, volatility, macro_bias, confidence FROM gold_regimes_old"
                        )
                        conn.execute("DROP TABLE gold_regimes_old;")
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS ix_gold_regimes_ts "
                            "ON gold_regimes(timestamp DESC)"
                        )
                    except sqlite3.OperationalError:
                        pass

                if user_version < 6:
                    try:
                        conn.execute("ALTER TABLE closed_trades ADD COLUMN strategy_name TEXT;")
                    except sqlite3.OperationalError:
                        pass

                if user_version < 7:
                    try:
                        conn.execute("ALTER TABLE gold_regimes ADD COLUMN details_json TEXT;")
                    except sqlite3.OperationalError:
                        pass

                if user_version < 8:
                    try:
                        conn.execute("ALTER TABLE closed_trades ADD COLUMN execution_mode TEXT;")
                        conn.execute(
                            "UPDATE closed_trades SET execution_mode = 'live' WHERE execution_mode IS NULL"
                        )
                    except sqlite3.OperationalError:
                        pass

                if user_version < 9:
                    for sql in (
                        "ALTER TABLE trade_states ADD COLUMN position_side TEXT NOT NULL DEFAULT 'LONG'",
                        "ALTER TABLE trade_states ADD COLUMN leverage REAL NOT NULL DEFAULT 1.0",
                        "ALTER TABLE trade_states ADD COLUMN margin_mode TEXT NOT NULL DEFAULT 'spot'",
                        "ALTER TABLE trade_states ADD COLUMN liquidation_price REAL NOT NULL DEFAULT 0.0",
                        "ALTER TABLE trade_states ADD COLUMN funding_paid REAL NOT NULL DEFAULT 0.0",
                    ):
                        try:
                            conn.execute(sql)
                        except sqlite3.OperationalError:
                            pass
                    conn.execute(
                        "UPDATE trade_states SET position_side = 'LONG' "
                        "WHERE position_side IS NULL OR position_side = ''"
                    )

                if user_version < 10:
                    try:
                        conn.execute(
                            "ALTER TABLE trade_states ADD COLUMN management_mode "
                            "TEXT NOT NULL DEFAULT 'strategy'"
                        )
                    except sqlite3.OperationalError:
                        pass
                    conn.execute(
                        "UPDATE trade_states SET management_mode = 'strategy' "
                        "WHERE management_mode IS NULL OR management_mode = ''"
                    )

                if user_version < 11:
                    try:
                        conn.execute(
                            "ALTER TABLE trade_states ADD COLUMN partial_tp_taken "
                            "INTEGER NOT NULL DEFAULT 0"
                        )
                    except sqlite3.OperationalError:
                        pass

                if user_version < SCHEMA_VERSION:
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")

            logger.info(f"Database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
        finally:
            conn.close()

    def save_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: List[Candle],
    ) -> None:
        sym = symbol.upper().replace("_", "")
        conn = self._get_connection()
        try:
            processed = []
            for c in candles:
                if isinstance(c, Candle):
                    processed.append((c.timestamp, c.open, c.high, c.low, c.close, c.volume))
                else:
                    processed.append((c[0], c[1], c[2], c[3], c[4], c[5]))

            with conn:
                conn.executemany("""
                    INSERT INTO prices (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        volume=excluded.volume
                """, [(sym, timeframe, c[0], c[1], c[2], c[3], c[4], c[5]) for c in processed])
        except Exception as e:
            logger.error(f"Error saving candles for {sym}: {e}")
        finally:
            conn.close()

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> List[Candle]:
        sym = symbol.upper().replace("_", "")
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, open, high, low, close, volume
                FROM prices
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (sym, timeframe, limit))
            rows = cursor.fetchall()
            rows.reverse()
            return [
                Candle(
                    timestamp=int(r["timestamp"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"])
                )
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting candles for {sym}: {e}")
            return []
        finally:
            conn.close()

    def delete_old_candles(self, symbol: str, timeframe: str, before_timestamp: int):
        sym = symbol.upper().replace("_", "")
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    DELETE FROM prices
                    WHERE symbol = ? AND timeframe = ? AND timestamp < ?
                """, (sym, timeframe, before_timestamp))
        except Exception as e:
            logger.error(f"Error pruning candles for {sym} {timeframe}: {e}")
        finally:
            conn.close()

    @staticmethod
    def _default_trade_state(sym: str) -> Dict[str, Any]:
        return {
            "symbol": sym,
            "state": "idle",
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "highest_price_seen": 0.0,
            "quantity": 0.0,
            "opened_at": None,
            "last_transition_at": None,
            "stop_loss_order_id": None,
            "position_side": "LONG",
            "leverage": 1.0,
            "margin_mode": "spot",
            "liquidation_price": 0.0,
            "funding_paid": 0.0,
            "management_mode": "strategy",
            "partial_tp_taken": 0,
        }

    def get_trade_state(self, symbol: str) -> Position:
        sym = symbol.upper().replace("_", "")
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_states WHERE symbol = ?", (sym,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
            else:
                d = self._default_trade_state(sym)
            return Position(
                symbol=d["symbol"],
                state=d["state"],
                entry_price=float(d.get("entry_price", 0.0) or 0.0),
                stop_loss=float(d.get("stop_loss", 0.0) or 0.0),
                take_profit=float(d.get("take_profit", 0.0) or 0.0),
                highest_price_seen=float(d.get("highest_price_seen", 0.0) or 0.0),
                quantity=float(d.get("quantity", 0.0) or 0.0),
                opened_at=d.get("opened_at"),
                last_transition_at=d.get("last_transition_at"),
                stop_loss_order_id=d.get("stop_loss_order_id"),
                position_side=str(d.get("position_side") or "LONG").upper(),
                leverage=float(d.get("leverage", 1.0) or 1.0),
                margin_mode=str(d.get("margin_mode") or "spot"),
                liquidation_price=float(d.get("liquidation_price", 0.0) or 0.0),
                funding_paid=float(d.get("funding_paid", 0.0) or 0.0),
                management_mode=str(d.get("management_mode") or "strategy").lower(),
                partial_tp_taken=bool(d.get("partial_tp_taken", 0) or 0),
            )
        except Exception as e:
            logger.error(f"Error getting trade state for {sym}: {e}")
            raise
        finally:
            conn.close()

    def save_trade_state(
        self,
        symbol_or_position: Any = None,
        state: Optional[str] = None,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        highest_price_seen: float = 0.0,
        quantity: float = 0.0,
        opened_at: Optional[str] = None,
        last_transition_at: Optional[str] = None,
        stop_loss_order_id: Optional[str] = None,
        position_side: str = "LONG",
        leverage: float = 1.0,
        margin_mode: str = "spot",
        liquidation_price: float = 0.0,
        funding_paid: float = 0.0,
        management_mode: str = "strategy",
        partial_tp_taken: bool = False,
        *,
        symbol: Optional[str] = None,
    ) -> None:
        """Persist the current trade state for a symbol via UPSERT.

        Thread-safety note:
            This method uses a separate connection per call, so the read
            (get_trade_state) and write (save_trade_state) are NOT in the same
            DB transaction. Lost-update safety is guaranteed externally by the
            engine's fcntl exclusive file lock (BaseEngine._acquire_engine_lock),
            which ensures only one engine process runs at a time.  The TUI is
            strictly read-only and never calls this method.
        """
        if isinstance(symbol_or_position, Position):
            pos = symbol_or_position
            symbol = pos.symbol
            state = pos.state
            entry_price = pos.entry_price
            stop_loss = pos.stop_loss
            take_profit = pos.take_profit
            highest_price_seen = pos.highest_price_seen
            quantity = pos.quantity
            opened_at = pos.opened_at
            last_transition_at = pos.last_transition_at
            stop_loss_order_id = pos.stop_loss_order_id
            position_side = pos.position_side
            leverage = pos.leverage
            margin_mode = pos.margin_mode
            liquidation_price = pos.liquidation_price
            funding_paid = pos.funding_paid
            management_mode = pos.management_mode
            partial_tp_taken = pos.partial_tp_taken
        elif symbol_or_position is not None:
            symbol = symbol_or_position
        if symbol is None:
            raise ValueError("save_trade_state requires a symbol or Position")

        sym = symbol.upper().replace("_", "")
        now_str = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        transition_str = last_transition_at or now_str
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO trade_states (
                        symbol, state, entry_price, stop_loss, take_profit,
                        highest_price_seen, quantity, opened_at, last_transition_at,
                        stop_loss_order_id, position_side, leverage, margin_mode,
                        liquidation_price, funding_paid, management_mode,
                        partial_tp_taken
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        state=excluded.state,
                        entry_price=excluded.entry_price,
                        stop_loss=excluded.stop_loss,
                        take_profit=excluded.take_profit,
                        highest_price_seen=excluded.highest_price_seen,
                        quantity=excluded.quantity,
                        opened_at=excluded.opened_at,
                        last_transition_at=excluded.last_transition_at,
                        stop_loss_order_id=excluded.stop_loss_order_id,
                        position_side=excluded.position_side,
                        leverage=excluded.leverage,
                        margin_mode=excluded.margin_mode,
                        liquidation_price=excluded.liquidation_price,
                        funding_paid=excluded.funding_paid,
                        management_mode=excluded.management_mode,
                        partial_tp_taken=excluded.partial_tp_taken
                """, (
                    sym, state, entry_price, stop_loss, take_profit,
                    highest_price_seen, quantity, opened_at, transition_str,
                    stop_loss_order_id, str(position_side or "LONG").upper(),
                    float(leverage or 1.0), str(margin_mode or "spot"),
                    float(liquidation_price or 0.0), float(funding_paid or 0.0),
                    str(management_mode or "strategy").lower(),
                    1 if partial_tp_taken else 0,
                ))
        except Exception as e:
            logger.error(f"Error saving trade state for {sym}: {e}")
        finally:
            conn.close()

    def save_closed_trade(
        self,
        symbol_or_trade: Any,
        side: Optional[str] = None,
        amount: float = 0.0,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        entry_cost: float = 0.0,
        gross_exit: float = 0.0,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        total_fees: float = 0.0,
        net_pnl: float = 0.0,
        net_pnl_pct: float = 0.0,
        trigger: Optional[str] = None,
        opened_at: Optional[str] = None,
        closed_at: Optional[str] = None,
        entry_regime: Optional[str] = None,
        exit_regime: Optional[str] = None,
        strategy_name: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> None:
        if isinstance(symbol_or_trade, dict):
            t = symbol_or_trade
            symbol = t["symbol"]
            side = t["side"]
            amount = t["amount"]
            entry_price = t["entry_price"]
            exit_price = t["exit_price"]
            entry_cost = t["entry_cost"]
            gross_exit = t["gross_exit"]
            entry_fee = t.get("entry_fee", 0.0)
            exit_fee = t.get("exit_fee", 0.0)
            total_fees = t.get("total_fees", 0.0)
            net_pnl = t.get("net_pnl", 0.0)
            net_pnl_pct = t.get("net_pnl_pct", 0.0)
            trigger = t.get("trigger")
            opened_at = t.get("opened_at")
            closed_at = t.get("closed_at")
            entry_regime = t.get("entry_regime")
            exit_regime = t.get("exit_regime")
            strategy_name = t.get("strategy_name")
            execution_mode = t.get("execution_mode")
        else:
            symbol = symbol_or_trade

        sym = symbol.upper().replace("_", "")
        closed_str = closed_at or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        mode = (execution_mode or "live").lower()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO closed_trades (
                        symbol, side, amount, entry_price, exit_price, entry_cost,
                        gross_exit, entry_fee, exit_fee, total_fees, net_pnl,
                        net_pnl_pct, trigger, opened_at, closed_at, entry_regime, exit_regime,
                        strategy_name, execution_mode
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sym, side, amount, entry_price, exit_price, entry_cost,
                    gross_exit, entry_fee, exit_fee, total_fees, net_pnl,
                    net_pnl_pct, trigger, opened_at, closed_str, entry_regime, exit_regime,
                    strategy_name, mode,
                ))
        except Exception as e:
            logger.error(f"Error saving closed trade for {sym}: {e}")
        finally:
            conn.close()

    def close_position_atomic(
        self,
        symbol: str,
        *,
        side: str,
        amount: float,
        entry_price: float,
        exit_price: float,
        entry_cost: float,
        gross_exit: float,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        total_fees: float = 0.0,
        net_pnl: float = 0.0,
        net_pnl_pct: float = 0.0,
        trigger: Optional[str] = None,
        opened_at: Optional[str] = None,
        closed_at: Optional[str] = None,
        entry_regime: Optional[str] = None,
        exit_regime: Optional[str] = None,
        strategy_name: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> bool:
        """Atomically record a closed trade and reset position state to idle."""
        sym = symbol.upper().replace("_", "")
        closed_str = closed_at or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        mode = (execution_mode or "live").lower()
        transition_str = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO closed_trades (
                        symbol, side, amount, entry_price, exit_price, entry_cost,
                        gross_exit, entry_fee, exit_fee, total_fees, net_pnl,
                        net_pnl_pct, trigger, opened_at, closed_at, entry_regime, exit_regime,
                        strategy_name, execution_mode
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sym, side, amount, entry_price, exit_price, entry_cost,
                    gross_exit, entry_fee, exit_fee, total_fees, net_pnl,
                    net_pnl_pct, trigger, opened_at, closed_str, entry_regime, exit_regime,
                    strategy_name, mode,
                ))
                conn.execute("""
                    INSERT INTO trade_states (
                        symbol, state, entry_price, stop_loss, take_profit,
                        highest_price_seen, quantity, opened_at, last_transition_at,
                        stop_loss_order_id, position_side, leverage, margin_mode,
                        liquidation_price, funding_paid, management_mode,
                        partial_tp_taken
                    )
                    VALUES (?, 'idle', 0, 0, 0, 0, 0, NULL, ?, NULL, 'LONG', 1.0, 'spot', 0, 0, 'strategy', 0)
                    ON CONFLICT(symbol) DO UPDATE SET
                        state='idle',
                        entry_price=0,
                        stop_loss=0,
                        take_profit=0,
                        highest_price_seen=0,
                        quantity=0,
                        opened_at=NULL,
                        last_transition_at=excluded.last_transition_at,
                        stop_loss_order_id=NULL,
                        position_side='LONG', leverage=1.0, margin_mode='spot',
                        liquidation_price=0, funding_paid=0, management_mode='strategy',
                        partial_tp_taken=0
                """, (sym, transition_str))
            return True
        except Exception as e:
            logger.error(f"Error in close_position_atomic for {sym}: {e}")
            return False
        finally:
            conn.close()

    def get_closed_trades(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if symbol:
                sym = symbol.upper().replace("_", "")
                cursor.execute("""
                    SELECT * FROM closed_trades WHERE symbol = ? ORDER BY closed_at DESC LIMIT ?
                """, (sym, limit))
            else:
                cursor.execute("""
                    SELECT * FROM closed_trades ORDER BY closed_at DESC LIMIT ?
                """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error getting closed trades: {e}")
            return []
        finally:
            conn.close()

    def count_regime_history(self, symbol: Optional[str] = None) -> int:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if symbol:
                sym = symbol.upper().replace("_", "")
                cursor.execute("SELECT COUNT(*) FROM regime_history WHERE symbol = ?", (sym,))
            else:
                cursor.execute("SELECT COUNT(*) FROM regime_history")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def insert_regime_history(
        self,
        symbol: str,
        *,
        old_regime: str,
        new_regime: str,
        candles_confirmed: int = 0,
        strategy_activated: Optional[str] = None,
        confidence: float = 0.0,
        timestamp: Optional[str] = None,
    ) -> None:
        sym = symbol.upper().replace("_", "")
        ts = timestamp or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO regime_history (
                        timestamp, symbol, old_regime, new_regime,
                        candles_confirmed, strategy_activated, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    ts, sym, old_regime, new_regime,
                    int(candles_confirmed), strategy_activated, float(confidence),
                ))
        except Exception as e:
            logger.error("Error inserting regime_history for %s: %s", sym, e)
        finally:
            conn.close()

    def get_regime_history(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if symbol:
                sym = symbol.upper().replace("_", "")
                cursor.execute(
                    "SELECT * FROM regime_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                    (sym, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Error reading regime_history: %s", e)
            return []
        finally:
            conn.close()

    def insert_strategy_handoff(
        self,
        symbol: str,
        *,
        old_strategy: str,
        new_strategy: str,
        open_position_id: Optional[str] = None,
        notes: str = "",
        timestamp: Optional[str] = None,
    ) -> None:
        sym = symbol.upper().replace("_", "")
        ts = timestamp or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO strategy_handoffs (
                        timestamp, symbol, old_strategy, new_strategy, open_position_id, notes
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (ts, sym, old_strategy, new_strategy, open_position_id, notes))
        except Exception as e:
            logger.error("Error inserting strategy_handoff for %s: %s", sym, e)
        finally:
            conn.close()

    def insert_event(
        self,
        run_id: str,
        seq: int,
        ts: str,
        event_type: str,
        symbol: str,
        mode: str = "paper",
        payload: Optional[str] = None,
        tick_id: Optional[str] = None,
    ) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events
                    (run_id, seq, ts, event_type, symbol, mode, payload, tick_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, seq, ts, event_type, symbol, mode, payload, tick_id),
                )
        except Exception as e:
            logger.error(f"Error inserting event: {e}")
        finally:
            conn.close()

    def delete_events_before(self, cutoff_ts: str) -> int:
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM events WHERE ts < ?",
                    (cutoff_ts,),
                )
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error pruning events before {cutoff_ts}: {e}")
            return 0
        finally:
            conn.close()

    def list_event_runs(
        self,
        limit: int = 20,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List distinct engine runs ordered by latest event (newest first)."""
        conn = self._get_connection()
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if symbol:
                clauses.append("symbol = ?")
                params.append(symbol.upper().replace("_", ""))
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = (
                f"SELECT run_id, symbol, mode, "
                f"MIN(ts) AS started_at, MAX(ts) AS ended_at, "
                f"COUNT(*) AS event_count, MAX(seq) AS max_seq "
                f"FROM events{where} "
                f"GROUP BY run_id "
                f"ORDER BY ended_at DESC "
                f"LIMIT ?"
            )
            params.append(limit)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error listing event runs: {e}")
            return []
        finally:
            conn.close()

    def query_events(
        self,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        run_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
        limit: int = 100,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            clauses: List[str] = []
            params: List[Any] = []
            if symbol:
                clauses.append("symbol = ?")
                params.append(symbol.upper().replace("_", ""))
            if event_type:
                clauses.append("event_type = ?")
                params.append(event_type)
            if run_id:
                clauses.append("run_id = ?")
                params.append(run_id)
            if since_ts:
                clauses.append("ts >= ?")
                params.append(since_ts)
            if until_ts:
                clauses.append("ts <= ?")
                params.append(until_ts)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            order_sql = "ts ASC, seq ASC" if order.lower() == "asc" else "ts DESC, seq DESC"
            sql = (
                f"SELECT run_id, seq, ts, event_type, symbol, mode, payload, tick_id "
                f"FROM events{where} ORDER BY {order_sql} LIMIT ?"
            )
            params.append(limit)
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("payload"):
                    try:
                        import json
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                rows.append(d)
            return rows
        except Exception as e:
            logger.error(f"Error querying events: {e}")
            return []
        finally:
            conn.close()

    def save_gold_regime(
        self,
        symbol: str,
        timestamp: int,
        regime: str,
        trend: str,
        volatility: str,
        macro_bias: str,
        confidence: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = self._get_connection()
        try:
            details_json = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
            with conn:
                conn.execute("""
                    INSERT INTO gold_regimes (symbol, timestamp, regime, trend, volatility, macro_bias, confidence, details_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        regime=excluded.regime,
                        trend=excluded.trend,
                        volatility=excluded.volatility,
                        macro_bias=excluded.macro_bias,
                        confidence=excluded.confidence,
                        details_json=excluded.details_json
                """, (symbol, timestamp, regime, trend, volatility, macro_bias, confidence, details_json))
        except Exception as e:
            logger.error(f"Error saving gold regime: {e}")
        finally:
            conn.close()

    def get_latest_gold_regime(self, symbol: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, timestamp, regime, trend, volatility, macro_bias, confidence, details_json
                FROM gold_regimes
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            raw_details = data.pop("details_json", None)
            if raw_details:
                try:
                    data["details"] = json.loads(raw_details)
                except Exception:
                    data["details"] = {}
            else:
                data["details"] = {}
            return data
        except Exception as e:
            logger.error(f"Error getting latest gold regime: {e}")
            return None
        finally:
            conn.close()

    def get_regime_at(self, ts_iso: str, symbol: str) -> str:
        try:
            ds = ts_iso.replace("Z", "").split(".")[0]
            dt = datetime.fromisoformat(ds)
            epoch = int(dt.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            return "NORMAL"
        
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT regime FROM gold_regimes
                WHERE symbol = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol, epoch))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            cursor.execute("""
                SELECT regime FROM gold_regimes
                WHERE symbol = ?
                ORDER BY abs(timestamp - ?) ASC
                LIMIT 1
            """, (symbol, epoch))
            row = cursor.fetchone()
            return row[0] if row else "NORMAL"
        except Exception as e:
            logger.error(f"Error getting regime at {ts_iso}: {e}")
            return "NORMAL"
        finally:
            conn.close()
