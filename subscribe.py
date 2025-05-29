import requests
import logging
import time

def subscribe_channel(channel_id: str, callback_url: str) -> tuple[bool, str]:
    return _submit_subscription(channel_id, callback_url, mode="subscribe")

def unsubscribe_channel(channel_id: str, callback_url: str) -> tuple[bool, str]:
    return _submit_subscription(channel_id, callback_url, mode="unsubscribe")

def _submit_subscription(channel_id: str, callback_url: str, mode: str, retry: int = 3, delay: int = 3) -> tuple[bool, str]:
    hub_url = 'https://pubsubhubbub.appspot.com/subscribe'
    topic = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    data = {
        'hub.mode': mode,
        'hub.topic': topic,
        'hub.callback': callback_url,
        'hub.verify': 'async'
    }
    for attempt in range(retry):
        try:
            resp = requests.post(hub_url, data=data, timeout=10)
            if resp.status_code == 202:
                msg = f"[✓] {mode.upper()} 成功: {channel_id}"
                logging.info(msg)
                return True, msg
            else:
                msg = f"[!] {mode.upper()} 失败: {resp.status_code} - {resp.text}"
                logging.warning(msg)
        except requests.exceptions.RequestException as e:
            msg = f"[!] 网络异常 ({mode}, 尝试 {attempt+1}/{retry}): {e}"
            logging.warning(msg)
        if attempt < retry - 1:
            time.sleep(delay)
    msg = f"[!] {mode.upper()} 最终失败: {channel_id}"
    logging.error(msg)
    return False, msg
