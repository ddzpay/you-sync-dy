import os
import queue
import threading
import asyncio
from flask import Flask, request, Response
import xml.etree.ElementTree as ET

from utils.youtube_monitor import YoutubeMonitor
from utils.video_downloader import VideoDownloader
from utils.douyin_uploader import DouyinUploader

app = Flask(__name__)

# ====== 全局同步队列 ======
video_id_queue = queue.Queue()

# ====== 并发控制参数 ======
MAX_CONCURRENT_UPLOADS = 3
UPLOAD_QUEUE_MAXSIZE = 5  # 上传队列最大长度（可根据实际情况调整）
upload_semaphore = None
upload_queue = None

def init_async_globals():
    global upload_semaphore, upload_queue
    if upload_semaphore is None:
        upload_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
    if upload_queue is None:
        upload_queue = asyncio.Queue(maxsize=UPLOAD_QUEUE_MAXSIZE)

# ====== 适配器：同步队列转异步 await ======
async def get_video_id_async():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, video_id_queue.get)

# ====== 频道视频推送接收入口 ======
@app.route('/youtube/callback', methods=['GET', 'POST'])
def youtube_callback():
    if request.method == 'GET':
        return Response(request.args.get("hub.challenge", ""), status=200)
    elif request.method == 'POST':
        xml_data = request.data.decode("utf-8")
        root = ET.fromstring(xml_data)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entry = root.find("atom:entry", ns)
        if entry is not None:
            video_id = entry.find("atom:videoId", ns).text
            print(f"[✓] 收到新视频通知: {video_id}")
            video_id_queue.put(video_id)  # 放入线程安全队列
        return Response("OK", status=200)

# ====== 分析并下载视频后，加入上传队列 ======
async def handle_video(video_id):
    monitor = YoutubeMonitor()

    # ----------- 去重检查 -----------
    checked_videos = monitor.checked_videos
    if video_id in checked_videos.values():
        log_handler(f"[-] 视频 {video_id} 已处理过，跳过。")
        return
    # ----------- 去重检查结束 ---------------

    downloader = VideoDownloader()

    info = await monitor.fetch_video_details(video_id)
    if not info:
        log_handler(f"[!] 获取视频信息失败: {video_id}")
        return

    if not monitor.is_recent(info['published_at']):
        log_handler(f"[-] 跳过：发布时间超过2分钟：{info['published_at']}")
        return

    if info['duration'] is None or info['duration'] > 60:
        log_handler(f"[-] 跳过：非 Shorts 视频（时长 {info['duration']} 秒）")
        return

    channel_id = info['channel_id']
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    downloaded_path = downloader.download_video(channel_id, video_url, video_id)
    if downloaded_path:
        log_handler(f"[✓] 视频已下载: {downloaded_path}")
        try:
            await upload_queue.put_nowait({
                "video_id": video_id,
                "channel_id": channel_id,
                "path": downloaded_path
            })
        except asyncio.QueueFull:
            log_handler(f"[!] 上传队列已满（容量: {UPLOAD_QUEUE_MAXSIZE}），丢弃本次任务: {downloaded_path}")
            try:
                os.remove(downloaded_path)
                log_handler(f"[🗑] 上传队列溢出，已删除未入队本地文件: {downloaded_path}")
            except Exception as e:
                log_handler(f"[!] 删除本地文件失败: {e}")
    else:
        log_handler(f"[!] 视频下载失败: {video_url}")

# ====== 全局唯一 DouyinUploader 实例 ======
uploader = DouyinUploader()
log_handler = print  # 默认日志处理为print，主程序可set_uploader_log_handler覆盖

def set_uploader_log_handler(handler):
    global log_handler
    log_handler = handler
    uploader.log_handler = handler

# ====== 上传 Worker（并发控制）======
async def upload_worker():
    while True:
        task = await upload_queue.get()
        async with upload_semaphore:
            await process_upload_task(task)
        upload_queue.task_done()

async def process_upload_task(task):
    video_id = task['video_id']
    channel_id = task['channel_id']
    path = task['path']
    monitor = YoutubeMonitor()

    log_handler(f"[↑] 开始上传: {video_id}")
    success = await uploader.upload_video(path)
    if success:
        monitor.record_video(channel_id, video_id)
        try:
            os.remove(path)
            log_handler(f"[🗑] 上传成功，已删除本地文件: {path}")
        except Exception as e:
            log_handler(f"[!] 删除失败: {e}")
    else:
        log_handler(f"[!] 上传失败，保留文件: {path}")

# ====== 启动多个上传线程 ======
def start_upload_workers():
    for _ in range(MAX_CONCURRENT_UPLOADS):
        asyncio.create_task(upload_worker())

# ====== 主异步处理线程 ======
def start_async_handler():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_handler())

async def async_handler():
    init_async_globals()
    await uploader.start_browser()
    await uploader.login()
    start_upload_workers()
    log_handler("[*] 等待 Google 推送更新通知中... 按 Ctrl+C 退出")
    while True:
        video_id = await get_video_id_async()
        await handle_video(video_id)

# ====== 程序入口 ======
if __name__ == "__main__":
    threading.Thread(target=start_async_handler, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, debug=False)
