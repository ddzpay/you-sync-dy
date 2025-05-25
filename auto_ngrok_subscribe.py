import time
import requests
import threading
import os
import logging
import configparser
import subprocess
import sys
import socket

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
CLOUDFLARED_LOG = "cloudflared.log"
CERT_PATH = os.path.abspath("config/cert.pem")  # <--- 新增
# =============================

def load_config():
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE, encoding="utf-8")
    except Exception as e:
        print(f"[!] 加载配置文件失败: {e}")
        sys.exit(1)
    cfg = {}
    try:
        if "global" in config:
            cfg["youtube_api_key"] = config.get("global", "youtube_api_key", fallback="")
            cfg["proxy"] = config.get("global", "proxy", fallback=None)
        else:
            print("[!] config.ini 缺少 [global] 节，请检查配置文件。")
            sys.exit(1)
        if "cloudflared" in config:
            cfg["public_url"] = config.get("cloudflared", "public_url", fallback=None)
            cfg["port"] = config.getint("cloudflared", "port", fallback=DEFAULT_PORT)
            cfg["tunnel_name"] = config.get("cloudflared", "tunnel_name", fallback=None)
        else:
            print("[!] config.ini 缺少 [cloudflared] 节，请检查配置文件。")
            cfg["public_url"] = None
            cfg["port"] = DEFAULT_PORT
            cfg["tunnel_name"] = None
    except Exception as e:
        print(f"[!] 解析配置文件异常: {e}")
        sys.exit(1)
    if not cfg.get("youtube_api_key"):
        print("[!] 缺少 youtube_api_key，程序无法启动。")
        sys.exit(1)
    return cfg

def load_channels():
    conf = configparser.ConfigParser(allow_no_value=True)
    try:
        conf.read(CHANNELS_FILE, encoding="utf-8")
    except Exception as e:
        print(f"[!] 加载频道配置失败: {e}")
        return []
    if "channels" in conf:
        return [k for k in conf["channels"].keys()]
    return []

def load_previous_subscribed_channels():
    import json
    try:
        if os.path.exists(SUBSCRIBED_FILE):
            with open(SUBSCRIBED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
    except Exception as e:
        print(f"[!] 读取已订阅频道失败: {e}")
    return set()

def save_subscribed_channels(channel_id_set):
    import json
    try:
        with open(SUBSCRIBED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(channel_id_set)), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] 保存订阅频道失败: {e}")

def alarm_on_failure(action, channel_id, callback_url):
    msg = f"[ALERT] {action} 失败: channel_id={channel_id}, callback_url={callback_url}"
    logging.error(msg)
    try:
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception as e:
        print(f"[!] 写入错误日志失败: {e}")

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

def is_port_available(port):
    """检测端口是否可用"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        s.close()
        return True
    except OSError:
        return False

def start_cloudflared(tunnel_name):
    cloudflared_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CLOUDFLARED_EXE)
    if not os.path.exists(cloudflared_path):
        print(f"[!] 未找到 cloudflared 可执行文件: {cloudflared_path}")
        return None
    if not tunnel_name:
        print("[!] 配置文件未设置 tunnel_name，无法启动 cloudflared")
        return None
    if not os.path.isfile(CERT_PATH):
        print(f"[!] 未找到 Cloudflare 认证证书: {CERT_PATH}")
        return None
    cmd = [cloudflared_path, "tunnel", "run", tunnel_name]
    print(f"[*] 启动 cloudflared: {' '.join(cmd)}")
    try:
        env = os.environ.copy()
        env["TUNNEL_ORIGIN_CERT"] = CERT_PATH  # <--- 关键行: 设置环境变量
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    except Exception as e:
        print(f"[!] 启动 cloudflared 失败: {e}")
        return None
    def print_log_thread():
        with open(CLOUDFLARED_LOG, "a", encoding="utf-8") as logf:
            for line in proc.stdout:
                print(f"[cloudflared] {line}", end="")
                logf.write(line)
    threading.Thread(target=print_log_thread, daemon=True).start()
    # 启动后等2秒判断是否崩溃
    time.sleep(2)
    if proc.poll() is not None:
        print("[!] cloudflared 启动失败，请检查 tunnel 名称和 Cloudflare 配置！详情见 cloudflared.log")
        return None
    return proc

def check_callback_url(callback_url, timeout=5):
    try:
        resp = requests.get(callback_url, timeout=timeout)
        if resp.status_code != 200:
            print(f"[!] Callback 地址 GET 返回状态码: {resp.status_code}")
            return False
        # 可选：POST 检查，模拟 PubSubHubbub 行为
        resp_post = requests.post(
            callback_url, 
            data="<test>xml</test>", 
            headers={"Content-Type": "application/xml"}, 
            timeout=timeout
        )
        if resp_post.status_code != 200:
            print(f"[!] Callback 地址 POST 返回状态码: {resp_post.status_code}")
            return False
        print(f"[✓] Callback 地址可用: {callback_url}")
        return True
    except Exception as e:
        print(f"[!] Callback 地址访问异常: {e}")
        return False

def ensure_single_instance(port):
    """防止多开。检测端口是否被占用。"""
    if not is_port_available(port):
        print(f"[!] 本地端口 {port} 已被占用，可能已有本程序实例在运行，请勿重复启动。")
        sys.exit(1)

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

    ensure_single_instance(port)

    # 1. 启动 cloudflared 隧道
    cloudflared_proc = start_cloudflared(tunnel_name)
    if not cloudflared_proc:
        print("[!] cloudflared 未能启动，程序退出")
        return
    print("[✓] cloudflared 隧道已启动")

    if not public_url:
        print("[!] 配置文件未设置 cloudflared 的 public_url")
        if cloudflared_proc:
            try:
                cloudflared_proc.kill()
            except Exception:
                pass
        return

    callback_url = public_url.rstrip("/") + CALLBACK_PATH
    print(f"[✓] 最终 Callback URL: {callback_url}")

    # 检查 callback 地址有效性
    if not check_callback_url(callback_url):
        print("[!] Callback 地址不可用，请检查 cloudflared 隧道和本地服务是否正常。")
        if cloudflared_proc:
            try:
                cloudflared_proc.kill()
            except Exception:
                pass
        return

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
            try:
                cloudflared_proc.kill()
                print("[*] cloudflared 已关闭")
            except Exception as e:
                print(f"[!] cloudflared 关闭时异常: {e}")

if __name__ == "__main__":
    main()
