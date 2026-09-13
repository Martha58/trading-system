import os
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

WAT = ZoneInfo("Africa/Lagos")

def _to_wat_str(timestamp) -> str:
    if hasattr(timestamp, 'strftime'):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(WAT).strftime('%Y-%m-%d %H:%M:%S') + " WAT"
    return str(timestamp)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

class TelegramSignalBot:
    def __init__(
        self, 
        bot_token_1: Optional[str] = None,
        signal_channel_1: Optional[str] = None,
        results_channel_1: Optional[str] = None,
        invite_link_1: Optional[str] = None,
        bot_token_2: Optional[str] = None,
        signal_channel_2: Optional[str] = None,
        results_channel_2: Optional[str] = None,
        invite_link_2: Optional[str] = None,
        bot_token_3: Optional[str] = None,
        signal_channel_3: Optional[str] = None,
    ):
        # Bot 1 Settings
        self.bot_token_1 = bot_token_1 or os.getenv("TELEGRAM_BOT_TOKEN_1") or os.getenv("TELEGRAM_BOT_TOKEN")
        self.signal_channel_1 = self._clean_channel_id(signal_channel_1 or os.getenv("TELEGRAM_SIGNAL_CHANNEL_1") or os.getenv("TELEGRAM_CHANNEL_ID"))
        self.results_channel_1 = self._clean_channel_id(results_channel_1 or os.getenv("TELEGRAM_RESULTS_CHANNEL_1"))
        self.invite_link_1 = invite_link_1 or os.getenv("TELEGRAM_INVITE_LINK_1") or os.getenv("TELEGRAM_INVITE_LINK")
        
        # Bot 2 Settings
        self.bot_token_2 = bot_token_2 or os.getenv("TELEGRAM_BOT_TOKEN_2")
        self.signal_channel_2 = self._clean_channel_id(signal_channel_2 or os.getenv("TELEGRAM_SIGNAL_CHANNEL_2"))
        self.results_channel_2 = self._clean_channel_id(results_channel_2 or os.getenv("TELEGRAM_RESULTS_CHANNEL_2") or os.getenv("TELEGRAM_CHANNEL_ID_2"))
        self.invite_link_2 = invite_link_2 or os.getenv("TELEGRAM_INVITE_LINK_2") or os.getenv("TELEGRAM_INVITE_LINK")
        
        # Bot 3 Settings (Auto-Trade Channel)
        self.bot_token_3 = bot_token_3 or os.getenv("TELEGRAM_BOT_TOKEN_3") or self.bot_token_1
        self.signal_channel_3 = self._clean_channel_id(signal_channel_3 or os.getenv("TELEGRAM_SIGNAL_CHANNEL_3"))

        self.enabled_1 = bool(self.bot_token_1 and self.signal_channel_1)
        self.enabled_2 = bool(self.bot_token_2 and self.signal_channel_2)
        self.enabled_3 = bool(self.bot_token_3 and self.signal_channel_3)

        self.base_url_1 = f"https://api.telegram.org/bot{self.bot_token_1}" if self.bot_token_1 else None
        self.base_url_2 = f"https://api.telegram.org/bot{self.bot_token_2}" if self.bot_token_2 else None
        self.base_url_3 = f"https://api.telegram.org/bot{self.bot_token_3}" if self.bot_token_3 else None
        
        self.session = requests.Session()

    def _clean_channel_id(self, raw_id: Optional[str]) -> Optional[str]:
        if not raw_id:
            return None
        cleaned = str(raw_id).strip().strip('"').strip("'")
        return cleaned if cleaned else None

    def start(self):
        if self.enabled_1 and self._test_bot_connection(self.base_url_1, "Bot 1"):
            log.info("Primary Telegram Bot 1 connected.")
        if self.enabled_2 and self._test_bot_connection(self.base_url_2, "Bot 2"):
            log.info("Secondary Telegram Bot 2 connected.")
        if self.enabled_3 and self._test_bot_connection(self.base_url_3, "Bot 3"):
            log.info("Auto-Trade Channel Bot 3 connected.")

    def stop(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _test_bot_connection(self, base_url: str, name: str) -> bool:
        try:
            res = self.session.get(f"{base_url}/getMe", timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("ok"):
                username = data.get("result", {}).get("username")
                log.info(f"{name} Verified: @{username}")
                return True
        except Exception as exc:
            log.error(f"Failed to connect {name}: {exc}")
        return False

    def send_message(self, text: str, base_url: str, target_channel: str, parse_mode: str = "HTML") -> bool:
        if not base_url or not target_channel:
            return False

        try:
            payload = {
                "chat_id": target_channel,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            response = self.session.post(f"{base_url}/sendMessage", json=payload, timeout=10)
            result = response.json()
            
            if response.ok and result.get("ok"):
                return True
            else:
                desc = result.get("description", "Unknown Telegram Error")
                log.error(f"Error sending message to {target_channel}: {response.status_code} - {desc}")
                return False
        except Exception as e:
            log.error(f"Error sending message to {target_channel}: {e}")
            return False

    def notify_signal(self, symbol: str, direction: str, entry: float, sl: float, tp: float, trend: str, timestamp, targets: List[str] = ["1", "2"]) -> bool:
        """Sends signal entries to targeted signal channels"""
        signal_data = dict(symbol=symbol, direction=direction, entry=entry, sl=sl, tp=tp, trend=trend, timestamp=timestamp)
        msg_text = self._format_signal_message(signal_data)

        res1 = self.send_message(msg_text, self.base_url_1, self.signal_channel_1) if ("1" in targets and self.enabled_1) else False
        res2 = self.send_message(msg_text, self.base_url_2, self.signal_channel_2) if ("2" in targets and self.enabled_2) else False
        res3 = self.send_message(msg_text, self.base_url_3, self.signal_channel_3) if ("3" in targets and self.enabled_3) else False

        return res1 or res2 or res3

    def notify_news_warning(self, event_title: str, news_time_str: str) -> bool:
        """Alerts ALL 3 signal channels prior to High Impact USD News releases"""
        msg = f"""
🚨 <b>HIGH IMPACT USD NEWS ALERT</b> 🚨

<b>Event:</b> {event_title}
<b>Scheduled Time:</b> {news_time_str}

⚠️ <i>High impact USD news coming up in 1 hour! Signal generation are temporarily paused.</i>
"""
        res1 = self.send_message(msg, self.base_url_1, self.signal_channel_1) if self.enabled_1 else False
        res2 = self.send_message(msg, self.base_url_2, self.signal_channel_2) if self.enabled_2 else False
        res3 = self.send_message(msg, self.base_url_3, self.signal_channel_3) if self.enabled_3 else False

        return res1 or res2 or res3

    def notify_trade_result(
        self, symbol: str, direction: str, result: str, pnl_pts: float, 
        entry: float, exit_price: float, trend: str, targets: List[str] = ["1", "2", "3"]
    ) -> bool:
        """Handles post-trade outcomes across channels"""
        trade_data = dict(symbol=symbol, direction=direction, result=result, pnl_pts=pnl_pts, entry=entry, exit_price=exit_price, trend=trend)
        msg_text = self._format_trade_result_message(trade_data)
        is_win = (result.upper() == 'WIN')

        if self.enabled_1:
            if "1" in targets:
                self.send_message(msg_text, self.base_url_1, self.signal_channel_1)
            
            if is_win and self.results_channel_1:
                self.send_message(msg_text, self.base_url_1, self.results_channel_1)
                promo_1 = self._format_promo_message(self.invite_link_1 or "https://t.me/")
                self.send_message(promo_1, self.base_url_1, self.results_channel_1)

        if self.enabled_2:
            if "2" in targets:
                self.send_message(msg_text, self.base_url_2, self.signal_channel_2)
            
            if is_win and self.results_channel_2:
                self.send_message(msg_text, self.base_url_2, self.results_channel_2)
                promo_2 = self._format_promo_message(self.invite_link_2 or self.invite_link_1 or "https://t.me/")
                self.send_message(promo_2, self.base_url_2, self.results_channel_2)

        if self.enabled_3 and ("3" in targets):
            self.send_message(msg_text, self.base_url_3, self.signal_channel_3)

        return True

    def _format_promo_message(self, invite_link: str) -> str:
        return f"""
🔥 <b>ANOTHER WINNING TRADE!</b> 🔥

Want real-time entry signals, full analysis, and exact TP/SL setups before trades trigger for free?

📥 <b>Join our VIP Main Group now:</b>
👉 <a href="{invite_link}">Click Here to Join Channel</a>
"""

    def _format_signal_message(self, signal_data: Dict[str, Any]) -> str:
        symbol = signal_data.get('symbol', 'GOLD').upper()
        direction = signal_data.get('direction', '').upper()
        entry = signal_data.get('entry', 0)
        sl = signal_data.get('sl', 0)
        tp = signal_data.get('tp', 0)
        trend = signal_data.get('trend', '').upper()

        risk = abs(sl - entry)
        reward = abs(tp - entry)
        rr_ratio = reward / risk if risk > 0 else 0

        emoji = '🟢' if direction == 'LONG' else '🔴'
        direction_emoji = '📈' if direction == 'LONG' else '📉'

        return f"""
{emoji} <b>NEW {symbol} TRADE SIGNAL</b> {direction_emoji}

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction}
<b>Entry Price:</b> ${entry:.2f}
<b>Stop Loss:</b> ${sl:.2f}
<b>Take Profit:</b> ${tp:.2f}
<b>Risk:Reward:</b> 1:{rr_ratio:.1f}

<b>Trend Context:</b> {trend}

⚠️ <i>Risk Management: 1:1 R:R ratio applied</i>
"""

    def _format_trade_result_message(self, trade_data: Dict[str, Any]) -> str:
        symbol = trade_data.get('symbol', 'GOLD').upper()
        direction = trade_data.get('direction', '').upper()
        result = trade_data.get('result', '').upper()
        pnl = trade_data.get('pnl_pts', 0)
        entry = trade_data.get('entry', 0)
        exit_price = trade_data.get('exit_price', 0)
        trend = trade_data.get('trend', '').upper()

        if result == 'WIN':
            emoji = '✅'
            result_text = 'PROFIT'
        elif result == 'LOSS':
            emoji = '❌'
            result_text = 'LOSS'
        else:
            emoji = '⚪'
            result_text = 'CLOSED'

        direction_emoji = '📈' if direction == 'LONG' else '📉'

        return f"""
{emoji} <b>{symbol} TRADE {result_text}</b> {direction_emoji}

<b>Symbol:</b> {symbol}
<b>Direction:</b> {direction}
<b>Entry:</b> ${entry:.2f}
<b>Exit:</b> ${exit_price:.2f}
<b>PnL:</b> {pnl:+.2f} pts

<b>Trend Context:</b> {trend}
"""

def create_telegram_bot_from_env() -> Optional[TelegramSignalBot]:
    return TelegramSignalBot()