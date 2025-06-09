import os
import logging
import subprocess
import time
import sys

class VideoDownloader:
    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = os.path.join(script_dir, 'downloads')
        os.makedirs(self.base_dir, exist_ok=True)

        project_root = os.path.dirname(script_dir)
        self.bin_path = os.path.join(project_root, 'tools')
        exe_suffix = ".exe" if sys.platform.startswith("win") else ""
        self.ffmpeg_path = os.path.join(self.bin_path, f"ffmpeg{exe_suffix}")
        self.ytdlp_path = os.path.join(self.bin_path, f"yt-dlp{exe_suffix}")

    def is_ffmpeg_available(self):
        return os.path.isfile(self.ffmpeg_path)

    def is_ytdlp_available(self):
        return os.path.isfile(self.ytdlp_path)

    def download_video(self, channel_id, video_url, video_id, max_retry=2, retry_delay=2):
        if not self.is_ffmpeg_available():
            logging.error("[!] 缺少 ffmpeg，检查可在 'tools' 目录中")
            return None

        if not self.is_ytdlp_available():
            logging.error("[!] 缺少 yt-dlp，检查可在 'tools' 目录中")
            return None

        channel_dir = os.path.join(self.base_dir, channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        output_path_template = os.path.join(channel_dir, f"{video_id}.%(ext)s")

        cmd = [
            self.ytdlp_path,
            "-S", "res:1920,fps,ext:mp4",
            "--no-playlist",
            "-o", output_path_template,
            video_url
        ]

        env = os.environ.copy()
        env["PATH"] = self.bin_path + os.pathsep + env.get("PATH", "")

        attempt = 0
        while attempt < max_retry:
            try:
                logging.info(f"[↓] 正在下载: {video_url} (尝试 {attempt+1}/{max_retry})")
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if result.returncode == 0:
                    final_path = os.path.join(channel_dir, f"{video_id}.mp4")
                    if os.path.exists(final_path):
                        logging.info(f"[✓] 已下载至: {final_path}")
                        return final_path
                    else:
                        for ext in ["mp4", "mkv", "webm"]:
                            path = os.path.join(channel_dir, f"{video_id}.{ext}")
                            if os.path.exists(path):
                                logging.info(f"[✓] 已下载至: {path}")
                                return path
                        logging.error(f"[!] 下载完成但未找到文件: {final_path}")
                else:
                    logging.error(f"[!] yt-dlp 执行失败 (尝试 {attempt+1}/{max_retry}):\n{result.stderr}")
            except Exception as e:
                logging.error(f"[!] 执行 yt-dlp 出错 (尝试 {attempt+1}/{max_retry}): {e}")
            attempt += 1
            if attempt < max_retry:
                time.sleep(retry_delay)
                logging.info(f"[!] 下载失败，{retry_delay} 秒后重试...")
        logging.error(f"[!] 视频下载最终失败: {video_url}")
        return None