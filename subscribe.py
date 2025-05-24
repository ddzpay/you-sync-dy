import requests

def subscribe_channel(channel_id: str, callback_url: str):
    _submit_subscription(channel_id, callback_url, mode="subscribe")

def unsubscribe_channel(channel_id: str, callback_url: str):
    _submit_subscription(channel_id, callback_url, mode="unsubscribe")

def _submit_subscription(channel_id: str, callback_url: str, mode: str):
    hub_url = 'https://pubsubhubbub.appspot.com/subscribe'
    topic = f'https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}'
    data = {
        'hub.mode': mode,
        'hub.topic': topic,
        'hub.callback': callback_url,
        'hub.verify': 'async'
    }

    try:
        resp = requests.post(hub_url, data=data, timeout=10)
        if resp.status_code == 202:
            print(f"[✓] {mode.upper()} 成功: {channel_id}")
        else:
            print(f"[!] {mode.upper()} 失败: {resp.status_code} - {resp.text}")
    except requests.exceptions.RequestException as e:
        print(f"[!] 网络异常 ({mode}): {e}")
