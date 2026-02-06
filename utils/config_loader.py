# utils/config_loader.py
"""
统一配置热加载模块
使用 watchdog 监听 config.ini 文件变化，实现 time_gap_minutes 热更新
"""
import os
import logging
import configparser
import asyncio
from datetime import timedelta
from typing import Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    raise ImportError("缺少依赖库: pip install watchdog")

# ------------------------
# 全局变量：在主线程中保存事件循环引用
# ------------------------
_main_thread_loop = None  # 在主线程中初始化时设置


def _set_main_thread_loop():
    """供主程序调用，设置主线程的事件循环"""
    global _main_thread_loop
    _main_thread_loop = asyncio.get_running_loop()
    logging.info(f"[√] config_loader 已绑定主线程事件循环: {_main_thread_loop}")


class ConfigReloader:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 上一级目录
        self.config_path = os.path.join(self.base_dir, "config", "config.ini")
        self.config_dir = os.path.dirname(self.config_path)
        self._time_gap_minutes = 60  # 默认值
        self._lock = asyncio.Lock()  # 异步安全锁
        self._observer: Optional[Observer] = None
        self._load_config()

    def _load_config(self):
        """同步加载配置（由 watchdog 调用）"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.config_path):
                config.read(self.config_path, encoding="utf-8")
                new_value = int(config.get("SETTINGS", "time_gap_minutes", fallback="60"))
            else:
                logging.warning(f"未找到 config.ini ({self.config_path})，使用默认值 60")
                new_value = 60
            old_value = self._time_gap_minutes
            self._time_gap_minutes = new_value
            logging.info(f"[√] 成功加载 time_gap_minutes = {new_value} 分钟 (从 {old_value} 更新)")
        except Exception as e:
            logging.error(f"[!] 解析 config.ini time_gap_minutes 出错: {e}")
            self._time_gap_minutes = 60

    async def get_time_gap(self) -> timedelta:
        """异步安全获取当前时间间隔"""
        async with self._lock:
            return timedelta(minutes=self._time_gap_minutes)

    def reload(self):
        """供 watchdog 调用：触发重载（线程安全的异步调度）"""
        global _main_thread_loop
        try:
            if _main_thread_loop is None:
                logging.warning("[!] 无法调度异步重载任务：主线程事件循环未设置，请调用 _set_main_thread_loop()")
                return
            if not _main_thread_loop.is_running():
                logging.warning("[!] 无法调度异步重载任务：主线程事件循环未运行")
                return
            # 使用已保存的主线程事件循环，安全调度任务
            _main_thread_loop.call_soon_threadsafe(asyncio.create_task, self._async_reload())
        except Exception as e:
            logging.error(f"[!] 调度异步重载任务时出错: {e}")

    async def _async_reload(self):
        """异步重载配置"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_config)

    def start_watching(self):
        """启动 watchdog 监听文件变化"""
        if self._observer:
            return
        class ConfigHandler(FileSystemEventHandler):
            def __init__(self, reloader):
                self.reloader = reloader
            def on_modified(self, event):
                if not event.is_directory and event.src_path == self.reloader.config_path:
                    logging.info(f"[!] 检测到 config.ini 被修改，正在重新加载...")
                    self.reloader.reload()
        event_handler = ConfigHandler(self)
        self._observer = Observer()
        self._observer.schedule(event_handler, path=self.config_dir, recursive=False)
        self._observer.start()


# 全局单例实例
config_reloader = ConfigReloader()
#config_reloader.start_watching()

# 便捷函数
async def get_time_gap():
    """获取当前生效的时间间隔（推荐在业务代码中使用）"""
    return await config_reloader.get_time_gap()