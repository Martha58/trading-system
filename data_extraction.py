import time
import logging
import pandas as pd
from datetime import datetime, timezone
from trade_executor import get_container_mt5, SYMBOL_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def is_london_or_ny_session() -> bool:
    current_hour = datetime.now(timezone.utc).hour
    return 7 <= current_hour < 21


def initialize_mt5() -> bool:
    mt5_fxpro, conn = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_fxpro is None:
        return False
    try:
        return bool(mt5_fxpro.initialize())
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def fetch_year_to_date_ohlc_mt5(symbol: str, timeframe: int) -> pd.DataFrame:
    mt5_inst = None
    conn = None

    for attempt in range(1, 4):
        mt5_inst, conn = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
        if mt5_inst is not None and mt5_inst.initialize():
            break
        time.sleep(1)

    if mt5_inst is None:
        raise RuntimeError("Failed to connect to FxPro MT5 container after retries")

    try:
        broker_symbol = SYMBOL_MAP.get("FxPro", {}).get(symbol, symbol)
        now = datetime.now(timezone.utc)
        start_of_year = datetime(now.year, 1, 1, tzinfo=timezone.utc)

        rates = mt5_inst.copy_rates_range(broker_symbol, timeframe, start_of_year, now)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No YTD data returned from MT5 container for {broker_symbol}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def fetch_ohlc_mt5(symbol: str, timeframe_minutes: int = 15, count: int = 200) -> pd.DataFrame:
    mt5_fxpro = None
    conn = None

    for attempt in range(1, 4):
        mt5_fxpro, conn = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
        if mt5_fxpro is not None and mt5_fxpro.initialize():
            break
        time.sleep(1)

    if mt5_fxpro is None:
        raise RuntimeError("Primary MT5 container connection unavailable after retries")

    try:
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

        # rpyc returns a proxied numpy array; pandas can build a DataFrame from
        # a structured array directly (columns come from the dtype field names).
        df = pd.DataFrame(rates)
        if "time" not in df.columns and hasattr(rates, "dtype") and rates.dtype.names:
            df = pd.DataFrame(rates.tolist(), columns=list(rates.dtype.names))

        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df.set_index("time", inplace=True)
        return df[["open", "high", "low", "close", "volume"]]
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass