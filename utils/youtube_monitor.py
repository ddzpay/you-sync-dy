import os
import logging
import re
import aiohttp
import configparser
from datetime import datetime, timezone  

class YoutubeMonitor:
    def __init__(self):
        self.history_file = os.path.abspath(os.path.join(os.path.dirname(__file__), 'history.json'))
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        self.checked_videos = self.load_history()

        # 加载 config.ini 获取 API key 和可选代理设置
        config_path = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.ini'))
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        if "global" in config:
            self.api_key = config.get("global", "youtube_api_key", fallback="")
            self.proxy = config.get("global", "proxy", fallback=None)
            if not self.api_key:
                logging.error("[!] config.ini 中缺少 youtube_api_key")
        else:
            logging.error("[!] config.ini 中缺少 [global] 部分")
            self.api_key = ""
            self.proxy = None

    def load_history(self):
        import json
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_history(self):
        import json
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.checked_videos, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"[!] 保存 history.json 时失败: {e}")

    def record_video(self, channel_id, video_id):
        self.checked_videos[channel_id] = video_id
        self.save_history()

    def get_channel_by_video_id(self, video_id):
        for cid, vid in self.checked_videos.items():
            if vid == video_id:
                return cid
        return None

    def parse_iso_duration(self, iso_str):
        pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
        match = pattern.match(iso_str)
        if not match:
            logging.warning(f"[!] 无法解析视频时长: {iso_str}")
            return None
        h, m, s = map(lambda x: int(x) if x else 0, match.groups())
        return h * 3600 + m * 60 + s

    async def fetch_video_details(self, video_id):
        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?key={self.api_key}&id={video_id}&part=snippet,contentDetails"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get("items", [])
                        if items:
                            item = items[0]
                            snippet = item["snippet"]
                            content = item["contentDetails"]

                            duration = self.parse_iso_duration(content["duration"])
                            publish_time = snippet["publishedAt"]

                            return {
                                "video_id": video_id,
                                "channel_id": snippet.get("channelId"),
                                "published_at": publish_time,
                                "duration": duration
                            }
                    else:
                        logging.error(f"[!] 请求失败: 状态码 {response.status}")
        except Exception as e:
            logging.error(f"[!] 获取视频信息失败: {e}")
        return None
