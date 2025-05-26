import subprocess
import time
import threading
import os
import logging
import configparser

from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import app, start_async_handler, set_uploader_log_handler

# ========== 配置区域 ==========

CONFIG_FILE = "config/config.ini"
CHANNELS_FILE = "config/channels.ini"
SUBSCRIBED_FILE = os.path.join("utils", "subscribed_channels.json")  # 用于记录上次订阅的频道
ERROR_LOG_FILE = "subscription_error.log"      # 失败报警日志文件
NGROK_PATH = "ngrok.exe"
NGROK_PORT = 8000
CALLBACK_PATH = "/youtube/callback"
# =============================

# 你自己的 ngrok 域名（HTTP/TLS）
NGROK_CUSTOM_DOMAIN = "miaoshahao.ngrok.app"

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
    conf.optionxform = str  # 保持 key 的原始大小写
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
    """
    使用自定义域名启动 ngrok，返回进程对象和公网地址。
    """
    print("[*] 正在启动 ngrok...")

    # HTTP 域名方式
    ngrok_proc = subprocess.Popen(
        [NGROK_PATH, "http", "--domain", NGROK_CUSTOM_DOMAIN, str(NGROK_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT
    )
    public_url = f"https://{NGROK_CUSTOM_DOMAIN}"

    print(f"[✓] 获取到 ngrok 地址: {public_url}")
    return ngrok_proc, public_url

def check_ngrok_public_url():
    """
    由于你已经固定自定义域名，理论上不必再检查 localhost:4040 状态。
    但为了兼容原有健康检查逻辑，这里保留，始终返回 True。
    """
    return True

def health_check_ngrok(get_ngrok_proc, restart_callback, interval=60):
    """
    get_ngrok_proc: 一个函数，返回当前 ngrok_proc 对象
    restart_callback: 一个函数，调用后会重启 ngrok 并返回 (ngrok_proc, public_url)
    """
    while True:
        ngrok_proc = get_ngrok_proc()
        # 1. 检查进程
        if ngrok_proc.poll() is not None:
            print("[!] ngrok 进程已退出，重启中...")
            restart_callback()
            time.sleep(3)
            continue
        # 2. 检查公网地址（自定义域名模式下总是 True）
        if not check_ngrok_public_url():
            print("[!] ngrok 公网地址不可用，重启中...")
            ngrok_proc.terminate()
            restart_callback()
            time.sleep(3)
            continue
        time.sleep(interval)

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

    # 用于健康检查的闭包
    state = {"ngrok_proc": ngrok_proc, "public_url": public_url}
    def get_ngrok_proc():
        return state["ngrok_proc"]
    def restart_ngrok():
        ngrok_proc, public_url = start_ngrok()
        state["ngrok_proc"] = ngrok_proc
        state["public_url"] = public_url
        print(f"[✓] ngrok 已重启，新的 Callback URL: {public_url + CALLBACK_PATH}")

    # 启动健康检查线程
    threading.Thread(
        target=health_check_ngrok,
        args=(get_ngrok_proc, restart_ngrok, 60),
        daemon=True
    ).start()

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
        state["ngrok_proc"].terminate()

if __name__ == "__main__":
    main()
