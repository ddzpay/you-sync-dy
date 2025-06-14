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
        self.aria2c_path = os.path.join(self.bin_path, f"aria2c{exe_suffix}")

    def is_ffmpeg_available(self):
        return os.path.isfile(self.ffmpeg_path)

    def is_ytdlp_available(self):
        return os.path.isfile(self.ytdlp_path)

    def is_aria2c_available(self):
        return os.path.isfile(self.aria2c_path)

    def _get_platform_cmd(self, video_url, output_path_template):
        url = video_url.lower()
        cmd = [
            self.ytdlp_path,
            "--no-playlist",
            "-o", output_path_template,
            "--ffmpeg-location", self.ffmpeg_path,
            "--merge-output-format", "mp4",
            "--concurrent-fragments", "8",
        ]

        # 如 aria2c 存在则启用更强加速
        if self.is_aria2c_available():
            cmd += [
                "--external-downloader", self.aria2c_path,
                "--external-downloader-args", "-x 16 -k 1M"
            ]

        # TikTok/Instagram走特殊格式
        if "tiktok.com" in url or "instagram.com" in url:
            cmd += ["-f", "best"]
        else:
            cmd += [
                "-S", "res,codec,fps,ext",
                "-f", "bestvideo+bestaudio"
            ]
        cmd.append(video_url)
        return cmd

    def download_video(self, channel_id, video_url, video_id, max_retry=2, retry_delay=2):
        if not self.is_ffmpeg_available():
            logging.error("[!] 缺少 ffmpeg，检查可在 'tools' 目录中")
            return None

        if not self.is_ytdlp_available():
            logging.error("[!] 缺少 yt-dlp，检查可在 'tools' 目录中")
            return None

        if not self.is_aria2c_available():
            logging.warning("[!] 未检测到 aria2c，将使用 yt-dlp 内置下载，速度可能较慢")

        channel_dir = os.path.join(self.base_dir, channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        output_path_template = os.path.join(channel_dir, f"{video_id}.%(ext)s")

        cmd = self._get_platform_cmd(video_url, output_path_template)

        env = os.environ.copy()
        env["PATH"] = self.bin_path + os.pathsep + env.get("PATH", "")

        attempt = 0
        while attempt < max_retry:
            try:
                logging.info(f"[↓] 正在下载: {video_url} (尝试 {attempt+1}/{max_retry})")
                logging.debug(f"[命令] {' '.join([str(c) for c in cmd])}")
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if result.returncode == 0:
                    # 检查各种后缀
                    for ext in ["mp4", "mkv", "webm"]:
                        path = os.path.join(channel_dir, f"{video_id}.{ext}")
                        if os.path.exists(path):
                            logging.info(f"[✓] 已下载至: {path}")
                            return path
                    logging.error(f"[!] 下载完成但未找到文件: {output_path_template}")
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

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="多平台视频下载工具（支持aria2c加速）")
    parser.add_argument("channel", help="频道ID或自定义目录")
    parser.add_argument("url", help="视频URL")
    parser.add_argument("vid", help="视频ID（文件名前缀）")
    args = parser.parse_args()

    downloader = VideoDownloader()
    downloader.download_video(args.channel, args.url, args.vid)