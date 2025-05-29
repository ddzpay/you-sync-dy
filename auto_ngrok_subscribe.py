import subprocess
import time
import threading
import os
import logging
import configparser
from datetime import datetime
from waitress import serve
from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import app, start_async_handler, set_uploader_log_handler, video_id_queue

# ========== 配置区域 ==========
CONFIG_FILE = "config/config.ini"
CHANNELS_FILE = "config/channels.ini"
SUBSCRIBED_FILE = os.path.join("utils", "subscribed_channels.json")
ERROR_LOG_FILE = "subscription_error.log"
NGROK_PATH = "ngrok.exe"
NGROK_PORT = 8000
CALLBACK_PATH = "/youtube/callback"
NGROK_CUSTOM_DOMAIN = "miaoshahao.ngrok.app"
# =============================

def setup_logging():
    """统一日志配置"""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("auto_ngrok_subscribe.log", encoding="utf-8")
        ]
    )

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    cfg = {}
    if "global" in config:
        cfg["youtube_api_key"] = config.get("global", "youtube_api_key", fallback="")
        cfg["ngrok_authtoken"] = config.get("global", "ngrok_authtoken", fallback=None)
    return cfg

def load_channels():
    conf = configparser.ConfigParser(allow_no_value=True)
    conf.optionxform = str
    conf.read(CHANNELS_FILE, encoding="utf-8")
    if "channels" in conf:
        return [k for k in conf["channels"].keys()]
    return []

def ensure_ngrok_authtoken(authtoken):
    config_dir = os.path.expanduser("~/.ngrok2")
    config_file = os.path.join(config_dir, "ngrok.yml")
    if not os.path.exists(config_file) or authtoken not in open(config_file, encoding="utf-8").read():
        subprocess.run([NGROK_PATH, "config", "add-authtoken", authtoken])
        logging.info("ngrok authtoken 已配置")
    else:
        logging.info("ngrok authtoken 已存在，无需重复配置")

def start_ngrok():
    logging.info("正在启动 ngrok...")
    ngrok_proc = subprocess.Popen(
        [NGROK_PATH, "http", "--domain", NGROK_CUSTOM_DOMAIN, str(NGROK_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT
    )
    public_url = f"https://{NGROK_CUSTOM_DOMAIN}"
    logging.info(f"获取到 ngrok 地址: {public_url}")
    return ngrok_proc, public_url

def check_ngrok_public_url():
    return True

def health_check_ngrok(get_ngrok_proc, restart_callback, interval=60):
    while True:
        ngrok_proc = get_ngrok_proc()
        if ngrok_proc.poll() is not None:
            logging.warning("ngrok 进程已退出，重启中...")
            restart_callback()
            time.sleep(3)
            continue
        if not check_ngrok_public_url():
            logging.warning("ngrok 公网地址不可用，重启中...")
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
        # 只在这里输出日志，不在 subscribe.py 输出
        logging.info(msg)
        if not success:
            alarm_on_failure("订阅", cid, callback_url)
    for cid in previous_channels - current_channels:
        success, msg = unsubscribe_channel(cid, callback_url)
        logging.info(msg)
        if not success:
            alarm_on_failure("取消订阅", cid, callback_url)
    save_subscribed_channels(current_channels)

def print_startup_banner(public_url):
    print("\n" + "="*60)
    print("you sync dy 自动转载系统")
    print("="*60)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Webhook地址: {public_url}/youtube/callback")
    print(f"本地服务: http://127.0.0.1:{NGROK_PORT}")
    print("="*60)
    print("温馨提示: 如要退出系统，请先按 Ctrl+C 关闭ngrok服务，再关闭（X）CMD控制台")
    print("="*60 + "\n")

def status_monitor(start_time):
    while True:
        time.sleep(300)  # 5分钟
        uptime = int(time.time() - start_time)
        h = uptime // 3600
        m = (uptime % 3600) // 60
        logging.info(f"[✓] 系统运行正常 - 运行时间: {h}小时{m}分钟")

def wait_webhook_ready(url, timeout=10):
    import requests
    for _ in range(timeout):
        try:
            r = requests.get(url)
            if r.status_code in (200, 400):
                return True
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Webhook 服务未准备好")

def main():
    setup_logging()
    config = load_config()
    channels = load_channels()

    authtoken = config.get("ngrok_authtoken", "")
    ensure_ngrok_authtoken(authtoken)

    ngrok_proc, public_url = start_ngrok()
    callback_url = public_url + CALLBACK_PATH
    logging.info("Webhook 服务器已启动")

    state = {"ngrok_proc": ngrok_proc, "public_url": public_url}

    def get_ngrok_proc():
        return state["ngrok_proc"]

    def restart_ngrok():
        ngrok_proc, public_url = start_ngrok()
        state["ngrok_proc"] = ngrok_proc
        state["public_url"] = public_url
        logging.info(f"ngrok 已重启，新的 Callback URL: {public_url + CALLBACK_PATH}")

    threading.Thread(
        target=health_check_ngrok,
        args=(get_ngrok_proc, restart_ngrok, 60),
        daemon=True
    ).start()

    # --------- 日志顺序控制 ---------
    waitress_started = threading.Event()
    def start_waitress():
        # 加一条和waitress原生输出内容一致的日志
        logging.info(f"Serving on http://0.0.0.0:{NGROK_PORT}")
        waitress_started.set()
        serve(app, host="0.0.0.0", port=NGROK_PORT, threads=6)

    flask_thread = threading.Thread(target=start_waitress)
    flask_thread.daemon = True
    flask_thread.start()

    set_uploader_log_handler(lambda msg: logging.info(msg))
    async_thread = threading.Thread(target=start_async_handler)
    async_thread.daemon = True
    async_thread.start()
    logging.info("异步处理/上传线程已启动")

    # 等待waitress日志输出再打印横幅
    waitress_started.wait()
    print_startup_banner(public_url)

    # 新增：确保 webhook 服务 ready 再发起订阅
    wait_webhook_ready(f"http://127.0.0.1:{NGROK_PORT}/youtube/callback")

    # 状态监控线程（防止睡眠）
    start_time = time.time()
    threading.Thread(target=status_monitor, args=(start_time,), daemon=True).start()

    sync_subscriptions(callback_url, channels)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("[✓] 程序被中断，关闭 ngrok...")
        state["ngrok_proc"].terminate()

if __name__ == "__main__":
    main()