import requests

def notify_wecom_group(msg, webhook_url):
    """
    企业微信群聊机器人告警推送
    :param msg: 告警内容字符串
    :param webhook_url: 企业微信群机器人 webhook 地址
    :return: True/False
    """
    payload = {
        "msgtype": "text",
        "text": {
            "content": msg
        }
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        return resp.status_code == 200 and resp.json().get("errcode", -1) == 0
    except Exception as e:
        print(f"[!] 企业微信推送失败: {e}")
        return False