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
ERROR_LOG_FILE = "log/subscription_error.log"
FRPC_PATH = os.path.join("tools", "frpc.exe")  # Windows 下用 frpc.exe
FRPC_INI = os.path.join("tools", "frpc.ini")   # 修改为 frpc.ini
FRP_PORT = 8000   # 本地服务监听端口，和 frpc.ini 一致
CALLBACK_PATH = "/youtube/callback"
FRP_CUSTOM_DOMAIN = "frp.miaoshark.com"  # 你的穿透域名
FRP_PUBLIC_PORT = 443  # 你公网访问的端口，nginx反代一般是443
# =============================

def setup_logging():
    """统一日志配置"""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("log/auto_frp_subscribe.log", encoding="utf-8", mode="w")
        ]
    )

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    cfg = {}
    if "global" in config:
        cfg["youtube_api_key"] = config.get("global", "youtube_api_key", fallback="")
    return cfg

def load_channels():
    conf = configparser.ConfigParser(allow_no_value=True)
    conf.optionxform = str
    conf.read(CHANNELS_FILE, encoding="utf-8")
    if "channels" in conf:
        return [k for k in conf["channels"].keys()]
    return []

def start_frpc():
    logging.info("正在启动 frpc...")
    # 启动 frpc，配置文件为 tools/frpc.ini
    frpc_proc = subprocess.Popen(
        [FRPC_PATH, "-c", FRPC_INI],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT
    )
    # 这里假设你用 Nginx 反代后公网就是 https://frp.miaoshark.com
    public_url = f"https://{FRP_CUSTOM_DOMAIN}"
    logging.info(f"获取到 frp 公网地址: {public_url}")
    return frpc_proc, public_url

def health_check_frpc(get_frpc_proc, restart_callback, interval=60):
    while True:
        frpc_proc = get_frpc_proc()
        if frpc_proc.poll() is not None:
            logging.warning("frpc 进程已退出，重启中...")
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
    print("YouTube同步抖音自动化系统")
    print("="*60)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Webhook地址: {public_url}/youtube/callback")
    print(f"本地端口: http://127.0.0.1:{FRP_PORT}")
    print("="*60)
    print("温馨提示: 如要退出系统，请先按 Ctrl+C 关闭 frpc 服务，再关闭CMD控制台窗口")
    print("="*60 + "\n")

def status_monitor(start_time):
    while True:
        time.sleep(600)  # 10分钟
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

    # 启动 frpc
    frpc_proc, public_url = start_frpc()
    callback_url = public_url + CALLBACK_PATH
    logging.info("Webhook 服务器已启动")

    state = {"frpc_proc": frpc_proc, "public_url": public_url}

    def get_frpc_proc():
        return state["frpc_proc"]

    def restart_frpc():
        frpc_proc, public_url = start_frpc()
        state["frpc_proc"] = frpc_proc
        state["public_url"] = public_url
        logging.info(f"frpc 已重启，新的 Callback URL: {public_url + CALLBACK_PATH}")

    # 健康检测线程，防止 frpc 异常退出
    threading.Thread(
        target=health_check_frpc,
        args=(get_frpc_proc, restart_frpc, 60),
        daemon=True
    ).start()

    # --------- 启动本地 Web 服务 ---------
    waitress_started = threading.Event()
    def start_waitress():
        logging.info(f"Serving on http://0.0.0.0:{FRP_PORT}")
        waitress_started.set()
        serve(app, host="0.0.0.0", port=FRP_PORT, threads=6)

    flask_thread = threading.Thread(target=start_waitress)
    flask_thread.daemon = True
    flask_thread.start()

    set_uploader_log_handler(lambda msg: logging.info(msg))
    async_thread = threading.Thread(target=start_async_handler)
    async_thread.daemon = True
    async_thread.start()
    logging.info("异步处理/上传线程已启动")

    waitress_started.wait()
    print_startup_banner(public_url)

    # 新增：确保 webhook 服务 ready 再发起订阅
    wait_webhook_ready(f"http://127.0.0.1:{FRP_PORT}/healthz")

    # 状态监控线程（防止睡眠）
    start_time = time.time()
    threading.Thread(target=status_monitor, args=(start_time,), daemon=True).start()
    
    # 等待其他线程输出完初始化日志再执行订阅
    time.sleep(3)

    sync_subscriptions(callback_url, channels)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("[✓] 程序被中断，关闭 frpc...")
        state["frpc_proc"].terminate()

if __name__ == "__main__":
    main()