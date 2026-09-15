import os
import logging
import math
import rpyc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BROKER_CONFIGS = {
    "FxPro": {
        "host_env": "FXPRO_HOST", "default_host": "mt5-fxpro",
        "port_env": "FXPRO_PORT", "default_port": 8001,
        "symbols": {"GOLD": "GOLD", "SILVER": "SILVER"}
    },
    "NairaFunded": {
        "host_env": "NAIRAFUNDED_HOST", "default_host": "mt5-nairafunded",
        "port_env": "NAIRAFUNDED_PORT", "default_port": 8001,
        "symbols": {"GOLD": "GOLD", "SILVER": "SILVER"}
    },
    "NairaTrader": {
        "host_env": "NAIRATRADER_HOST", "default_host": "mt5-nairatrader",
        "port_env": "NAIRATRADER_PORT", "default_port": 8001,
        "symbols": {"GOLD": "XAUUSD", "SILVER": "XAGUSD"}
    }
}

SYMBOL_MAP = {b: cfg["symbols"] for b, cfg in BROKER_CONFIGS.items()}

def get_container_mt5(host_env: str, default_host: str, port_env: str, default_port: int):
    host = os.getenv(host_env, default_host)
    port = int(os.getenv(port_env, default_port))
    try:
        conn = rpyc.classic.connect(host, port)
        conn._config["sync_request_timeout"] = 15
        return conn.modules.MetaTrader5, conn
    except Exception as e:
        log.error(f"Failed to connect to MT5 container at {host}:{port} -> {e}")
        return None, None

def get_current_price_mt5(symbol: str) -> tuple[float, float]:
    mt5_inst, conn = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if not mt5_inst:
        raise ValueError(f"Could not connect to primary MT5 container for {symbol}")
    
    try:
        if not mt5_inst.initialize():
            raise ValueError(f"Failed to initialize primary MT5 terminal")
            
        broker_symbol = str(SYMBOL_MAP.get("FxPro", {}).get(symbol.upper(), symbol))
        mt5_inst.symbol_select(broker_symbol, True)
        
        tick = mt5_inst.symbol_info_tick(broker_symbol)
        if tick is None:
            raise ValueError(f"Could not get tick data for {broker_symbol}")
        return float(tick.bid), float(tick.ask)
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def execute_container_trade(broker_name: str, config: dict, symbol: str, direction: str, volume: float, sl: float, tp: float) -> bool:
    mt5_inst, conn = get_container_mt5(
        config["host_env"], config["default_host"],
        config["port_env"], config["default_port"]
    )
    if mt5_inst is None or conn is None:
        log.error(f"[{broker_name}] MT5 Connection unavailable.")
        return False

    try:
        if not mt5_inst.initialize():
            log.error(f"[{broker_name}] MT5 Initialization failed.")
            return False

        broker_symbol = str(config["symbols"].get(symbol.upper(), symbol))
        
        if not mt5_inst.symbol_select(broker_symbol, True):
            log.error(f"[{broker_name}] Failed to select symbol '{broker_symbol}' in Market Watch.")
            return False

        symbol_info = mt5_inst.symbol_info(broker_symbol)
        if symbol_info is None:
            log.error(f"[{broker_name}] Failed to find symbol info for {broker_symbol}")
            return False

        tick = mt5_inst.symbol_info_tick(broker_symbol)
        if tick is None:
            log.error(f"[{broker_name}] Tick fetch failed for {broker_symbol}")
            return False

        digits = int(symbol_info.digits)
        point = float(symbol_info.point)
        stop_level = float(getattr(symbol_info, "trade_stops_level", 0)) * point

        # Normalize volume to broker step size
        vol_step = float(getattr(symbol_info, "volume_step", 0.01))
        vol_min = float(getattr(symbol_info, "volume_min", 0.01))
        volume = max(vol_min, round(volume / vol_step) * vol_step)

        if direction.lower() == "long":
            order_type = int(mt5_inst.ORDER_TYPE_BUY)
            price = float(tick.ask)
            if sl >= price:
                sl = price - (1.0 if point == 0.01 else 100 * point)
            if (price - sl) < stop_level:
                sl = price - stop_level - (10 * point)
            if tp <= price:
                tp = price + (1.0 if point == 0.01 else 100 * point)
            if (tp - price) < stop_level:
                tp = price + stop_level + (10 * point)
        else:
            order_type = int(mt5_inst.ORDER_TYPE_SELL)
            price = float(tick.bid)
            if sl <= price:
                sl = price + (1.0 if point == 0.01 else 100 * point)
            if (sl - price) < stop_level:
                sl = price + stop_level + (10 * point)
            if tp >= price:
                tp = price - (1.0 if point == 0.01 else 100 * point)
            if (price - tp) < stop_level:
                tp = price - stop_level - (10 * point)

        # Strictly format floats to match exact digit precision
        price = float(f"{price:.{digits}f}")
        sl = float(f"{sl:.{digits}f}")
        tp = float(f"{tp:.{digits}f}")
        volume = float(f"{volume:.2f}")

        # Check broker supported filling mode
        filling_mode = int(getattr(symbol_info, "filling_mode", 0))
        possible_fillings = []
        if filling_mode & 1:
            possible_fillings.append(int(mt5_inst.ORDER_FILLING_FOK))
        if filling_mode & 2:
            possible_fillings.append(int(mt5_inst.ORDER_FILLING_IOC))
        possible_fillings.append(int(getattr(mt5_inst, "ORDER_FILLING_RETURN", 2)))

        result = None
        for fill_type in possible_fillings:
            request = {
                "action": int(mt5_inst.TRADE_ACTION_DEAL),
                "symbol": str(broker_symbol),
                "volume": float(volume),
                "type": int(order_type),
                "price": float(price),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": int(20),
                "magic": int(888999),
                "comment": str("Wickless Bot Multi-Account"),
                "type_time": int(mt5_inst.ORDER_TIME_GTC),
                "type_filling": int(fill_type),
            }

            remote_request = conn.builtins.dict(request)
            result = mt5_inst.order_send(request=remote_request)
            
            if result is not None and getattr(result, 'retcode', None) == mt5_inst.TRADE_RETCODE_DONE:
                break

        if result is None:
            err = mt5_inst.last_error()
            log.error(f"❌ [{broker_name}] Order Failed! mt5.order_send returned None. MT5 Error: {err}")
            return False

        if getattr(result, 'retcode', None) != mt5_inst.TRADE_RETCODE_DONE:
            ret_code = getattr(result, 'retcode', 'None')
            ret_comment = getattr(result, 'comment', 'No Response')
            log.error(f"❌ [{broker_name}] Order Rejected! Retcode: {ret_code} | Reason: {ret_comment} | last_error={mt5_inst.last_error()} | request={request}")
            return False

        log.info(f"🚀 [{broker_name}] Order Executed! Ticket: #{result.order} | {direction.upper()} {volume} lots @ {price}")
        return True

    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def execute_multi_account_trades(symbol: str, direction: str, volume: float, sl: float, tp: float):
    log.info(f"⚡ Executing Multi-Account Trades for {symbol} ({direction.upper()})...")
    for broker_name, config in BROKER_CONFIGS.items():
        try:
            execute_container_trade(broker_name, config, symbol, direction, volume, sl, tp)
        except Exception as exc:
            log.error(f"Error executing trade on {broker_name}: {exc}")