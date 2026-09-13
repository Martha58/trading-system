import pandas as pd

# INDICATORS SETTINGS
EMA_FAST           = 20               
EMA_SLOW           = 50               
NEUTRAL_BAND_PCT   = 0.05      

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def compute_emas(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    fast = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    slow = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return fast, slow

def get_trend(ema_fast: float, ema_slow: float, price: float) -> str:
    gap = abs(ema_fast - ema_slow)
    band = price * NEUTRAL_BAND_PCT / 100
    if gap <= band:
        return "neutral"
    return "up" if ema_fast > ema_slow else "down"

def is_bullish_wickless(row: pd.Series, tol_pct: float) -> bool:
    if row["close"] <= row["open"]:
        return False
    body = row["close"] - row["open"]
    return body > 0 and (row["open"] - row["low"]) <= tol_pct * body

def is_bearish_wickless(row: pd.Series, tol_pct: float) -> bool:
    if row["close"] >= row["open"]:
        return False
    body = row["open"] - row["close"]
    return body > 0 and (row["high"] - row["open"]) <= tol_pct * body