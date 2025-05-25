import time
import requests
import threading
import os
import logging
import configparser
import subprocess
import sys

from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import app, start_async_handler, set_uploader_log_handler

# ========== 配置区域 ==========
CONFIG_FILE = "config/config.ini"
CHANNELS_FILE = "config/channels.ini"
SUBSCRIBED_FILE = os.path.join("utils", "subscribed_channels.json")  # 用于记录上次订阅的频道
ERROR_LOG_FILE = "subscription_error.log"      # 失败报警日志文件

CALLBACK_PATH = "/youtube/callback"
DEFAULT_PORT = 8000
CLOUDFLARED_EXE = "cloudflared.exe"  # cloudflared 文件名
# =============================

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    cfg = {}
    if "global" in config:
        cfg["youtube_api_key"] = config.get("global", "youtube_api_key", fallback="")
        cfg["proxy"] = config.get("global", "proxy", fallback=None)
    if "cloudflared" in config:
        cfg["public_url"] = config.get("cloudflared", "public_url", fallback=None)
        cfg["port"] = config.getint("cloudflared", "port", fallback=DEFAULT_PORT)
        cfg["tunnel_name"] = config.get("cloudflared", "tunnel_name", fallback=None)
    else:
        cfg["public_url"] = None
        cfg["port"] = DEFAULT_PORT
        cfg["tunnel_name"] = None
    return cfg

def load_channels():
    conf = configparser.ConfigParser(allow_no_value=True)
    conf.read(CHANNELS_FILE, encoding="utf-8")
    if "channels" in conf:
        return [k for k in conf["channels"].keys()]
    return []

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

def start_cloudflared(tunnel_name):
    cloudflared_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CLOUDFLARED_EXE)
    if not os.path.exists(cloudflared_path):
        print(f"[!] 未找到 cloudflared 可执行文件: {cloudflared_path}")
        return None
    if not tunnel_name:
        print("[!] 配置文件未设置 tunnel_name，无法启动 cloudflared")
        return None
    cmd = [cloudflared_path, "tunnel", "run", tunnel_name]
    print(f"[*] 启动 cloudflared: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc

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

    public_url = config.get("public_url")
    port = config.get("port", DEFAULT_PORT)
    tunnel_name = config.get("tunnel_name")

    # 1. 启动 cloudflared 隧道
    cloudflared_proc = start_cloudflared(tunnel_name)
    if not cloudflared_proc:
        print("[!] cloudflared 未能启动，程序退出")
        return
    print("[✓] cloudflared 隧道已启动")

    if not public_url:
        print("[!] 配置文件未设置 cloudflared 的 public_url")
        # 停止 cloudflared
        if cloudflared_proc:
            cloudflared_proc.terminate()
        return

    callback_url = public_url.rstrip("/") + CALLBACK_PATH
    print(f"[✓] 最终 Callback URL: {callback_url}")

    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False))
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
        print("[✓] 程序被中断，退出...")
    finally:
        if cloudflared_proc:
            cloudflared_proc.terminate()
            print("[*] cloudflared 已关闭")

if __name__ == "__main__":
    main()
