import os
import logging
import pandas as pd
from trade_executor import get_container_mt5, SYMBOL_MAP

log = logging.getLogger(__name__)

def initialize_mt5() -> bool:
    """Checks whether the primary MT5 socket connection is reachable."""
    mt5_fxpro = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_fxpro is None:
        return False
    return bool(mt5_fxpro.initialize())

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

    # Convert remote structured array to DataFrame
    df = pd.DataFrame(list(rates))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df[["time", "open", "high", "low", "close", "volume"]]