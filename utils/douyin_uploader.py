import os
import pyautogui
import json
import asyncio
import time
from playwright.async_api import async_playwright, TimeoutError

class DouyinUploader:
    def __init__(self, log_handler=None):
        self.playwright = None
        self.browser = None  # 注意：这里是 persistent context 对象
        self.page = None
        self.timeout = 60_000
        self.user_data_dir = 'user_data/douyin1'
        self._browser_lock = asyncio.Lock()
        self.log_handler = log_handler or (lambda msg: None)

    def log(self, msg):
        if self.log_handler:
            self.log_handler(msg)

    def _is_logged_in_url(self, url: str) -> bool:
        return (
            url.endswith("/creator-micro/home")
            or url.startswith("https://creator.douyin.com/creator-micro/content/manage")
        )

    async def start_browser(self):
        async with self._browser_lock:
            if self.browser:
                return

            try:
                screen_width, screen_height = pyautogui.size()
            except Exception:
                screen_width, screen_height = 1920, 1080

            SCALE_FACTOR = 1.25
            viewport_width = int(screen_width / SCALE_FACTOR)
            viewport_height = int(screen_height / SCALE_FACTOR)

            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=False,
                viewport={'width': viewport_width, 'height': viewport_height},
                device_scale_factor=SCALE_FACTOR,
                args=[
                    "--start-maximized",
                    f"--force-device-scale-factor={SCALE_FACTOR}",
                    "--disable-web-security",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            
            # 注入隐藏 webdriver 的脚本
            await self.browser.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
            
            await asyncio.sleep(1.5)
            pyautogui.hotkey('win', 'up')
            self.log("[✓] 浏览器已启动")

    async def close_browser(self):
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        self.browser = None
        self.page = None
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self.log("[✓] 浏览器已关闭")

    async def is_page_alive(self):
        if self.page is None or self.browser is None:
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    async def ensure_logged_in(self):
        alive = await self.is_page_alive()
        if not alive:
            self.log("[!] 检测到浏览器或页面已关闭，正在重启浏览器")
            await self.start_browser()

        try:
            current_url = self.page.url
            if not self._is_logged_in_url(current_url):
                self.log("[✓] 页面未处于登录状态，尝试跳转主页检测")
                await self.page.goto("https://creator.douyin.com/creator-micro/home", timeout=self.timeout)
                current_url = self.page.url

            if current_url == "https://creator.douyin.com/":
                self.log("[!] Cookie 失效或未登录，等待扫码登录")
                await self.wait_for_login()
            elif self._is_logged_in_url(current_url):
                self.log("[✓] 已处于登录状态")
            else:
                self.log(f"[!] 当前页面未知: {current_url}，尝试扫码登录")
                await self.wait_for_login()
        except Exception as e:
            self.log(f"[!] 页面检测异常: {e}，尝试扫码登录")
            await self.wait_for_login()

    async def wait_for_login(self):
        try:
            self.log("[✓] 请扫码登录抖音账号...")
            await self.page.wait_for_url("**/creator-micro/home", timeout=self.timeout)
            self.log("[✓] 登录成功")
        except TimeoutError:
            self.log("[!] 登录超时，请检查网络或扫码是否成功")

    async def upload_video(self, video_path, max_retry=3, retry_delay=2):
        attempt = 0
        while attempt < max_retry:
            try:
                await self.ensure_logged_in()
                self.log(f"[✓] 正在上传视频中...(尝试 {attempt+1}/{max_retry})")
                await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')

                if not os.path.exists(video_path):
                    self.log(f"[!] 视频文件不存在: {video_path}")
                    return False

                input_file = self.page.locator('input[type="file"]')
                await input_file.set_input_files(video_path)
                self.log("[✓] 视频文件已选择")

                preview_tab = self.page.locator('[class*="tabItem"]', has_text="预览视频")
                try:
                    await preview_tab.wait_for(timeout=180_000)
                    self.log("[✓] 视频预览已生成")
                except TimeoutError:
                    self.log("[!] 视频预览未生成，上传可能失败")
                    attempt += 1
                    if attempt < max_retry:
                        self.log(f"[!] {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    continue

                publish_button = self.page.locator(
                    'div[class^="content-confirm-container"] button',
                    has_text="发布"
                )
                try:
                    await publish_button.wait_for(timeout=self.timeout)
                    await publish_button.click()
                    self.log("[✓] 点击发布按钮")

                    try:
                        await self.page.wait_for_url(
                            "https://creator.douyin.com/creator-micro/content/manage?enter_from=publish*",
                            timeout=60_000
                        )
                        self.log("[✓] 页面跳转到发布管理页，发布成功")
                        return True
                    except TimeoutError:
                        self.log("[!] 未检测到跳转，上传可能失败")
                        attempt += 1
                        if attempt < max_retry:
                            self.log(f"[!] {retry_delay} 秒后重试...")
                            await asyncio.sleep(retry_delay)
                        continue

                except TimeoutError:
                    self.log("[!] 发布按钮未加载")
                    attempt += 1
                    if attempt < max_retry:
                        self.log(f"[!] {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    continue

            except Exception as e:
                self.log(f"[!] 上传异常: {e}")
                attempt += 1
                if attempt < max_retry:
                    self.log(f"[!] {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                continue

        self.log(f"[!] 视频最终上传失败: {video_path}")
        return False
