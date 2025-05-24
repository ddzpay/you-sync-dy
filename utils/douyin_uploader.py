import os
import pickle
import pyautogui
from playwright.async_api import async_playwright, TimeoutError
import asyncio

class DouyinUploader:
    def __init__(self, log_handler=None):
        self.playwright = None
        self.browser = None
        self.page = None
        self.cookies_path = 'cookies/douyin.pkl'
        self.timeout = 60_000  # 毫秒
        self._browser_lock = asyncio.Lock()
        self.log_handler = log_handler or (lambda msg: None)

    def log(self, msg):
        if self.log_handler:
            self.log_handler(msg)

    async def start_browser(self):
        async with self._browser_lock:
            if self.browser:
                return
            try:
                screen_width, screen_height = pyautogui.size()
            except Exception:
                screen_width = 1920
                screen_height = 1080
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=False, args=["--start-maximized"]
            )
            context = await self.browser.new_context(viewport={"width": screen_width, "height": screen_height})
            self.page = await context.new_page()
            self.log("[✓] 浏览器已启动")

    async def close_browser(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        self.log("[✓] 浏览器已关闭")

    async def save_cookies(self, path):
        cookies = await self.page.context.cookies()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump(cookies, f)
        self.log("[✓] Cookie 已保存")

    async def load_cookies(self, path):
        with open(path, 'rb') as f:
            cookies = pickle.load(f)
        return cookies

    async def login(self):
        await self.start_browser()
        await self.page.goto('https://creator.douyin.com/')
        if os.path.exists(self.cookies_path):
            self.log("[✓] 发现 Cookie，尝试自动登录...")
            cookies = await self.load_cookies(self.cookies_path)
            await self.page.context.add_cookies(cookies)
            await self.page.reload()
            try:
                await self.page.wait_for_url("**/creator-micro/home", timeout=15000)
                self.log("[✓] Cookie 登录成功")
                return True
            except TimeoutError:
                self.log("[!] Cookie 登录失败，准备扫码登录")

        self.log("[✓] 请扫码登录抖音账号...")
        try:
            await self.page.wait_for_url("**/creator-micro/home", timeout=self.timeout)
            self.log("[✓] 扫码登录成功，保存新的 Cookie")
            await self.save_cookies(self.cookies_path)
            return True
        except TimeoutError:
            self.log("[!] 登录超时，请检查网络或扫码是否成功")
            return False

    async def upload_video(self, video_path, max_retry=1, retry_delay=5):
        attempt = 0
        while attempt < max_retry:
            try:
                self.log(f"[✓] 准备上传视频: {video_path} (尝试 {attempt+1}/{max_retry})")
                await self.page.goto('https://creator.douyin.com/creator-micro/content/upload')

                if not os.path.exists(video_path):
                    self.log(f"[!] 视频文件不存在: {video_path}")
                    return False

                # 使用 Locator 并等待文件输入框出现
                input_file = self.page.locator('input[type="file"]')
                await input_file.set_input_files(video_path)
                self.log("[✓] 视频文件已选择")

                # 等待预览区域
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

                # 点击发布按钮
                publish_button = self.page.locator('div[class^="content-confirm-container"] button', has_text="发布")
                try:
                    await publish_button.wait_for(timeout=self.timeout)
                    await publish_button.click()
                    self.log("[✓] 点击发布按钮")

                    # 等待发布成功页面跳转
                    try:
                        await self.page.wait_for_url(
                            "https://creator.douyin.com/creator-micro/content/manage?enter_from=publish*",
                            timeout=60_000
                        )
                        self.log("[✓] 页面跳转到发布管理页，发布成功")
                        return True
                    except TimeoutError:
                        self.log("[!] 未检测到发布成功后的跳转，上传可能失败")
                        attempt += 1
                        if attempt < max_retry:
                            self.log(f"[!] {retry_delay} 秒后重试...")
                            await asyncio.sleep(retry_delay)
                        continue

                except TimeoutError:
                    self.log("[!] 发布按钮未加载，发布失败")
                    attempt += 1
                    if attempt < max_retry:
                        self.log(f"[!] {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    continue

            except Exception as e:
                self.log(f"[!] 上传过程发生异常: {e}")
            attempt += 1
            if attempt < max_retry:
                self.log(f"[!] 上传失败，{retry_delay} 秒后重试...")
                await asyncio.sleep(retry_delay)
        self.log(f"[!] 视频最终上传失败: {video_path}")
        return False
