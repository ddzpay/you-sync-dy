import os
import pyautogui
from playwright.async_api import async_playwright

# 导入同级 utils 下的上传器
from .douyin_uploader import DouyinUploader
from .kuaishou_uploader import KuaishouUploader

class BrowserManager:
    def __init__(self, log_handler=print):
        self.playwright = None
        self.browser = None
        self.douyin_page = None
        self.kuaishou_page = None
        self.uploader_douyin = None
        self.uploader_kuaishou = None
        self.log_handler = log_handler

    async def start(self):
        try:
            screen_width, screen_height = pyautogui.size()
        except Exception:
            screen_width, screen_height = 1920, 1080

        SCALE_FACTOR = 1.25
        viewport_width = int(screen_width / SCALE_FACTOR)
        viewport_height = int(screen_height / SCALE_FACTOR)

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user_data", "Profile1")),
            headless=False,
            viewport={'width': viewport_width, 'height': viewport_height},
            device_scale_factor=SCALE_FACTOR,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            args=["--start-maximized"],
            ignore_default_args=["--enable-automation", "--no-sandbox"]
        )
        await self.browser.add_init_script("""
            // 1. 伪装 webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

            // 2. 伪装 Chrome 运行环境
            window.navigator.chrome = {
                runtime: {},
                loadTimes: () => {},
                csi: () => {},
            };

            // 3. 伪装权限
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );

            // 4. 伪装插件和语言
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

            // 5. 伪装内存、线程、网络
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'connection', {
                get: () => ({
                    downlink: 10,
                    effectiveType: "4g",
                    rtt: 50,
                    saveData: false
                })
            });

            // 6. 伪装 MIME types
            Object.defineProperty(navigator, 'mimeTypes', {
                get: () => [{ type: 'application/pdf' }]
            });

            // 7. 伪装屏幕参数
            Object.defineProperty(window, 'devicePixelRatio', { get: () => 1.25 });
            Object.defineProperty(screen, 'width', { get: () => 1920 });
            Object.defineProperty(screen, 'height', { get: () => 1080 });

            // 8. 关闭 OffscreenCanvas
            window.OffscreenCanvas = undefined;
        """)
        self.kuaishou_page = self.browser.pages[0]
        self.douyin_page = await self.browser.new_page()
        await self.douyin_page.goto("https://creator.douyin.com/creator-micro/content/manage")
        await self.kuaishou_page.goto("https://cp.kuaishou.com/article/manage/video")
        self.uploader_douyin = DouyinUploader(page=self.douyin_page, log_handler=self.log_handler)
        self.uploader_kuaishou = KuaishouUploader(page=self.kuaishou_page, log_handler=self.log_handler)
        await self.uploader_douyin.ensure_logged_in()
        await self.uploader_kuaishou.ensure_logged_in()

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()