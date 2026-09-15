import os
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

        # ---- DIAGNOSTIC: terminal / account / symbol state ----
        term = mt5_inst.terminal_info()
        acct = mt5_inst.account_info()
        log.info(
            f"[{broker_name}] TERM  | trade_allowed={getattr(term,'trade_allowed',None)} "
            f"| connected={getattr(term,'connected',None)} "
            f"| tradeapi_disabled={getattr(term,'tradeapi_disabled',None)} "
            f"| build={getattr(term,'build',None)}"
        )
        log.info(
            f"[{broker_name}] ACCT  | login={getattr(acct,'login',None)} "
            f"| server={getattr(acct,'server',None)} "
            f"| trade_allowed={getattr(acct,'trade_allowed',None)} "
            f"| trade_expert={getattr(acct,'trade_expert',None)} "
            f"| margin_free={getattr(acct,'margin_free',None)} "
            f"| currency={getattr(acct,'currency',None)}"
        )
        log.info(
            f"[{broker_name}] SYM   | name={symbol_info.name} "
            f"| digits={digits} point={point} "
            f"| trade_mode={trade_mode} "
            f"| filling_mode={filling_mode} "
            f"| stops_level={symbol_info.trade_stops_level} "
            f"| freeze_level={getattr(symbol_info,'trade_freeze_level',None)} "
            f"| bid={tick.bid} ask={tick.ask}"
        )
        # ---- END DIAGNOSTIC ----

        # ---- Build the request ----
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
            "comment": "",                                # EMPTY (was 'WicklessBot')
            "type_time": int(mt5_inst.ORDER_TIME_GTC),    # GTC = 0
            "type_filling": int(type_filling),
        }

        # ---- ORDER_CHECK: must run AFTER request is built, BEFORE order_send ----
        try:
            check = mt5_inst.order_check(request=request)
            if check is None:
                log.error(
                    f"[{broker_name}] order_check returned None. "
                    f"last_error={mt5_inst.last_error()}"
                )
            else:
                log.info(
                    f"[{broker_name}] order_check | retcode={check.retcode} "
                    f"| comment='{check.comment}' "
                    f"| margin={getattr(check,'margin',None)} "
                    f"| margin_free={getattr(check,'margin_free',None)} "
                    f"| balance={getattr(check,'balance',None)} "
                    f"| equity={getattr(check,'equity',None)}"
                )
        except Exception as e:
            log.warning(f"[{broker_name}] order_check raised: {e}")
        # ---- END ORDER_CHECK ----

        # ---- Build native remote dict and send ----
        log.info(f"[{broker_name}] Sending request: {request}")

        try:
            remote_request = conn.builtins.dict(request)
            log.info(f"[{broker_name}] remote_request type={type(remote_request)}")
        except Exception as e:
            log.warning(
                f"[{broker_name}] conn.builtins.dict failed ({e}); "
                f"falling back to direct dict"
            )
            remote_request = request

        result = mt5_inst.order_send(request=remote_request)

        if result is None:
            err = mt5_inst.last_error()
            log.error(f"❌ [{broker_name}] order_send returned None. last_error={err}")
            return False

        if int(getattr(result, "retcode", -1)) != int(mt5_inst.TRADE_RETCODE_DONE):
            log.error(
                f"❌ [{broker_name}] Rejected | retcode={result.retcode} "
                f"| comment={getattr(result, 'comment', '')} "
                f"| request={request}"
            )
            return False

        log.info(
            f"🚀 [{broker_name}] Filled! ticket=#{result.order} "
            f"{direction.upper()} {volume} @ {result.price}"
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