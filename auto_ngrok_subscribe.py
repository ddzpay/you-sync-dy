import subprocess
import time
import requests
import threading
import os
import logging
import configparser

from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import app, start_async_handler, set_uploader_log_handler

# ========== 配置区域 ==========

CONFIG_FILE = "conf/config.ini"
CHANNELS_FILE = "conf/channels.ini"
SUBSCRIBED_FILE = "subscribed_channels.json"  # 用于记录上次订阅的频道
ERROR_LOG_FILE = "subscription_error.log"      # 失败报警日志文件
NGROK_PATH = "ngrok.exe"
NGROK_PORT = 8000
CALLBACK_PATH = "/youtube/callback"
# =============================

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    cfg = {}
    if "global" in config:
        cfg["youtube_api_key"] = config.get("global", "youtube_api_key", fallback="")
        cfg["proxy"] = config.get("global", "proxy", fallback=None)
        cfg["ngrok_authtoken"] = config.get("global", "ngrok_authtoken", fallback=None)
    return cfg

def load_channels():
    conf = configparser.ConfigParser(allow_no_value=True)
    conf.read(CHANNELS_FILE, encoding="utf-8")
    if "channels" in conf:
        return [k for k in conf["channels"].keys()]
    return []

def ensure_ngrok_authtoken(authtoken):
    config_dir = os.path.expanduser("~/.ngrok2")
    config_file = os.path.join(config_dir, "ngrok.yml")
    if not os.path.exists(config_file) or authtoken not in open(config_file, encoding="utf-8").read():
        subprocess.run([NGROK_PATH, "config", "add-authtoken", authtoken])
        print("[*] ngrok authtoken 已配置")
    else:
        print("[*] ngrok authtoken 已存在，无需重复配置")

def start_ngrok():
    print("[*] 正在启动 ngrok...")
    ngrok_proc = subprocess.Popen([NGROK_PATH, "http", str(NGROK_PORT)],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.STDOUT)
    # 等待ngrok启动并获取公网地址（重试机制）
    for _ in range(10):
        time.sleep(1.5)
        try:
            res = requests.get("http://localhost:4040/api/tunnels").json()
            tunnels = res.get('tunnels')
            if tunnels and len(tunnels) > 0 and 'public_url' in tunnels[0]:
                public_url = tunnels[0]['public_url']
                print(f"[✓] 获取到 ngrok 地址: {public_url}")
                return ngrok_proc, public_url
        except Exception:
            continue
    print("[!] 无法获取 ngrok 公网地址")
    ngrok_proc.terminate()
    exit(1)

def load_previous_subscribed_channels():
    import json
    if os.path.exists(SUBSCRIBED_FILE):
        with open(SUBSCRIBED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_subscribed_channels(channel_id_set):
    import json
    with open(SUBSCRIBED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(channel_id_set)), f, indent=2, ensure_ascii=False)

def alarm_on_failure(action, channel_id, callback_url):
    msg = f"[ALERT] {action} 失败: channel_id={channel_id}, callback_url={callback_url}"
    logging.error(msg)
    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def sync_subscriptions(callback_url, channels):
    previous_channels = load_previous_subscribed_channels()
    current_channels = set(channels)
    for cid in current_channels - previous_channels:
        success, msg = subscribe_channel(cid, callback_url)
        print(msg)
        if not success:
            alarm_on_failure("订阅", cid, callback_url)
    for cid in previous_channels - current_channels:
        success, msg = unsubscribe_channel(cid, callback_url)
        print(msg)
        if not success:
            alarm_on_failure("取消订阅", cid, callback_url)
    save_subscribed_channels(current_channels)

def print_log(msg):
    print(msg)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("auto_ngrok_subscribe.log", encoding="utf-8")
        ]
    )

    config = load_config()
    channels = load_channels()

    authtoken = config.get("ngrok_authtoken", "")
    ensure_ngrok_authtoken(authtoken)
    ngrok_proc, public_url = start_ngrok()
    callback_url = public_url + CALLBACK_PATH
    print(f"[✓] 最终 Callback URL: {callback_url}")

    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=NGROK_PORT, debug=False))
    flask_thread.daemon = True
    flask_thread.start()
    print("[*] Webhook 服务器已启动")

    set_uploader_log_handler(print_log)
    async_thread = threading.Thread(target=start_async_handler)
    async_thread.daemon = True
    async_thread.start()
    print("[*] 异步处理/上传线程已启动")

    sync_subscriptions(callback_url, channels)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("[✓] 程序被中断，关闭 ngrok...")
        ngrok_proc.terminate()

if __name__ == "__main__":
    main()
