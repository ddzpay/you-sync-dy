import os
import asyncio
import logging
import atexit
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
from asyncio import Lock
import json

from utils.browser_manager import BrowserManager
from utils.youtube_monitor import YoutubeMonitor
from utils.video_downloader import AsyncVideoDownloader
from utils.config_loader import get_time_gap, _set_main_thread_loop, config_reloader

# 导入各平台上传脚本
from utils.douyin_uploader import init_globals as douyin_init, get_queue as get_douyin_queue, worker as douyin_worker

MAX_DOWNLOAD_QUEUE_SIZE = 4
MAX_CONCURRENT_DOWNLOADS = 2

video_id_queue = None
download_semaphore = None

LAST_TIME_FILE = os.path.join(os.path.dirname(__file__), "config", "last_processed_time.json")
last_processed_time_per_channel = {}
channel_time_lock = Lock()

youtube_monitor = YoutubeMonitor()
log_handler = print

browser_manager = None

#A端单个频道限流逻辑
#当参数设置为-1时代表该频道不处理

CUSTOM_CHANNEL_IDS = {
    "UCzSFLbvTKdcfmCo7saWZujQ": 720, 
    "UCIbPhiNMXmko9CTUaWX8gqQ": 720,
    "UCleXpK9Sb2MCSZeNR4CTsMQ": 720,
    "UCwavDe8g8Mfdk0o8QVJKaog": 720,
    "UCjWCVEhAS4LCECSMDNMnzlw": -1,
    "UCh9xEOEmXC_FuGUarv_2HUw": 720,
    "UCUc0c5R90Evk4zNqxO6GzHA": -1, 
    "UCZn8dbFxfy_iOWnWEHyFfdw": 720,
    "UCurCrjSzGWfL2MMxkzVKAIw": -1, #抄袭4oA博主
    "UCqaBbXWyJ3-kHBb2PdkUJRw": -1,
    "UCwUq57PDCpsvwN5DYRig2-w": 720,
    "UCJtVPEhP9ovaD0OkVi66B2A": 720,
    "UCnWRXcywrripPvT9SGbztjg": -1,
    "UCM7d5JKl2mPhZdwrpVG0hnQ": -1,
    "UCCf51KVCmk-AGJY-XrCEkjw": -1,
    "UCqcwDHhFk17OEHuvf16kY4A": 720, 
    "UCO9RUgHoQ-bUpfFQopCFrxw": 720,
    "UCSr575W5pK9NmHiZ69WFp4A": -1,
    "UCVWG-brm2sO4CYuNlQg_4oA": -1,
    "UCiNvbjFfN4lQTNJm6P-hfzA": 720,
    "UC-maRiqJ9Y3mBZfBk-xnb8A": 720,
    "UCqGRYxVOmDGCZPjMD-UBGlw": 720,
}

#不推送C端的频道
#NO_PUSH_C_IDS = set()    #空集合

#不推送C端的频道ID
NO_PUSH_C_IDS = {
    "UC-maRiqJ9Y3mBZfBk-xnb8A",
    "UCqGRYxVOmDGCZPjMD-UBGlw",
}

C_ENDPOINT = "https://keai.frps.miaoshark.com/youtube/callback"

def load_last_processed_time():
    global last_processed_time_per_channel
    try:
        if os.path.exists(LAST_TIME_FILE):
            with open(LAST_TIME_FILE, "r", encoding="utf-8") as f:
                last_processed_time_per_channel = {}
                for line in f:
                    line = line.strip()
                    if line:
                        record = json.loads(line)
                        channel_id = record["channel_id"]
                        dt_str = record["last_time"]
                        last_processed_time_per_channel[channel_id] = datetime.fromisoformat(dt_str)
        else:
            last_processed_time_per_channel = {}
    except Exception as e:
        logging.error(f"加载 last_processed_time.json 失败: {e}")
        last_processed_time_per_channel = {}

def save_last_processed_time():
    try:
        os.makedirs(os.path.dirname(LAST_TIME_FILE), exist_ok=True)
        with open(LAST_TIME_FILE, "w", encoding="utf-8") as f:
            for channel_id, dt in last_processed_time_per_channel.items():
                record = {
                    "channel_id": channel_id,
                    "last_time": dt.isoformat()
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"保存 last_processed_time.json 失败: {e}")

async def async_save_last_processed_time():
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, save_last_processed_time)
    except Exception as e:
        logging.error(f"异步保存 last_processed_time.json 失败: {e}")

def cleanup_on_exit():
    try:
        save_last_processed_time()
        logging.info("程序退出，已保存最后的时间记录")
    except Exception as e:
        logging.error(f"程序退出清理失败: {e}")

atexit.register(cleanup_on_exit)
load_last_processed_time()

async def init_async_globals():
    global download_semaphore, video_id_queue
    douyin_init()
    if download_semaphore is None:
        download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    if video_id_queue is None:
        video_id_queue = asyncio.Queue(maxsize=MAX_DOWNLOAD_QUEUE_SIZE)

@asynccontextmanager
async def lifespan(app):
    global browser_manager
    _set_main_thread_loop()
    config_reloader.start_watching()
    await init_async_globals()
    browser_manager = BrowserManager(log_handler=log_handler)
    await browser_manager.start()

    # 启动各平台 worker
    worker_tasks = [
        asyncio.create_task(douyin_worker(browser_manager.uploader_douyin, log_handler), name=f"douyin_worker_{i}")
        for i in range(2)
    ]
    main_task = asyncio.create_task(async_handler_task(), name="main_handler")
    all_tasks = [main_task] + worker_tasks

    log_handler("[✓] 系统初始化完成")
    yield

    log_handler("[✓] 开始优雅关闭后台任务...")
    for t in all_tasks:
        if not t.done():
            t.cancel()
    for t in all_tasks:
        try:
            await t
        except asyncio.CancelledError:
            log_handler(f"[✓] 任务 {t.get_name()} 已取消")
        except Exception as e:
            logging.error(f"关闭任务 {t.get_name()} 时出错: {e}")

    try:
        await browser_manager.stop()
        log_handler("[✓] 浏览器及Playwright已关闭")
    except Exception as e:
        logging.error(f"关闭BrowserManager异常: {e}")

    log_handler("[✓] 所有后台资源已释放，服务已安全退出。")

app = FastAPI(lifespan=lifespan)

def set_uploader_log_handler(handler):
    global log_handler
    log_handler = handler
    # 让 browser_manager 的 uploader 也同步日志（如果已初始化）
    if browser_manager and getattr(browser_manager, "uploader_douyin", None):
        browser_manager.uploader_douyin.log_handler = handler

def extract_id_from_url(platform, url):
    import re
    if platform == "youtube":
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        return m.group(1) if m else url
    if platform == "tiktok":
        m = re.search(r"/video/(\d+)", url)
        return m.group(1) if m else url
    if platform == "ins":
        m = re.search(r"/(reel|p)/([a-zA-Z0-9_-]+)", url)
        return m.group(2) if m else url
    return url

async def get_video_task_async():
    return await video_id_queue.get()

async def forward_xml_to_c_async(video_id, channel_id):
    import aiohttp
    xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>{video_id}</yt:videoId>
    <yt:channelId>{channel_id}</yt:channelId>
  </entry>
</feed>"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                C_ENDPOINT,
                data=xml_template.encode("utf-8"),
                headers={"Content-Type": "application/atom+xml"},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                logging.info(f"XML推送到C端（嘟嘟总裁），返回: {resp.status}")
    except Exception as e:
        logging.error(f"推送XML到C端（嘟嘟总裁）失败: {e}")

@app.get("/healthz")
async def health_check():
    return PlainTextResponse("OK", status_code=200)

@app.api_route('/youtube/callback', methods=['GET', 'POST'])
async def youtube_callback(request: Request):
    global last_processed_time_per_channel
    import xml.etree.ElementTree as ET
    if request.method == 'GET':
        params = dict(request.query_params)
        challenge = params.get("hub.challenge", "")
        if challenge:
            logging.info(f"收到 YouTube 订阅验证 GET，challenge={challenge}")
            return PlainTextResponse(challenge, status_code=200)
        else:
            logging.warning("收到 YouTube 订阅验证 GET，但没有 challenge 参数")
            return PlainTextResponse("Missing challenge", status_code=400)

    elif request.method == 'POST':
        now = datetime.now()
        content_type = request.headers.get("content-type", "")
        try:
            if content_type.startswith("application/json"):
                data = await request.json()
                platform = data.get("platform", "youtube")
                video_url = data.get("url")
                video_id = data.get("video_id")
                channel_id = data.get("channel_id", platform)
                local_path = data.get("local_path")  # 新增，没这个字段就是 None
                if video_url or (platform.startswith("douyin") and local_path):  # 支持混剪场景
                    logging.info(f"[✓] 收到新{platform}手动提交视频: {video_url or local_path}")
                    try:
                        await video_id_queue.put({
                            "platform": platform,
                            "video_url": video_url,
                            "video_id": video_id,
                            "channel_id": channel_id,
                            "manual": True,
                            "path": local_path
                        })
                    except asyncio.QueueFull:
                        logging.warning(f"[!] 下载队列已满（容量: {MAX_DOWNLOAD_QUEUE_SIZE}），丢弃本次推送: {video_url or local_path}")

            else:
                xml_data = (await request.body()).decode("utf-8")
                try:
                    root = ET.fromstring(xml_data)
                except ET.ParseError as e:
                    logging.error(f"XML解析失败: {e}")
                    return PlainTextResponse("Invalid XML", status_code=400)
                ns = {
                    'atom': 'http://www.w3.org/2005/Atom',
                    'yt': 'http://www.youtube.com/xml/schemas/2015'
                }
                entry = root.find("atom:entry", ns)
                if entry is not None:
                    video_id_elem = entry.find("yt:videoId", ns)
                    channel_id_elem = entry.find("yt:channelId", ns)
                    if video_id_elem is not None and video_id_elem.text:
                        video_id = video_id_elem.text
                        channel_id = channel_id_elem.text if channel_id_elem is not None else "youtube"
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        
                        #白名单单个频道ID限流逻辑
                        is_disabled_channel = False
                        should_process = True

                        async with channel_time_lock:
                            custom_time_gap_min = CUSTOM_CHANNEL_IDS.get(channel_id)
                            if custom_time_gap_min is not None:
                                if custom_time_gap_min == -1:   # 当设置为-1时代表该频道不处理
                                    should_process = False
                                    is_disabled_channel = True
                                    logging.info(
                                        f"[!] 频道 {channel_id} 已设置为永久禁用，本次新视频{video_id}不处理，推送给C端（嘟嘟总裁）"
                                    )
                                elif custom_time_gap_min == 0:
                                    last_processed_time_per_channel[channel_id] = now
                                    should_process = True
                                else:
                                    current_time_gap = timedelta(minutes=custom_time_gap_min)
                                    last_time = last_processed_time_per_channel.get(channel_id)
                                    if last_time is None or now - last_time >= current_time_gap:
                                        last_processed_time_per_channel[channel_id] = now
                                        should_process = True
                                    else:
                                        should_process = False
                            else:
                                current_time_gap = await get_time_gap()
                                last_time = last_processed_time_per_channel.get(channel_id)
                                if last_time is None or now - last_time >= current_time_gap:
                                    last_processed_time_per_channel[channel_id] = now
                                    should_process = True
                                else:
                                    should_process = False

                        if not should_process:
                            if not is_disabled_channel:
                                logging.info(
                                    f"[!] 频道 {channel_id} {current_time_gap.total_seconds()//60:.0f}分钟内已推送过其它视频，本次新视频{video_id}不处理，推送给C端（嘟嘟总裁）"
                                )
                            
                            if channel_id in NO_PUSH_C_IDS:
                                logging.info(f"[!] 频道 {channel_id} 已设置不推送C端（嘟嘟总裁），本次新视频{video_id}已丢弃")
                                return PlainTextResponse("Channel limited, not pushed", status_code=200)

                            asyncio.create_task(forward_xml_to_c_async(video_id, channel_id))
                            return PlainTextResponse("Channel limited, pushed to C", status_code=200)

                        asyncio.create_task(async_save_last_processed_time())

                        logging.info(f"[✓] 收到YouTube订阅视频通知: {video_id}")
                        try:
                            await video_id_queue.put({
                                "platform": "youtube",
                                "video_url": video_url,
                                "video_id": video_id,
                                "channel_id": channel_id
                            })
                        except asyncio.QueueFull:
                            logging.warning(f"[!] 下载队列已满（容量: {MAX_DOWNLOAD_QUEUE_SIZE}），丢弃本次推送: {video_url}")

        except Exception as e:
            logging.error(f"解析 POST 回调出错: {e}")
            logging.exception("详细错误信息")

        return PlainTextResponse("OK", status_code=200)

async def async_handler_task():
    log_handler("[✓] 正在监控YouTube视频推送... ")
    while True:
        try:
            task = await get_video_task_async()
            asyncio.create_task(handle_video(task))
        except Exception as e:
            log_handler(f"[!] 异步处理任务异常: {e}")
            logging.exception("异步处理任务异常")
            await asyncio.sleep(5)

async def handle_video(task):
    async with download_semaphore:
        platform = task.get("platform", "youtube")
        video_url = task.get("video_url")
        video_id = task.get("video_id") or extract_id_from_url(platform, video_url)
        channel_id = task.get("channel_id", platform)
        manual = task.get("manual", False)
        path = task.get("path")

        # ---- 优先处理本地混剪/人工任务（如 main.py 混剪上传、path 不为空） ----
        if platform in ("douyin", "douyinmix") and manual and path:
            try:
                await get_douyin_queue().put({
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "path": path,
                    "platform": "douyin"
                })
                log_handler(f"[✓] 混剪视频已直接入队抖音上传...")
            except asyncio.QueueFull:
                log_handler(f"[!] 抖音上传队列已满，丢弃本次任务: {path}")
            return

        # ---- 普通YouTube自动推送视频逻辑 ----
        if platform == "youtube" and not manual:
            checked_videos = youtube_monitor.checked_videos
            if video_id in checked_videos.values():
                log_handler(f"[-] 视频 {video_id} 已处理过，跳过。")
                return
            try:
                info = await youtube_monitor.fetch_video_details(video_id)
                if not info:
                    log_handler(f"[!] 获取视频信息失败: {video_id}")
                    return
                if not youtube_monitor.is_recent(info['published_at'], minutes=2):
                    log_handler(
                        f"[-] 跳过：该作品发布时间已超过2分钟，发布于（北京时间）："
                        f"{(datetime.strptime(info['published_at'], '%Y-%m-%dT%H:%M:%SZ') + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    return
                if info['duration'] is None or info['duration'] > 120:
                    log_handler(f"[-] 跳过：非 Shorts 视频（时长 {info['duration']} 秒）")
                    return
            except Exception as e:
                log_handler(f"[!] 获取YouTube视频详情失败: {e}")
                return

        # ---- 需要下载的视频（如普通YouTube/TikTok/Instagram推送） ----
        try:
            downloader = AsyncVideoDownloader()
            downloaded_path = await downloader.download_video(channel_id, video_url, video_id)
        except Exception as e:
            logging.info(f"[!] 调用 video_downloader.py 失败: {e}")
            downloaded_path = None

        if downloaded_path:
            # 抖音分发（不再判断频道是否在白名单里）
            try:
                await get_douyin_queue().put({
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "path": downloaded_path,
                    "platform": "douyin"
                })
            except asyncio.QueueFull:
                log_handler(f"[!] 抖音上传队列已满，丢弃本次任务: {downloaded_path}")
                try:
                    os.remove(downloaded_path)
                    log_handler(f"[x] 抖音上传队列溢出，已删除未入队本地文件: {downloaded_path}")
                except Exception as e:
                    log_handler(f"[!] 删除本地文件失败: {e}")
        else:
            log_handler(f"[!] 视频下载失败: {video_url}")

__all__ = [
    'app',
    'video_id_queue',
    'init_async_globals',
    'async_handler_task',
    'get_video_task_async',
    'handle_video',
    'log_handler'
]