import logging
from trade_executor import get_current_price_mt5, execute_multi_account_trades

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def test_trade_execution():
    symbol = "GOLD"
    direction = "long"
    volume = 0.01  # Minimum lot size
    
    log.info("Fetching current market price to construct test trade...")
    bid, ask = get_current_price_mt5(symbol)
    
    # Set execution levels close to live price
    entry_price = ask
    sl = round(entry_price - 5.0, 2)  # 50 pips below Ask
    tp = round(entry_price + 5.0, 2)  # 50 pips above Ask

    log.info(f"Test Trade Parameters | Entry: {entry_price} | SL: {sl} | TP: {tp}")
    log.info("Sending test trade to all container accounts...")
    
    execute_multi_account_trades(symbol, direction, volume, sl, tp)

if __name__ == "__main__":
    test_trade_execution()