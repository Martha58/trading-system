from datetime import datetime, timezone
import requests 
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# HIGH IMPACT USD NEWS FILTER
def get_high_impact_news_status(telegram_bot=None, notified_set=set()):
    now = datetime.now(timezone.utc)
    is_blocked = False
    
    try:
        url = "https://nagerholidays.com/api/v3/NextPublicHolidays/US"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            events = response.json() if isinstance(response.json(), list) else []
            for ev in events:
                if ev.get('countryCode') == 'US':
                    ev_time_str = ev.get('date')
                    ev_time = datetime.fromisoformat(ev_time_str).astimezone(timezone.utc)
                    diff_seconds = (ev_time - now).total_seconds()
                    ev_id = f"{ev.get('name')}_{ev_time_str}"

                    if 3300 <= diff_seconds <= 3600 and ev_id not in notified_set:
                        if telegram_bot:
                            time_str = ev_time.strftime("%H:%M UTC")
                            telegram_bot.notify_news_warning(ev.get('name', 'USD High Impact News'), time_str)
                            log.info("📢 News warning sent: %s", ev.get('name'))
                        notified_set.add(ev_id)

                    if -3600 <= diff_seconds <= 3600:
                        is_blocked = True
                        log.info("⛔ Trading paused: High Impact USD News (%s)", ev.get('name'))

    except Exception as e:
        log.debug("Calendar check pass: %s", e)

    return is_blocked, notified_set