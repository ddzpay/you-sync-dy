import os
import queue
import threading
import asyncio
import logging
from flask import Flask, request, Response
import xml.etree.ElementTree as ET
from datetime import datetime

from utils.youtube_monitor import YoutubeMonitor
from utils.video_downloader import VideoDownloader
from utils.douyin_uploader import DouyinUploader
from utils.video_history import VideoHistory  # 新增

app = Flask(__name__)

video_id_queue = queue.Queue()

MAX_CONCURRENT_UPLOADS = 3
UPLOAD_QUEUE_MAXSIZE = 5
upload_semaphore = None
upload_queue = None

video_history = VideoHistory()  # 实例化全局唯一历史记录对象

def init_async_globals():
    global upload_semaphore, upload_queue
    if upload_semaphore is None:
        upload_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
    if upload_queue is None:
        upload_queue = asyncio.Queue(maxsize=UPLOAD_QUEUE_MAXSIZE)

async def get_video_task_async():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, video_id_queue.get)

@app.route("/healthz", methods=["GET"])
def health_check():
    return "OK", 200

@app.route('/youtube/callback', methods=['GET', 'POST'])
def youtube_callback():
    if request.method == 'GET':
        challenge = request.args.get("hub.challenge", "")
        if challenge:
            logging.info(f"收到 YouTube 订阅验证 GET，challenge={challenge}")
            return Response(challenge, status=200)
        else:
            logging.warning("收到 YouTube 订阅验证 GET，但没有 challenge 参数")
            return Response("Missing challenge", status=400)
    elif request.method == 'POST':
        content_type = request.headers.get("Content-Type", "")
        try:
            # 新接口支持 JSON 提交
            if content_type.startswith("application/json"):
                data = request.get_json(force=True)
                platform = data.get("platform", "youtube")
                video_url = data.get("url")
                video_id = data.get("video_id")  # 如果有就直接用，没有可后面提取
                # 兼容 YouTube 老格式
                if not video_url and video_id and platform == "youtube":
                    video_url = f"https://www.youtube.com/watch?v={video_id}"
                if video_url:
                    logging.info(f"[✓] 收到新{platform}视频通知: {video_url}")
                    video_id_queue.put({
                        "platform": platform,
                        "video_url": video_url,
                        "video_id": video_id,
                    })
            else:
                # 兼容原有 YouTube XML
                xml_data = request.data.decode("utf-8")
                root = ET.fromstring(xml_data)
                ns = {
                    'atom': 'http://www.w3.org/2005/Atom',
                    'yt': 'http://www.youtube.com/xml/schemas/2015'
                }
                entry = root.find("atom:entry", ns)
                if entry is not None:
                    video_id_elem = entry.find("yt:videoId", ns)
                    if video_id_elem is not None and video_id_elem.text:
                        video_id = video_id_elem.text
                        logging.info(f"[✓] 收到新YouTube视频通知: {video_id}")
                        video_id_queue.put({
                            "platform": "youtube",
                            "video_url": f"https://www.youtube.com/watch?v={video_id}",
                            "video_id": video_id
                        })
        except Exception as e:
            logging.error(f"解析 POST 回调出错: {e}")
        return Response("OK", status=200)

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

async def handle_video(task):
    platform = task.get("platform", "youtube")
    video_url = task.get("video_url")
    video_id = task.get("video_id") or extract_id_from_url(platform, video_url)
    channel_id = platform

    # 全平台去重（上传成功前不处理）
    if video_history.is_processed(platform, video_id):
        log_handler(f"[-] {platform} 视频 {video_id} 已处理过，跳过。")
        return

    # YouTube 特有的发布时间和时长判断
    if platform == "youtube":
        monitor = YoutubeMonitor()
        checked_videos = monitor.checked_videos
        if video_id in checked_videos.values():
            log_handler(f"[-] 视频 {video_id} 已处理过，跳过。")
            return
        info = await monitor.fetch_video_details(video_id)
        if not info:
            log_handler(f"[!] 获取视频信息失败: {video_id}")
            return
        if not monitor.is_recent(info['published_at'], minutes=2):
            log_handler(f"[-] 跳过：发布时间已超过2分钟，发布时间：{info['published_at']}")
            return
        if info['duration'] is None or info['duration'] > 70:
            log_handler(f"[-] 跳过：非 Shorts 视频（时长 {info['duration']} 秒）")
            return

    # 统一下载处理
    downloader = VideoDownloader()
    loop = asyncio.get_running_loop()
    downloaded_path = await loop.run_in_executor(
        None, downloader.download_video, channel_id, video_url, video_id
    )

    if downloaded_path:
        try:
            await upload_queue.put({
                "video_id": video_id,
                "channel_id": channel_id,
                "path": downloaded_path,
                "platform": platform
            })
        except asyncio.QueueFull:
            log_handler(f"[!] 上传队列已满（容量: {UPLOAD_QUEUE_MAXSIZE}），丢弃本次任务: {downloaded_path}")
            try:
                os.remove(downloaded_path)
                log_handler(f"[x] 上传队列溢出，已删除未入队本地文件: {downloaded_path}")
            except Exception as e:
                log_handler(f"[!] 删除本地文件失败: {e}")
    else:
        log_handler(f"[!] 视频下载失败: {video_url}")

uploader = DouyinUploader()
log_handler = print

def set_uploader_log_handler(handler):
    global log_handler
    log_handler = handler
    uploader.log_handler = handler

async def upload_worker():
    while True:
        try:
            task = await upload_queue.get()
            async with upload_semaphore:
                await process_upload_task(task)
            upload_queue.task_done()
        except Exception as e:
            log_handler(f"[!] upload_worker异常: {e}")
            logging.exception("upload_worker异常")

async def process_upload_task(task):
    video_id = task['video_id']
    channel_id = task['channel_id']
    path = task['path']
    platform = task.get('platform', 'youtube')
    monitor = YoutubeMonitor()
    success = await uploader.upload_video(path)
    if success:
        if platform == "youtube":
            monitor.record_video(channel_id, video_id)
        # 全平台: 上传成功后记录已处理
        video_history.mark_processed(platform, video_id)
        try:
            os.remove(path)
            log_handler(f"[✓] 上传成功，已删除本地文件: {path}")
        except Exception as e:
            log_handler(f"[!] 删除失败: {e}")
    else:
        log_handler(f"[!] 上传失败，保留文件: {path}")

def start_upload_workers():
    for _ in range(MAX_CONCURRENT_UPLOADS):
        asyncio.create_task(upload_worker())

def start_async_handler():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_handler())

async def async_handler():
    init_async_globals()
    await uploader.start_browser()
    await uploader.ensure_logged_in()
    start_upload_workers()
    log_handler("[✓] 正在监控多平台视频推送... ")
    while True:
        task = await get_video_task_async()
        await handle_video(task)

# 供主程序导入队列用
__all__ = ['app', 'start_async_handler', 'set_uploader_log_handler', 'video_id_queue']