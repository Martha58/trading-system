import logging
import time
from telegram_bot import create_telegram_bot_from_env
from data_extraction import initialize_mt5, fetch_ohlc_mt5
from trade_executor import get_current_price_mt5
from news_filter import get_high_impact_news_status
from strategy_engine import WicklessCandleBot
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SYMBOLS            = ["GOLD", "SILVER"]
INTERVAL           = 15
OUTPUTSIZE         = 200
POLL_SECONDS       = 5
ENABLE_TELEGRAM    = True

def main():
    log.info("Initializing MT5 container connections...")
    
    # Retry loop: waits for MT5 container socket to come online
    while not initialize_mt5():
        log.info("Waiting 5 seconds for MT5 socket server to become available...")
        time.sleep(5)

    log.info("MT5 container sockets initialized successfully!")

    telegram_bot = create_telegram_bot_from_env() if ENABLE_TELEGRAM else None
    if telegram_bot:
        telegram_bot.start()

    bots = {sym: WicklessCandleBot(symbol=sym, telegram_bot=telegram_bot) for sym in SYMBOLS}
    notified_news_events = set()

    for sym, bot in bots.items():
        log.info("Fetching initial data to prime zones for: %s", sym)
        try:
            df_seed = fetch_ohlc_mt5(sym, INTERVAL, OUTPUTSIZE)
            bid, ask = get_current_price_mt5(sym)
            bot.backtest(df_seed)
            bot.process_latest(df_seed, (bid + ask) / 2)
            bot.live_mode = True
        except Exception as exc:
            log.error("Initialization error for %s: %s", sym, exc)

    log.info("Live monitoring started for GOLD & SILVER (Polling every %ds)...", POLL_SECONDS)

    while True:
        try:
            news_blocked, notified_news_events = get_high_impact_news_status(
                telegram_bot=telegram_bot, 
                notified_set=notified_news_events
            )

            for sym, bot in bots.items():
                df_live = fetch_ohlc_mt5(sym, INTERVAL, OUTPUTSIZE)
                bid, ask = get_current_price_mt5(sym)
                mid_price = (bid + ask) / 2
                
                bot.process_latest(df_live, mid_price, news_blocked=news_blocked)

        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
            if telegram_bot:
                telegram_bot.stop()
            break
        except Exception as exc:
            log.error("Unexpected error in main loop: %s", exc, exc_info=True)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()