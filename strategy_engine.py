from datetime import datetime, timezone
import pandas as pd
import logging
import os

from indicator import (
    compute_atr, 
    compute_emas, 
    get_trend, 
    is_bearish_wickless, 
    is_bullish_wickless
)

from data_extraction import is_london_or_ny_session
from telegram_bot import TelegramSignalBot
from trade_executor import execute_multi_account_trades

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# AUTOMATED EXECUTION SETTINGS
LOT_SIZE           = float(os.getenv("LOT_SIZE", "0.01"))
MAGIC_NUMBER       = 888999

WICK_TOLERANCE_PCT = 0.05
ATR_PERIOD         = 14
SL_ATR_MULT        = 1.0

MAX_ZONE_AGE       = 8

EMA_FAST           = 20               
EMA_SLOW           = 50               
NEUTRAL_BAND_PCT   = 0.05             

# DATA STRUCTURES
class Zone:
    def __init__(self, price: float, direction: str, bar_idx: int, atr: float, source_ts):
        self.price     = price
        self.direction = direction
        self.bar_idx   = bar_idx
        self.atr       = atr
        self.source_ts = source_ts
        self.active    = True

    def age(self, current_idx: int) -> int:
        return current_idx - self.bar_idx

class Trade:
    def __init__(self, symbol, direction, entry, sl, tp, timestamp, trend, telegram_targets=None):
        self.symbol           = symbol
        self.direction        = direction
        self.entry            = entry
        self.sl               = sl
        self.tp               = tp
        self.open_time        = timestamp
        self.close_time       = None
        self.result           = None
        self.pnl_pts          = None
        self.trend            = trend
        self.telegram_targets = telegram_targets or ["3"]

    def close(self, price, timestamp, reason):
        self.close_time = timestamp
        self.result     = reason
        self.pnl_pts    = (price - self.entry) if self.direction == "long" else (self.entry - price)
        return self

    def __repr__(self):
        tag = f"{self.result} {self.pnl_pts:+.2f}pts" if self.result else "OPEN"
        return (f"Trade({self.symbol} {self.direction.upper()} [{self.trend}] "
                f"entry={self.entry:.2f} sl={self.sl:.2f} tp={self.tp:.2f} | {tag})")

# STRATEGY ENGINE
class WicklessCandleBot:
    def __init__(self, symbol: str, telegram_bot: TelegramSignalBot = None):
        self.symbol           = symbol
        self.zones            : list[Zone]  = []
        self.open_trade       : Trade | None = None
        self.closed_trades    : list[Trade] = []
        self._known_zone_keys : set         = set()
        self.live_mode        = False
        self.telegram         = telegram_bot

        self.daily_losses     = 0
        self.last_loss_date   = datetime.now(timezone.utc).date()

    def backtest(self, df: pd.DataFrame):
        """Replay historical closed candles to estimate strategy performance."""
        log.info("=" * 60)
        log.info("[%s] YTD BACK-TEST %d candles (%s → %s)", self.symbol, len(df), df.index[0], df.index[-1])
        log.info("Trend filter : EMA%d/EMA%d neutral band=%.2f%% MAX_ZONE_AGE=%d",
                  EMA_FAST, EMA_SLOW, NEUTRAL_BAND_PCT, MAX_ZONE_AGE)
        log.info("=" * 60)

        atr = compute_atr(df, ATR_PERIOD)
        ema_fast, ema_slow = compute_emas(df)

        bt_zones: list[Zone] = []
        bt_open_trade: Trade | None = None
        bt_closed_trades: list[Trade] = []

        for i, (ts, row) in enumerate(df.iterrows()):
            ef = ema_fast.iloc[i]
            es = ema_slow.iloc[i]
            trend = get_trend(ef, es, row["close"])

            if bt_open_trade:
                t = bt_open_trade
                hit = None
                if t.direction == "long":
                    if row["low"] <= t.sl:
                        hit = ("LOSS", t.sl)
                    elif row["high"] >= t.tp:
                        hit = ("WIN", t.tp)
                else:
                    if row["high"] >= t.sl:
                        hit = ("LOSS", t.sl)
                    elif row["low"] <= t.tp:
                        hit = ("WIN", t.tp)
                if hit:
                    result, price = hit
                    t.close(price, ts, result)
                    bt_closed_trades.append(t)
                    bt_open_trade = None

            a = atr.iloc[i]
            if is_bullish_wickless(row, WICK_TOLERANCE_PCT):
                bt_zones.append(Zone(row["open"], "long", i, a, ts))
            if is_bearish_wickless(row, WICK_TOLERANCE_PCT):
                bt_zones.append(Zone(row["open"], "short", i, a, ts))

            if not bt_open_trade:
                for zone in bt_zones:
                    if not zone.active:
                        continue
                    if zone.age(i) > MAX_ZONE_AGE:
                        zone.active = False
                        continue
                    if zone.direction == "long" and trend == "down":
                        continue
                    if zone.direction == "short" and trend == "up":
                        continue

                    if zone.direction == "long" and row["low"] <= zone.price:
                        sl = zone.price - zone.atr * SL_ATR_MULT
                        tp = zone.price + (zone.price - sl)
                        bt_open_trade = Trade(self.symbol, "long", zone.price, sl, tp, ts, trend)
                        zone.active = False
                        break
                    elif zone.direction == "short" and row["high"] >= zone.price:
                        sl = zone.price + zone.atr * SL_ATR_MULT
                        tp = zone.price - (sl - zone.price)
                        bt_open_trade = Trade(self.symbol, "short", zone.price, sl, tp, ts, trend)
                        zone.active = False
                        break

        trades = bt_closed_trades
        if not trades:
            log.info("[%s] No closed trades in the YTD backtest window.", self.symbol)
            log.info("=" * 60)
            return

        wins = [t for t in trades if t.result == "WIN"]
        losses = [t for t in trades if t.result == "LOSS"]
        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t.pnl_pts for t in trades)
        avg_win = sum(t.pnl_pts for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pts for t in losses) / len(losses) if losses else 0.0

        log.info("[%s] YTD Total trades : %d", self.symbol, len(trades))
        log.info("[%s] YTD Wins         : %d", self.symbol, len(wins))
        log.info("[%s] YTD Losses       : %d", self.symbol, len(losses))
        log.info("[%s] YTD Win rate     : %.1f %%", self.symbol, win_rate)
        log.info("[%s] YTD Total PnL    : %+.2f pts", self.symbol, total_pnl)
        log.info("[%s] YTD Avg win      : %+.2f pts", self.symbol, avg_win)
        log.info("[%s] YTD Avg loss     : %+.2f pts", self.symbol, avg_loss)
        log.info("=" * 60)

    def _reset_daily_losses_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today > self.last_loss_date:
            self.daily_losses = 0
            self.last_loss_date = today

    def _check_exits_live(self, live_price: float, ts):
        if not self.open_trade:
            return
        
        self._reset_daily_losses_if_new_day()
        t = self.open_trade

        if t.direction == "long":
            if live_price <= t.sl:
                self.open_trade = None
                t.close(t.sl, ts, "LOSS")
                self.closed_trades.append(t)
                self.daily_losses += 1
                log.info("❌ [%s] LONG STOPPED OUT | Exit: %.2f | %s", self.symbol, t.sl, t)
                if self.telegram and self.live_mode:
                    self.telegram.notify_trade_result(
                        symbol=self.symbol, direction=t.direction, result=t.result, pnl_pts=t.pnl_pts,
                        entry=t.entry, exit_price=t.sl, trend=t.trend, targets=t.telegram_targets
                    )
            elif live_price >= t.tp:
                self.open_trade = None
                t.close(t.tp, ts, "WIN")
                self.closed_trades.append(t)
                log.info("✅ [%s] LONG TARGET HIT | Exit: %.2f | %s", self.symbol, t.tp, t)
                if self.telegram and self.live_mode:
                    self.telegram.notify_trade_result(
                        symbol=self.symbol, direction=t.direction, result=t.result, pnl_pts=t.pnl_pts,
                        entry=t.entry, exit_price=t.tp, trend=t.trend, targets=t.telegram_targets
                    )
        else:  # short
            if live_price >= t.sl:
                self.open_trade = None
                t.close(t.sl, ts, "LOSS")
                self.closed_trades.append(t)
                self.daily_losses += 1
                log.info("❌ [%s] SHORT STOPPED OUT | Exit: %.2f | %s", self.symbol, t.sl, t)
                if self.telegram and self.live_mode:
                    self.telegram.notify_trade_result(
                        symbol=self.symbol, direction=t.direction, result=t.result, pnl_pts=t.pnl_pts,
                        entry=t.entry, exit_price=t.sl, trend=t.trend, targets=t.telegram_targets
                    )
            elif live_price <= t.tp:
                self.open_trade = None
                t.close(t.tp, ts, "WIN")
                self.closed_trades.append(t)
                log.info("✅ [%s] SHORT TARGET HIT | Exit: %.2f | %s", self.symbol, t.tp, t)
                if self.telegram and self.live_mode:
                    self.telegram.notify_trade_result(
                        symbol=self.symbol, direction=t.direction, result=t.result, pnl_pts=t.pnl_pts,
                        entry=t.entry, exit_price=t.tp, trend=t.trend, targets=t.telegram_targets
                    )

    def _check_entries_live(self, live_price: float, ts, closed_df: pd.DataFrame, trend: str, news_blocked: bool):
        if self.open_trade or not self.live_mode or len(closed_df) == 0:
            return

        self._reset_daily_losses_if_new_day()

        if news_blocked:
            log.info("[%s] Entry blocked: High Impact News Filter active", self.symbol)
            return

        latest_pos = len(closed_df) - 1

        for zone in self.zones:
            if not zone.active:
                continue

            try:
                zone_pos = closed_df.index.get_loc(zone.source_ts)
                age = latest_pos - zone_pos
            except KeyError:
                zone.active = False
                continue

            if age > MAX_ZONE_AGE:
                zone.active = False
                log.info("[%s] 🚫 Zone at %.2f expired (Age: %d candles)", self.symbol, zone.price, age)
                continue

            if zone.direction == "long" and trend == "down":
                continue
            if zone.direction == "short" and trend == "up":
                continue

            # Check Long Setup
            if zone.direction == "long" and live_price <= zone.price:
                sl = zone.price - zone.atr * SL_ATR_MULT
                tp = zone.price + (zone.price - sl)
                
                # Determine Telegram destinations
                telegram_targets = ["3"]  # Auto-trade channel 3 always receives GOLD & SILVER trades
                if self.symbol.upper() == "GOLD":
                    if is_london_or_ny_session() and self.daily_losses < 2:
                        telegram_targets.extend(["1", "2"])
                    else:
                        if not is_london_or_ny_session():
                            log.info("[%s] Skipping Telegram Channels 1 & 2: Outside London/NY session", self.symbol)
                        if self.daily_losses >= 2:
                            log.info("[%s] Skipping Telegram Channels 1 & 2: Daily 2-loss limit reached", self.symbol)
                else:
                    log.info("[%s] Skipping Telegram Channels 1 & 2: Only GOLD trades are sent to Channels 1 & 2", self.symbol)

                self.open_trade = Trade(self.symbol, "long", zone.price, sl, tp, ts, trend, telegram_targets=telegram_targets)
                zone.active = False
                log.info("[%s] 🟢 REALTIME LONG ENTRY [trend=%s] Live=%.2f Entry=%.2f SL=%.2f TP=%.2f",
                         self.symbol, trend, live_price, zone.price, sl, tp)
                
                # 1. ALWAYS execute on MT5 Live Account (GOLD only)
                if self.symbol.upper() == "GOLD":
                    execute_multi_account_trades(self.symbol, "long", LOT_SIZE, sl, tp)
                else:
                    log.info("[%s] Skipping MT5 Auto-Trade Execution: Only GOLD is enabled for automated trading", self.symbol)

                # 2. Dispatch Telegram Signal
                if self.telegram:
                    self.telegram.notify_signal(
                        symbol=self.symbol, direction="long", entry=zone.price, sl=sl, tp=tp, trend=trend, timestamp=ts, targets=telegram_targets
                    )
                break

            # Check Short Setup
            elif zone.direction == "short" and live_price >= zone.price:
                sl = zone.price + zone.atr * SL_ATR_MULT
                tp = zone.price - (sl - zone.price)
                
                # Determine Telegram destinations
                telegram_targets = ["3"]  # Auto-trade channel 3 always receives GOLD & SILVER trades
                if self.symbol.upper() == "GOLD":
                    if is_london_or_ny_session() and self.daily_losses < 2:
                        telegram_targets.extend(["1", "2"])
                    else:
                        if not is_london_or_ny_session():
                            log.info("[%s] Skipping Telegram Channels 1 & 2: Outside London/NY session", self.symbol)
                        if self.daily_losses >= 2:
                            log.info("[%s] Skipping Telegram Channels 1 & 2: Daily 2-loss limit reached", self.symbol)
                else:
                    log.info("[%s] Skipping Telegram Channels 1 & 2: Only GOLD trades are sent to Channels 1 & 2", self.symbol)

                self.open_trade = Trade(self.symbol, "short", zone.price, sl, tp, ts, trend, telegram_targets=telegram_targets)
                zone.active = False
                log.info("[%s] 🔴 REALTIME SHORT ENTRY [trend=%s] Live=%.2f Entry=%.2f SL=%.2f TP=%.2f",
                         self.symbol, trend, live_price, zone.price, sl, tp)
                
                # 1. ALWAYS execute on MT5 Live Account (GOLD only)
                if self.symbol.upper() == "GOLD":
                    execute_multi_account_trades(self.symbol, "short", LOT_SIZE, sl, tp)
                else:
                    log.info("[%s] Skipping MT5 Auto-Trade Execution: Only GOLD is enabled for automated trading", self.symbol)

                # 2. Dispatch Telegram Signal
                if self.telegram:
                    self.telegram.notify_signal(
                        symbol=self.symbol, direction="short", entry=zone.price, sl=sl, tp=tp, trend=trend, timestamp=ts, targets=telegram_targets
                    )
                break

    def _expire_stale_zones(self, closed_df: pd.DataFrame):
        if len(closed_df) == 0:
            return
        latest_pos = len(closed_df) - 1
        for zone in self.zones:
            if not zone.active:
                continue
            try:
                zone_pos = closed_df.index.get_loc(zone.source_ts)
            except KeyError:
                zone.active = False
                continue
            if (latest_pos - zone_pos) > MAX_ZONE_AGE:
                zone.active = False

    def process_latest(self, df: pd.DataFrame, live_price: float, news_blocked: bool = False):
        atr = compute_atr(df, ATR_PERIOD)
        ema_fast, ema_slow = compute_emas(df)
        
        closed_df = df.iloc[:-1]
        latest_idx = len(closed_df) - 1

        for i, (ts, row) in enumerate(closed_df.iterrows()):
            a = atr.iloc[i]
            # Convert timestamp to a standard ISO string to prevent key mismatch
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)

            for direction, detector in [("long", is_bullish_wickless), ("short", is_bearish_wickless)]:
                key = (ts_str, direction)
                if key in self._known_zone_keys:
                    continue
                
                if detector(row, WICK_TOLERANCE_PCT):
                    self.zones.append(Zone(row["open"], direction, i, a, ts))
                    self._known_zone_keys.add(key)
                    
                    # ONLY log when a fresh candle closes during LIVE monitoring
                    if self.live_mode and i == latest_idx:
                        log.info("✨ [%s] NEW Wickless candle zone spotted at %.2f (%s on %s)", 
                                 self.symbol, row['open'], direction.upper(), ts_str)

        self._expire_stale_zones(closed_df)
        latest_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        ef = ema_fast.iloc[-1]
        es = ema_slow.iloc[-1]
        trend = get_trend(ef, es, live_price)

        if self.open_trade:
            self._check_exits_live(live_price, latest_ts)

        if not self.open_trade and self.live_mode:
            self._check_entries_live(live_price, latest_ts, closed_df, trend, news_blocked)

        if self.open_trade:
            mt5_status = "RUNNING" if self.symbol.upper() == "GOLD" else "SKIPPED (GOLD ONLY)"
            ch1_2_status = "ACTIVE" if (self.symbol.upper() == "GOLD" and is_london_or_ny_session() and self.daily_losses < 2) else "PAUSED/OFF-SESSION/DISABLED"
            log.info(
                "[%s] ⚡ ACTIVE TRADE RUNNING | Trade: %s %s @ %.2f | MT5 Auto-Trader: %s | Channel 3: RUNNING | Channels 1&2: %s",
                self.symbol, self.open_trade.direction.upper(), self.symbol, self.open_trade.entry, mt5_status, ch1_2_status
            )

        active_zones = [z for z in self.zones if z.active]
        log.info(
            "[%s] Live: %.2f | Trend: %s | Active Zones: %d | Open Trade: %s",
            self.symbol, live_price, trend.upper(), len(active_zones), self.open_trade or "none"
        )