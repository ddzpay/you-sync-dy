import subprocess
import time
import threading
import os
import logging
import configparser
import json
import requests
import asyncio
import logging.handlers
import queue
import aiohttp
from datetime import datetime, timedelta, timezone
import uvicorn
from subscribe import subscribe_channel, unsubscribe_channel
from webhook_server import (
    app, 
    set_uploader_log_handler, 
    video_id_queue, 
    init_async_globals,
    async_handler_task
)

# ========== 配置区域 ==========
CONFIG_FILE = "config/config.ini"
CHANNELS_FILE = "config/channels.ini"
SUBSCRIBED_FILE = os.path.join("config", "subscribed_channels.json")
LAST_RENEW_TIME_FILE = os.path.join("config", "last_renew_time.json")
ERROR_LOG_FILE = "log/subscription_error.log"
FRPC_PATH = os.path.join("tools", "frpc.exe")  # Windows 下用 frpc.exe
FRPC_INI = os.path.join("config", "frpc.ini")   # 修改为 frpc.ini
FRP_PORT = 8001   # 本地服务监听端口，和 frpc.ini 一致
CALLBACK_PATH = "/youtube/callback"
FRP_CUSTOM_DOMAIN = "shijuezhentan.frps.miaoshark.com"  # 你的穿透域名
FRP_PUBLIC_PORT = 443  # 你公网访问的端口，nginx反代一般是443
# =============================

def setup_logging():
    log_dir = "log"
    os.makedirs(log_dir, exist_ok=True)
    log_queue = queue.Queue(-1)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "auto_frp_subscribe.log"),
        maxBytes=50*1024*1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", '%Y-%m-%d %H:%M:%S'))
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", '%Y-%m-%d %H:%M:%S'))

    listener = logging.handlers.QueueListener(log_queue, file_handler, stream_handler)
    listener.start()

    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = []
    root_logger.addHandler(queue_handler)

# 增加主动flush方法
def flush_all(logger=None):
    logger = logger or logging.getLogger()
    for h in logger.handlers:
        try:
            h.flush()
        except Exception as e:
            print(f"Flush 失败: {e}")

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
    if os.path.exists(SUBSCRIBED_FILE):
        with open(SUBSCRIBED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_subscribed_channels(channel_id_set):
    with open(SUBSCRIBED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(channel_id_set)), f, indent=2, ensure_ascii=False)

def alarm_on_failure(action, channel_id, callback_url):
    msg = f"[ALERT] {action} 失败: channel_id={channel_id}, callback_url={callback_url}"
    logging.error(msg)
    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

async def async_sync_subscriptions(callback_url, channels):
    previous_channels = load_previous_subscribed_channels()
    current_channels = set(channels)
    
    # 订阅新频道
    for cid in current_channels - previous_channels:
        success, msg = await subscribe_channel(cid, callback_url)
        logging.info(msg)
        if not success:
            alarm_on_failure("订阅", cid, callback_url)
        await asyncio.sleep(4)  # 异步等待，不阻塞主线程
    
    # 取消订阅移除的频道
    for cid in previous_channels - current_channels:
        success, msg = await unsubscribe_channel(cid, callback_url)
        logging.info(msg)
        if not success:
            alarm_on_failure("取消订阅", cid, callback_url)
        await asyncio.sleep(4)
    
    save_subscribed_channels(current_channels)

def sync_subscriptions(callback_url, channels):
    asyncio.run(async_sync_subscriptions(callback_url, channels))

#续订频道===================================================================================
def save_last_renew_time(ts=None):
    if ts is None:
        ts = int(time.time())
    with open(LAST_RENEW_TIME_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_renew_time": ts}, f)

def load_last_renew_time():
    if os.path.exists(LAST_RENEW_TIME_FILE):
        with open(LAST_RENEW_TIME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return int(data.get("last_renew_time", 0))
    return 0

def start_renew_subscription_loop(callback_url, interval_hours=24*3):
    async def renew_loop():
        while True:
            try:
                interval_seconds = interval_hours * 3600
                now = int(time.time())
                last_renew = load_last_renew_time()
                elapsed = now - last_renew
                if elapsed < interval_seconds and last_renew > 0:
                    sleep_time = interval_seconds - elapsed
                    logging.info(f"[✓] 距离下次自动续订还有 {sleep_time//3600} 小时 {sleep_time%3600//60} 分")
                    await asyncio.sleep(sleep_time)
                channel_ids = load_previous_subscribed_channels()
                logging.info(f"[✓] 正在自动续订频道...")
                renew_count = 0  # 新增：统计续订数量
                for cid in channel_ids:
                    try:
                        success, msg = await subscribe_channel(cid, callback_url)
                        logging.info(msg)
                        if success:
                            renew_count += 1
                    except Exception as e:
                        logging.exception(f"频道 {cid} 续订异常: {e}")
                    await asyncio.sleep(4)
                save_last_renew_time()
                logging.info(f"[✓] 本轮共续订了 {renew_count} 个频道。")  # 新增：输出统计结果
                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logging.exception(f"自动续订主循环异常: {e}")
                await asyncio.sleep(60)
    threading.Thread(target=lambda: asyncio.run(renew_loop()), daemon=True).start()
#===========================================================================================

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
        try:
            time.sleep(600)
            uptime = int(time.time() - start_time)
            h = uptime // 3600
            m = (uptime % 3600) // 60
            logging.info(f"[✓] 系统运行正常 - 运行时间: {h}小时{m}分钟")
        except Exception as e:
            logging.exception(f"[status_monitor exception]: {e}")

def wait_webhook_ready(url, timeout=20):
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

    set_uploader_log_handler(lambda msg: logging.info(msg))

    # --------- 启动本地 Web 服务 ---------
    uvicorn_started = threading.Event()
    def start_uvicorn():
        logging.info(f"Serving on http://0.0.0.0:{FRP_PORT}")
        uvicorn_started.set()
        uvicorn.run("webhook_server:app", host="0.0.0.0", port=FRP_PORT, workers=1, log_level="warning")

    uvicorn_thread = threading.Thread(target=start_uvicorn)
    uvicorn_thread.daemon = True
    uvicorn_thread.start()

    uvicorn_started.wait()
    print_startup_banner(public_url)

    # 新增：确保 webhook 服务 ready 再发起订阅
    wait_webhook_ready(f"http://127.0.0.1:{FRP_PORT}/healthz")

    # 状态监控线程（防止睡眠）
    start_time = time.time()
    threading.Thread(target=status_monitor, args=(start_time,), daemon=True).start()

    # === 这里插入后台定时 flush 线程 ===
    def start_periodic_flush(interval=2):
        def periodic_flush():
            while True:
                time.sleep(interval)
                flush_all()
        threading.Thread(target=periodic_flush, daemon=True).start()

    # 启动
    start_periodic_flush()
    # === 插入结束 ===

    # 等待其他线程输出完初始化日志再执行订阅
    time.sleep(3)

    sync_subscriptions(callback_url, channels)

    #启动续订线程
    start_renew_subscription_loop(callback_url)
    
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("[✓] 程序被中断，关闭 frpc...")
        state["frpc_proc"].terminate()

if __name__ == "__main__":
    main()