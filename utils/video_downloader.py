import os
import logging
import sys
import argparse
import asyncio
from functools import partial
import yt_dlp
from utils.notifier import notify_wecom_group

WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=9283fa7c-0e99-4c89-85e2-2908c7285804"

class AsyncVideoDownloader:
    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(script_dir)
        self.base_dir = os.path.join(self.project_root, 'downloads')
        os.makedirs(self.base_dir, exist_ok=True)
        self.cache_dir = os.path.join(self.project_root, '.cache')
        os.makedirs(self.cache_dir, exist_ok=True)

        self.cookies_path = r"C:\Users\Administrator\Desktop\xiaobaojiang-YT2DouKuai\cookies\youtube_cookies.txt"
        self.bin_path = os.path.join(self.project_root, 'tools')
        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        self.ffmpeg_path = os.path.join(self.bin_path, f"ffmpeg{exe_suffix}")

    async def download_video(self, channel_id, video_url, video_id, max_retry=2, retry_delay=10):
        channel_dir = os.path.join(self.base_dir, channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        output_path_template = os.path.join(channel_dir, f"{video_id}.%(ext)s")

        url = video_url.lower()
        if "tiktok.com" in url or "instagram.com" in url:
            ydl_opts = {
                'format': 'best',
                'outtmpl': output_path_template,
                'noplaylist': True,
                'noprogress': True,
                'quiet': True,
                'no_warnings': True,
                'cookiesfrombrowser': ('firefox',)
            }
        else:
            ydl_opts = {
                'format': 'bestvideo[height<=1920][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1280][ext=mp4]+bestaudio',
                'outtmpl': output_path_template,
                'noplaylist': True,
                'merge_output_format': 'mp4',
                'noprogress': True,
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {'youtube': {'playback_wait': '0'}},
                'cache_dir': self.cache_dir,
                'cookies': self.cookies_path,
                'ffmpeg_location': self.ffmpeg_path,
                'jsruntimes': 'deno',
                'remote_components': 'ejs:github',
            }

        async def run_yt_dlp():
            # yt_dlp 不支持异步，只能用线程池
            loop = asyncio.get_event_loop()
            func = partial(self._download, video_url, ydl_opts)
            return await loop.run_in_executor(None, func)

        attempt = 0
        while attempt < max_retry:
            try:
                logging.info(f"[↓] 正在下载: {video_url} (尝试 {attempt+1}/{max_retry})")
                await run_yt_dlp()
                for ext in ["mp4", "mkv", "webm"]:
                    final_path = os.path.join(channel_dir, f"{video_id}.{ext}")
                    if os.path.exists(final_path):
                        logging.info(f"[✓] 已下载至: {final_path}")
                        return final_path
                logging.error("[!] 下载完成但未找到视频文件")
            except Exception as e:
                logging.error(f"[!] yt-dlp 下载出错 (尝试 {attempt+1}/{max_retry}): {e}")
            attempt += 1
            if attempt < max_retry:
                logging.info(f"[!] 下载失败，{retry_delay} 秒后重试...")
                await asyncio.sleep(retry_delay)
        logging.error(f"[!] 视频下载最终失败: {video_url}")
        notify_wecom_group(f"[!]小包浆Vlog视频下载失败，请尽快检查代理", WECOM_WEBHOOK)
        return None

    def _download(self, video_url, ydl_opts):
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="多平台视频下载工具（异步）")
    parser.add_argument("channel", help="频道ID或自定义目录")
    parser.add_argument("url", help="视频URL")
    parser.add_argument("vid", help="视频ID（文件名前缀）")
    args = parser.parse_args()

    downloader = AsyncVideoDownloader()
    await downloader.download_video(args.channel, args.url, args.vid)

if __name__ == "__main__":
    asyncio.run(main())