import os
import pickle
import pyautogui
import time
import configparser
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
                screen_width, screen_height = 1920, 1080

            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=[
                    "--start-maximized",
                    "--force-device-scale-factor=1",
                    "--disable-web-security"
                ]
            )
            context = await self.browser.new_context(
                viewport={'width': screen_width, 'height': screen_height - 100},
                device_scale_factor=1
            )
            self.page = await context.new_page()

            # 等待窗口弹出并手动最大化
            time.sleep(1.5)
            pyautogui.hotkey('win', 'up')

            self.log("[✓] 浏览器已启动并最大化")

    async def close_browser(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        self.page = None
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

    async def is_page_alive(self):
        # 判断页面和浏览器对象是否活着
        if self.page is None or self.browser is None:
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    async def ensure_logged_in(self):
        # 检查浏览器和页面是否存活，否则重启并用cookie自动登录
        alive = await self.is_page_alive()
        if not alive:
            self.log("[!] 检测到浏览器或页面已关闭，正在重启并重新登录")
            await self.close_browser()
            await self.login()
        else:
            # 检查是否还在登录态
            try:
                await self.page.goto("https://creator.douyin.com/", timeout=self.timeout)
                # 如果跳转到登录页说明登录态失效，需重新登录
                if "login" in self.page.url:
                    self.log("[!] 登录态失效，重新登录中")
                    await self.close_browser()
                    await self.login()
            except Exception as e:
                self.log(f"[!] 页面检测异常: {e}，尝试重启浏览器并重新登录")
                await self.close_browser()
                await self.login()

    async def login(self):
        await self.start_browser()
        await self.page.goto('https://creator.douyin.com/')
        if os.path.exists(self.cookies_path):
            self.log("[✓] 发现 Cookie，尝试自动登录...")
            try:
                cookies = await self.load_cookies(self.cookies_path)
                await self.page.context.add_cookies(cookies)
                await self.page.reload()
                try:
                    await self.page.wait_for_url("**/creator-micro/home", timeout=15000)
                    self.log("[✓] Cookie 登录成功")
                    return True
                except TimeoutError:
                    self.log("[!] Cookie 登录失败，准备扫码登录")
            except Exception as e:
                self.log(f"[!] 加载 Cookie 失败: {e}，准备扫码登录")

        self.log("[✓] 请扫码登录抖音账号...")
        try:
            await self.page.wait_for_url("**/creator-micro/home", timeout=self.timeout)
            self.log("[✓] 扫码登录成功，保存新的 Cookie")
            await self.save_cookies(self.cookies_path)
            return True
        except TimeoutError:
            self.log("[!] 登录超时，请检查网络或扫码是否成功")
            return False

    def read_channel_tags(self, ini_file='config/channels.ini'):
        config = configparser.ConfigParser(allow_no_value=True, delimiters=('=', ':'))
        config.optionxform = str  # 保持标签大小写
        config.read(ini_file, encoding='utf-8')
        if 'tags' in config:
            return list(config['tags'].keys())
        return []

    async def upload_video(self, video_path, max_retry=3, retry_delay=5):
        attempt = 0
        while attempt < max_retry:
            try:
                await self.ensure_logged_in()  # 检查并恢复登录
                self.log(f"[✓] 正在上传视频中: {video_path} (尝试 {attempt+1}/{max_retry})")
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

                # === 插入标签填写逻辑 ===
                tags = self.read_channel_tags('config/channels.ini')
                if tags:
                    tag_input = self.page.locator('div[contenteditable="true"][data-placeholder="添加作品简介"]')
                    try:
                        await tag_input.wait_for(timeout=10_000)
                        for tag in tags:
                            await tag_input.type(f"#{tag}", delay=50)  # delay=50表示每个字符间隔50毫秒
                            await tag_input.press("Enter")
                            await asyncio.sleep(0.3)
                        self.log("[✓] 标签已全部填写")
                    except TimeoutError:
                        self.log("[!] 标签输入框未找到，跳过标签填写")
                else:
                    self.log("[!] 没有可用标签，跳过标签填写")
                # === 标签逻辑结束 ===

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
                # 发生任何异常都先尝试关闭浏览器，下次循环重启并自动登录
                await self.close_browser()
                attempt += 1
                if attempt < max_retry:
                    self.log(f"[!] 上传失败，{retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                continue
        self.log(f"[!] 视频最终上传失败: {video_path}")
        return False
