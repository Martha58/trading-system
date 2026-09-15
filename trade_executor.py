import os
import json
import logging
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
        "symbols": {"GOLD": "XAUUSDm", "SILVER": "XAGUSDm"}
    },
    "NairaTrader": {
        "host_env": "NAIRATRADER_HOST", "default_host": "mt5-nairatrader",
        "port_env": "NAIRATRADER_PORT", "default_port": 8001,
        "symbols": {"GOLD": "XAUUSD.s", "SILVER": "XAGUSD.s"}
    }
}

SYMBOL_MAP = {b: cfg["symbols"] for b, cfg in BROKER_CONFIGS.items()}


def get_container_mt5(host_env: str, default_host: str, port_env: str, default_port: int):
    """
    Return (MetaTrader5 module, rpyc connection) for the target container.
    Kept as a tuple so existing callers (data_extraction.py) keep working.
    """
    host = os.getenv(host_env, default_host)
    port = int(os.getenv(port_env, default_port))
    try:
        conn = rpyc.classic.connect(host, port)
        conn._config["sync_request_timeout"] = 15
        return conn.modules.MetaTrader5, conn
    except Exception as e:
        log.error(f"Failed to connect to MT5 container at {host}:{port} -> {e}")
        return None, None


def _remote_order_send(conn, request_dict):
    """
    Execute MetaTrader5.order_send(...) on the remote (Wine) side, forcing
    the request to arrive as a *native* Python dict instead of an rpyc netref.

    We build an expression that:
      1. JSON-decodes a string we pass over the wire,
      2. Calls MetaTrader5.order_send on the resulting native dict,
      3. Returns a plain tuple of the fields we care about.

    Sending the request as a JSON string sidesteps rpyc's proxy-based
    argument marshalling entirely. The MT5 C extension on the remote side
    receives a real dict, exactly as if it were called locally.
    """
    request_json = json.dumps(request_dict)

    # Single expression (rpyc classic .eval() only accepts expressions).
    # repr() produces a safe Python string literal for the JSON payload.
    expr = (
        "(lambda r: "
        "(r.retcode, r.order, r.deal, r.price, r.comment) if r is not None "
        "else (None, None, None, None, 'order_send returned None'))"
        "(__import__('MetaTrader5').order_send("
        "__import__('json').loads(" + repr(request_json) + ")"
        "))"
    )

    try:
        retcode, order, deal, price, comment = conn.eval(expr)
        return retcode, order, deal, price, comment
    except Exception as e:
        log.error(f"Remote order_send via JSON failed: {e}")
        return None, None, None, None, str(e)


def get_current_price_mt5(symbol: str) -> tuple[float, float]:
    mt5_inst, conn = get_container_mt5("FXPRO_HOST", "mt5-fxpro", "FXPRO_PORT", 8001)
    if not mt5_inst:
        raise ValueError(f"Could not connect to primary MT5 container for {symbol}")

    try:
        if not mt5_inst.initialize():
            raise ValueError("Failed to initialize primary MT5 terminal")

        broker_symbol = str(SYMBOL_MAP.get("FxPro", {}).get(symbol.upper(), symbol))
        mt5_inst.symbol_select(broker_symbol, True)

        tick = mt5_inst.symbol_info_tick(broker_symbol)
        if tick is None:
            raise ValueError(f"Could not get tick data for {broker_symbol}")
        return float(tick.bid), float(tick.ask)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def execute_container_trade(broker_name, config, symbol, direction, volume, sl, tp):
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
            log.error(f"[{broker_name}] Failed to select symbol '{broker_symbol}'.")
            return False

        symbol_info = mt5_inst.symbol_info(broker_symbol)
        if symbol_info is None:
            log.error(f"[{broker_name}] No symbol_info for {broker_symbol}")
            return False

        # Verify symbol is fully tradeable (SYMBOL_TRADE_MODE_FULL = 4)
        trade_mode = int(getattr(symbol_info, "trade_mode", -1))
        if trade_mode != 4:
            log.error(
                f"[{broker_name}] Symbol {broker_symbol} not fully tradeable "
                f"(trade_mode={trade_mode})."
            )
            return False

        tick = mt5_inst.symbol_info_tick(broker_symbol)
        if tick is None:
            log.error(f"[{broker_name}] Tick fetch failed for {broker_symbol}")
            return False

        digits = int(symbol_info.digits)
        point = float(symbol_info.point)
        stop_level = float(getattr(symbol_info, "trade_stops_level", 0)) * point

        # Volume normalization
        vol_step = float(getattr(symbol_info, "volume_step", 0.01)) or 0.01
        vol_min = float(getattr(symbol_info, "volume_min", 0.01))
        volume = max(vol_min, round(volume / vol_step) * vol_step)

        if direction.lower() == "long":
            order_type = int(mt5_inst.ORDER_TYPE_BUY)
            price = float(tick.ask)
            if sl >= price:
                sl = price - 1.0
            if (price - sl) < stop_level:
                sl = price - stop_level - (10 * point)
            if tp <= price:
                tp = price + 1.0
            if (tp - price) < stop_level:
                tp = price + stop_level + (10 * point)
        else:
            order_type = int(mt5_inst.ORDER_TYPE_SELL)
            price = float(tick.bid)
            if sl <= price:
                sl = price + 1.0
            if (sl - price) < stop_level:
                sl = price + stop_level + (10 * point)
            if tp >= price:
                tp = price - 1.0
            if (price - tp) < stop_level:
                tp = price - stop_level - (10 * point)

        price = float(f"{price:.{digits}f}")
        sl = float(f"{sl:.{digits}f}")
        tp = float(f"{tp:.{digits}f}")
        volume = float(f"{volume:.2f}")

        # Choose filling mode from symbol_info.filling_mode
        SYMBOL_FILLING_FOK = int(getattr(mt5_inst, "SYMBOL_FILLING_FOK", 1))
        SYMBOL_FILLING_IOC = int(getattr(mt5_inst, "SYMBOL_FILLING_IOC", 2))

        filling_mode = int(getattr(symbol_info, "filling_mode", 0))
        log.info(f"[{broker_name}] symbol={broker_symbol} filling_mode={filling_mode}")

        if filling_mode & SYMBOL_FILLING_FOK:
            type_filling = int(mt5_inst.ORDER_FILLING_FOK)
        elif filling_mode & SYMBOL_FILLING_IOC:
            type_filling = int(mt5_inst.ORDER_FILLING_IOC)
        else:
            log.error(
                f"[{broker_name}] Symbol {broker_symbol} supports neither FOK "
                f"nor IOC filling; market order not possible."
            )
            return False

        request = {
            "action": int(mt5_inst.TRADE_ACTION_DEAL),
            "symbol": broker_symbol,
            "volume": float(volume),
            "type": int(order_type),
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 20,
            "magic": 888999,
            "comment": "",
            "type_time": int(mt5_inst.ORDER_TIME_GTC),
            "type_filling": int(type_filling),
        }

        log.info(f"[{broker_name}] Sending request (JSON-encoded): {request}")

        retcode, order, deal, fill_price, comment = _remote_order_send(conn, request)

        if retcode is None:
            log.error(f"❌ [{broker_name}] order_send returned None. comment={comment}")
            return False

        if int(retcode) != int(mt5_inst.TRADE_RETCODE_DONE):
            log.error(
                f"❌ [{broker_name}] Rejected | retcode={retcode} "
                f"| comment='{comment}' | request={request}"
            )
            return False

        log.info(
            f"🚀 [{broker_name}] Filled! ticket=#{order} deal=#{deal} "
            f"{direction.upper()} {volume} @ {fill_price}"
        )
        return True

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def execute_multi_account_trades(symbol: str, direction: str, volume: float, sl: float, tp: float):
    log.info(f"⚡ Executing Multi-Account Trades for {symbol} ({direction.upper()})...")
    for broker_name, config in BROKER_CONFIGS.items():
        try:
            execute_container_trade(broker_name, config, symbol, direction, volume, sl, tp)
        except Exception as exc:
            log.error(f"Error executing trade on {broker_name}: {exc}")