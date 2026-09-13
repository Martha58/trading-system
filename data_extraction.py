import os
import pandas as pd
import logging
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
    """Verifies socket connection to primary FxPro container."""
    mt5_inst = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_inst is None or not mt5_inst.initialize():
        log.warning("FxPro MT5 container socket not ready yet...")
        return False
    log.info("MT5 FxPro Container Connected Successfully")
    return True

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

def fetch_ohlc_mt5(symbol: str, timeframe: int, outputsize: int = 200) -> pd.DataFrame:
    """Fetches recent rolling candles for live monitoring via FxPro container."""
    mt5_inst = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_inst is None or not mt5_inst.initialize():
        raise RuntimeError("Failed to connect to FxPro MT5 container")
    
    broker_symbol = SYMBOL_MAP.get("FxPro", {}).get(symbol, symbol)
    rates = mt5_inst.copy_rates_from_pos(broker_symbol, timeframe, 0, outputsize)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data returned from MT5 container for {broker_symbol}")
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'tick_volume': 'volume'}, inplace=True)
    return df