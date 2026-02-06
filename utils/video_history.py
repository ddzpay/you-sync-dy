import os
import json
from threading import Lock

class VideoHistory:
    def __init__(self, history_file=None):
        if history_file is None:
            history_file = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'video_history.json'))
        self.history_file = history_file
        self.lock = Lock()
        self._data = self._load()

    def _load(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def is_processed(self, platform, video_id):
        with self.lock:
            vids = self._data.get(platform, [])
            return video_id in vids

    def mark_processed(self, platform, video_id):
        with self.lock:
            if platform not in self._data:
                self._data[platform] = []
            if video_id not in self._data[platform]:
                self._data[platform].append(video_id)
                self._save()