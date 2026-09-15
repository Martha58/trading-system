"""
Bisect which field is causing FxPro Demo to reject the request.
Uses order_check only — never places a trade.
"""
import logging
import rpyc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bisect")

HOST, PORT = "mt5-fxpro", 8001


def check(mt5, label, req):
    try:
        r = mt5.order_check(request=req)
        if r is None:
            log.error(f"[{label}] -> None | last_error={mt5.last_error()}")
        else:
            log.info(
                f"[{label}] -> retcode={r.retcode} "
                f"comment='{r.comment}' "
                f"margin={getattr(r,'margin',None)}"
            )
    except Exception as e:
        log.error(f"[{label}] raised: {e}")


def main():
    conn = rpyc.classic.connect(HOST, PORT)
    conn._config["sync_request_timeout"] = 15
    mt5 = conn.modules.MetaTrader5
    mt5.initialize()

    sym = "GOLD"
    mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    log.info(f"symbol_info: digits={info.digits} point={info.point} "
             f"filling_mode={info.filling_mode} trade_mode={info.trade_mode}")
    log.info(f"tick: bid={tick.bid} ask={tick.ask}")

    ask = float(tick.ask)
    bid = float(tick.bid)

    # ---- Test 1: MINIMAL request, no stops, no deviation, no magic, no comment ----
    r1 = {
        "action": int(mt5.TRADE_ACTION_DEAL),
        "symbol": sym,
        "volume": 0.01,
        "type": int(mt5.ORDER_TYPE_BUY),
        "price": ask,
        "type_filling": int(mt5.ORDER_FILLING_IOC),
    }
    check(mt5, "1 minimal IOC", r1)

    # ---- Test 2: minimal + SL/TP ----
    r2 = dict(r1, sl=ask - 20.0, tp=ask + 20.0)
    check(mt5, "2 + SL/TP", r2)

    # ---- Test 3: minimal + FOK filling instead of IOC ----
    r3 = dict(r1, type_filling=int(mt5.ORDER_FILLING_FOK))
    check(mt5, "3 minimal FOK", r3)

    # ---- Test 4: minimal + RETURN filling (invalid for market but check anyway) ----
    r4 = dict(r1, type_filling=int(mt5.ORDER_FILLING_RETURN))
    check(mt5, "4 minimal RETURN", r4)

    # ---- Test 5: minimal + deviation ----
    r5 = dict(r1, deviation=20)
    check(mt5, "5 + deviation", r5)

    # ---- Test 6: minimal + magic ----
    r6 = dict(r1, magic=888999)
    check(mt5, "6 + magic", r6)

    # ---- Test 7: minimal + comment ----
    r7 = dict(r1, comment="TestBot")
    check(mt5, "7 + comment", r7)

    # ---- Test 8: minimal + type_time GTC ----
    r8 = dict(r1, type_time=int(mt5.ORDER_TIME_GTC))
    check(mt5, "8 + type_time", r8)

    # ---- Test 9: the exact request that's currently failing ----
    r9 = {
        "action": int(mt5.TRADE_ACTION_DEAL),
        "symbol": sym,
        "volume": 0.01,
        "type": int(mt5.ORDER_TYPE_BUY),
        "price": ask,
        "sl": ask - 5.0,
        "tp": ask + 5.0,
        "deviation": 20,
        "magic": 888999,
        "comment": "",
        "type_time": int(mt5.ORDER_TIME_GTC),
        "type_filling": int(mt5.ORDER_FILLING_IOC),
    }
    check(mt5, "9 full request (current)", r9)

    # ---- Test 10: same as 9 but SELL instead of BUY ----
    r10 = dict(r9, type=int(mt5.ORDER_TYPE_SELL), price=bid)
    check(mt5, "10 full SELL", r10)

    conn.close()


if __name__ == "__main__":
    main()