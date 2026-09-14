import os
import logging
import pandas as pd
import rpyc
from datetime import datetime, timezone
from trade_executor import get_container_mt5, SYMBOL_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# SESSION FILTER (London: 07:00 UTC - 16:00 UTC, NY: 13:00 UTC - 21:00 UTC)
def is_london_or_ny_session() -> bool:
    """Checks if current UTC time falls within London or New York trading hours (07:00 - 21:00 UTC)"""
    current_hour = datetime.now(timezone.utc).hour
    return 7 <= current_hour < 21

def initialize_mt5() -> bool:
    """Checks whether the primary MT5 socket connection is reachable."""
    mt5_fxpro = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_fxpro is None:
        return False
    return bool(mt5_fxpro.initialize())

def fetch_year_to_date_ohlc_mt5(symbol: str, timeframe: int) -> pd.DataFrame:
    """Fetches historical candles from Jan 1 of current year to Date via FxPro container."""
    mt5_inst = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_inst is None or not mt5_inst.initialize():
        raise RuntimeError("Failed to connect to FxPro MT5 container")
    
    broker_symbol = SYMBOL_MAP.get("FxPro", {}).get(symbol, symbol)
    now = datetime.now(timezone.utc)
    start_of_year = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    
    rates = mt5_inst.copy_rates_range(broker_symbol, timeframe, start_of_year, now)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No YTD data returned from MT5 container for {broker_symbol}")
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'tick_volume': 'volume'}, inplace=True)
    return df

def fetch_ohlc_mt5(symbol: str, timeframe_minutes: int = 15, count: int = 200) -> pd.DataFrame:
    """Fetches historical rates over RPyC and constructs a clean pandas DataFrame."""
    mt5_fxpro = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_fxpro is None or not mt5_fxpro.initialize():
        raise RuntimeError("Primary MT5 container connection unavailable")

    broker_symbol = SYMBOL_MAP.get("FxPro", {}).get(symbol, symbol)
    
    tf_map = {
        1: mt5_fxpro.TIMEFRAME_M1,
        5: mt5_fxpro.TIMEFRAME_M5,
        15: mt5_fxpro.TIMEFRAME_M15,
        30: mt5_fxpro.TIMEFRAME_M30,
        60: mt5_fxpro.TIMEFRAME_H1,
        240: mt5_fxpro.TIMEFRAME_H4,
        1440: mt5_fxpro.TIMEFRAME_D1,
    }
    timeframe = tf_map.get(timeframe_minutes, mt5_fxpro.TIMEFRAME_M15)

    rates = mt5_fxpro.copy_rates_from_pos(broker_symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        raise ValueError(f"Failed to retrieve rates for {broker_symbol}")

    # Pull raw rates structured array into local process memory
    local_rates = rpyc.classic.obtain(rates)

    # Convert structured numpy array explicitly into a list of tuples with field names
    colnames = list(local_rates.dtype.names)
    df = pd.DataFrame(local_rates.tolist(), columns=colnames)

    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df[["time", "open", "high", "low", "close", "volume"]]