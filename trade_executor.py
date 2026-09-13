import os
import logging
import rpyc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MAGIC_NUMBER = 888999

SYMBOL_MAP = {
    "FxPro": {"GOLD": "GOLD", "SILVER": "SILVER"},
    "NairaFunded": {"GOLD": "XAUUSDm", "SILVER": "XAGUSDm"},
    "NairaTrader": {"GOLD": "XAUUSDm", "SILVER": "XAGUSDm"},
}

def get_container_mt5(host_env: str, default_host: str, port_env: str, default_port: int):
    """Establishes RPyC connection and imports MetaTrader5 from the target container."""
    host = os.getenv(host_env, default_host)
    port = int(os.getenv(port_env, default_port))
    try:
        conn = rpyc.classic.connect(host, port)
        mt5_remote = conn.builtins.__import__("MetaTrader5")
        return mt5_remote
    except Exception as err:
        log.warning("MT5 container socket at %s:%d not ready yet - %s", host, port, err)
        return None

def execute_container_trade(account_label: str, host_env: str, default_host: str, port_env: str, default_port: int,
                            symbol: str, direction: str, volume: float, sl: float, tp: float) -> bool:
    """Dynamically connects and executes a market deal over remote socket connection."""
    mt5_instance = get_container_mt5(host_env, default_host, port_env, default_port)
    
    if mt5_instance is None:
        log.error("❌ Trade aborted for %s: MT5 container socket connection unavailable", account_label)
        return False

    if not mt5_instance.initialize():
        log.error("❌ Failed to initialize MT5 connection for %s container", account_label)
        return False

    broker_symbol = SYMBOL_MAP.get(account_label, {}).get(symbol, symbol)

    symbol_info = mt5_instance.symbol_info(broker_symbol)
    if symbol_info is None:
        log.error("[%s] Failed to find symbol info for %s", account_label, broker_symbol)
        return False

    if not symbol_info.visible:
        if not mt5_instance.symbol_select(broker_symbol, True):
            log.error("[%s] Failed to select symbol %s", account_label, broker_symbol)
            return False

    tick = mt5_instance.symbol_info_tick(broker_symbol)
    if tick is None:
        log.error("[%s] Failed to fetch tick for trade execution on %s", account_label, broker_symbol)
        return False

    digits = symbol_info.digits
    point = symbol_info.point
    stop_level = getattr(symbol_info, "trade_stops_level", 0) * point

    if direction == "long":
        order_type = mt5_instance.ORDER_TYPE_BUY
        price = tick.ask
        if sl >= price:
            sl = price - (1.0 if point == 0.01 else 100 * point)
        if (price - sl) < stop_level:
            sl = price - stop_level - (10 * point)
    else:  # short
        order_type = mt5_instance.ORDER_TYPE_SELL
        price = tick.bid
        if sl <= price:
            sl = price + (1.0 if point == 0.01 else 100 * point)
        if (sl - price) < stop_level:
            sl = price + stop_level + (10 * point)

    price = round(price, digits)
    sl = round(sl, digits)
    tp = round(tp, digits)

    filling_mode = symbol_info.filling_mode
    if filling_mode & 1:
        filling_type = mt5_instance.ORDER_FILLING_FOK
    elif filling_mode & 2:
        filling_type = mt5_instance.ORDER_FILLING_IOC
    else:
        filling_type = mt5_instance.ORDER_FILLING_RETURN

    request = {
        "action": mt5_instance.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": float(volume),
        "type": order_type,
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": f"Bot Signal [{account_label}]",
        "type_time": mt5_instance.ORDER_TIME_GTC,
        "type_filling": filling_type,
    }

    result = mt5_instance.order_send(request)
    if result is None or result.retcode != mt5_instance.TRADE_RETCODE_DONE:
        ret_code = result.retcode if result else "None"
        ret_comment = result.comment if result else "No Response"
        log.error("❌ MT5 [%s] Order Failed! Symbol: %s | Retcode: %s | Description: %s", 
                  account_label, broker_symbol, ret_code, ret_comment)
        return False

    log.info("🚀 MT5 [%s] Order Executed Successfully! Symbol: %s | Ticket: #%d | %s %.2f lots @ %.2f | SL: %.2f | TP: %.2f",
             account_label, broker_symbol, result.order, direction.upper(), volume, price, sl, tp)
    return True

def execute_multi_account_trades(symbol: str, direction: str, volume: float, sl: float, tp: float) -> bool:
    """Triggers automated orders across FxPro, NairaFunded, and NairaTrader containers concurrently."""
    res_fxpro = execute_container_trade("FxPro", "FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001, symbol, direction, volume, sl, tp)
    res_nairafunded = execute_container_trade("NairaFunded", "NAIRAFUNDED_HOST", "mt5-nairafunded", "NAIRAFUNDED_PORT", 8001, symbol, direction, volume, sl, tp)
    res_nairatrader = execute_container_trade("NairaTrader", "NAIRATRADER_HOST", "mt5-nairatrader", "NAIRATRADER_PORT", 8001, symbol, direction, volume, sl, tp)
    
    return res_fxpro or res_nairafunded or res_nairatrader

def get_current_price_mt5(symbol: str) -> tuple[float, float]:
    """Fetches market ticks using the primary FxPro container connection."""
    mt5_fxpro = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if mt5_fxpro is None:
        raise ValueError("FxPro MT5 container socket connection is offline")
    broker_symbol = SYMBOL_MAP.get("FxPro", {}).get(symbol, symbol)
    tick = mt5_fxpro.symbol_info_tick(broker_symbol)
    if tick is None:
        raise ValueError(f"Could not get tick data for {broker_symbol}")
    return tick.bid, tick.ask