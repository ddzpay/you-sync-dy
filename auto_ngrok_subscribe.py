import subprocess
import time
import requests
import json
import threading
import os
import asyncio

from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import app, start_async_handler, set_uploader_log_handler
from utils.douyin_uploader import DouyinUploader

# ========== 配置区域 ==========
NGROK_PATH = "ngrok.exe"
AUTHTOKEN = "2vkOXZqlvneXRXUm8SMacHRQLMh_6N4se6VioNdCwSiCfVkFW"
NGROK_PORT = 8000
CALLBACK_PATH = "/youtube/callback"
CONFIG_FILE = "config.json"
SUBSCRIBED_FILE = "subscribed_channels.json"  # 用于记录上次订阅的频道
# =============================

def ensure_ngrok_authtoken():
    config_dir = os.path.expanduser("~/.ngrok2")
    config_file = os.path.join(config_dir, "ngrok.yml")
    if not os.path.exists(config_file) or AUTHTOKEN not in open(config_file, encoding="utf-8").read():
        subprocess.run([NGROK_PATH, "config", "add-authtoken", AUTHTOKEN])
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
            public_url = res['tunnels'][0]['public_url']
            print(f"[✓] 获取到 ngrok 地址: {public_url}")
            return ngrok_proc, public_url
        except Exception:
            continue
    print("[!] 无法获取 ngrok 公网地址")
    ngrok_proc.terminate()
    exit(1)

def start_flask():
    app.run(host="0.0.0.0", port=NGROK_PORT, debug=False)

def load_previous_subscribed_channels():
    if os.path.exists(SUBSCRIBED_FILE):
        with open(SUBSCRIBED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_subscribed_channels(channel_id_set):
    with open(SUBSCRIBED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(channel_id_set)), f, indent=2, ensure_ascii=False)

def sync_subscriptions(callback_url):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    current_channels = set(config.get("channels", []))
    previous_channels = load_previous_subscribed_channels()
    for cid in current_channels - previous_channels:
        subscribe_channel(cid, callback_url)
    for cid in previous_channels - current_channels:
        unsubscribe_channel(cid, callback_url)
    save_subscribed_channels(current_channels)

# 统一日志打印回调
def print_log(msg):
    print(msg)

def main():
    ensure_ngrok_authtoken()
    ngrok_proc, public_url = start_ngrok()
    callback_url = public_url + CALLBACK_PATH
    print(f"[✓] 最终 Callback URL: {callback_url}")

    # 1. 在新线程启动 Flask Server
    flask_thread = threading.Thread(target=start_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("[*] Webhook 服务器已启动")

    # 2. 启动异步处理线程（需先设置日志回调）
    set_uploader_log_handler(print_log)
    async_thread = threading.Thread(target=start_async_handler)
    async_thread.daemon = True
    async_thread.start()
    print("[*] 异步处理/上传线程已启动")

    # 3. 同步订阅状态
    sync_subscriptions(callback_url)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("[✓] 程序被中断，关闭 ngrok...")
        ngrok_proc.terminate()

if __name__ == "__main__":
    main()