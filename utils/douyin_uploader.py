import os
import pickle
import pyautogui
import time
from playwright.async_api import async_playwright, TimeoutError
import asyncio

class DouyinUploader:
    def __init__(self, log_handler=None):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.cookies_path = 'cookies/douyin.pkl'
        self.timeout = 60_000  # ms
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
            self.context = await self.browser.new_context(
                viewport={'width': screen_width, 'height': screen_height - 100},
                device_scale_factor=1
            )
            self.page = await self.context.new_page()

            time.sleep(1.5)
            pyautogui.hotkey('win', 'up')
            self.log("[✓] 浏览器已启动并最大化")

    async def close_browser(self):
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        self.page = None
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        self.context = None
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        self.browser = None
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self.log("[✓] 浏览器已关闭")

    async def save_cookies(self, path):
        cookies = await self.page.context.cookies()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump(cookies, f)
        self.log("[✓] Cookie 已保存")

    async def load_cookies(self, path):
        try:
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
            return cookies
        except Exception as e:
            self.log(f"[!] Cookie 加载失败: {e}，已删除损坏的cookie文件")
            try:
                os.remove(path)
            except Exception:
                pass
            return []

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
            self.log("[!] 检测到浏览器或页面已关闭，正在重启并重新登录")
            await self.close_browser()
            await self.login()
        else:
            try:
                await self.page.goto("https://creator.douyin.com/", timeout=self.timeout)
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

    async def upload_video(self, video_path, max_retry=3, retry_delay=5):
        attempt = 0
        while attempt < max_retry:
            try:
                await self.ensure_logged_in()
                self.log(f"[✓] 正在上传视频中: {video_path} (尝试 {attempt+1}/{max_retry})")
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
                    await self.page.screenshot(path=f"upload_fail_{attempt+1}.png")
                    attempt += 1
                    if attempt < max_retry:
                        self.log(f"[!] {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    continue

                publish_button = self.page.locator('div[class^="content-confirm-container"] button', has_text="发布")
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
                        self.log("[!] 未检测到发布成功后的跳转，上传可能失败")
                        await self.page.screenshot(path=f"publish_fail_{attempt+1}.png")
                        attempt += 1
                        if attempt < max_retry:
                            self.log(f"[!] {retry_delay} 秒后重试...")
                            await asyncio.sleep(retry_delay)
                        continue

                except TimeoutError:
                    self.log("[!] 发布按钮未加载，发布失败")
                    await self.page.screenshot(path=f"publish_btn_fail_{attempt+1}.png")
                    attempt += 1
                    if attempt < max_retry:
                        self.log(f"[!] {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                    continue

            except Exception as e:
                self.log(f"[!] 上传过程发生异常: {e}")
                try:
                    await self.page.screenshot(path=f"upload_exception_{attempt+1}.png")
                except Exception:
                    pass
                await self.close_browser()
                attempt += 1
                if attempt < max_retry:
                    self.log(f"[!] 上传失败，{retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                continue
        self.log(f"[!] 视频最终上传失败: {video_path}")
        return False
