import os
import logging
import subprocess
import time

class VideoDownloader:
    def __init__(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = os.path.join(script_dir, 'downloads')
        os.makedirs(self.base_dir, exist_ok=True)

        # 设置日志
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def is_ffmpeg_available(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ffmpeg_path = os.path.join(project_root, "ffmpeg", "bin", "ffmpeg.exe")
        return os.path.isfile(ffmpeg_path)

    def is_ytdlp_available(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ytdlp_path = os.path.join(project_root, "ffmpeg", "bin", "yt-dlp.exe")
        return os.path.isfile(ytdlp_path)

    def set_env_path(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bin_path = os.path.join(project_root, "ffmpeg", "bin")
        os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")

    def run_with_live_output(self, cmd):
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output_lines = []
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            print(line)  # 实时输出到控制台
            logging.info(line)  # 同时写入日志
            output_lines.append(line)
        process.stdout.close()
        process.wait()
        return process.returncode, "\n".join(output_lines)

    def download_video(self, channel_id, video_url, video_id, max_retry=3, retry_delay=3):
        if not self.is_ffmpeg_available():
            logging.error("[!] 缺少 ffmpeg.exe，请将其放在 'ffmpeg/bin/' 目录中")
            return None

        if not self.is_ytdlp_available():
            logging.error("[!] 缺少 yt-dlp.exe，请将其放在 'ffmpeg/bin/' 目录中")
            return None

        self.set_env_path()

        channel_dir = os.path.join(self.base_dir, channel_id)
        os.makedirs(channel_dir, exist_ok=True)
        output_path_template = os.path.join(channel_dir, f"{video_id}.%(ext)s")

        cmd = [
            "yt-dlp.exe",
            "-f", "bv*+ba/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", output_path_template,
            video_url
        ]

        attempt = 0
        while attempt < max_retry:
            try:
                logging.info(f"[↓] 正在下载: {video_url} (尝试 {attempt+1}/{max_retry})")
                returncode, output = self.run_with_live_output(cmd)
                if returncode == 0:
                    final_path = os.path.join(channel_dir, f"{video_id}.mp4")
                    if os.path.exists(final_path):
                        logging.info(f"[✓] 下载完成: {final_path}")
                        return final_path
                    else:
                        logging.error(f"[!] 下载完成但未找到文件: {final_path}")
                else:
                    logging.error(f"[!] yt-dlp 执行失败 (尝试 {attempt+1}/{max_retry}):\n{output}")
            except Exception as e:
                logging.error(f"[!] 执行 yt-dlp 出错 (尝试 {attempt+1}/{max_retry}): {e}")
            attempt += 1
            if attempt < max_retry:
                time.sleep(retry_delay)
                logging.info(f"[!] 下载失败，{retry_delay} 秒后重试...")

        logging.error(f"[!] 视频下载最终失败: {video_url}")
        return None
