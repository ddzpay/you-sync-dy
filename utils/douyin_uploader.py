import os
import random
import sys
import pyautogui
import asyncio
import time
import configparser
import re
from playwright.async_api import TimeoutError
from utils.notifier import notify_wecom_group

WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=9283fa7c-0e99-4c89-85e2-2908c7285804"

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#抖音队列与 worker 
MAX_CONCURRENT_UPLOADS = 1
UPLOAD_QUEUE_MAXSIZE = 4

upload_semaphore = None
upload_queue = None

def init_globals():
    global upload_semaphore, upload_queue
    if upload_semaphore is None:
        upload_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
    if upload_queue is None:
        upload_queue = asyncio.Queue(maxsize=UPLOAD_QUEUE_MAXSIZE)

def get_queue():
    return upload_queue

async def worker(uploader, log_handler):
    while True:
        try:
            task = await upload_queue.get()
            async with upload_semaphore:
                await process_upload_task(uploader, task, log_handler)
            upload_queue.task_done()
        except Exception as e:
            log_handler(f"[!] Douyin upload_worker异常: {type(e).__name__} | {str(e).splitlines()[0]}")

async def process_upload_task(uploader, task, log_handler):
    video_id = task['video_id']
    channel_id = task['channel_id']
    path = task['path']
    platform = task.get('platform', 'douyin')
    try:
        success = await uploader.upload_video(path, task=task)
        if success:
            if should_wait_preview(task):  # 长视频
                log_handler(f"[✓] 抖音上传成功，保留本地文件: {path}")
                try:
                    os.remove(path)
                except Exception as e:
                    log_handler(f"[!] 删除长视频文件失败: {type(e).__name__} | {str(e).splitlines()[0]}")
            else:
                log_handler(f"[✓] 抖音上传成功，保留本地文件: {path}")
        else:
            log_handler(f"[!] 抖音上传失败，保留文件: {path}")
            notify_wecom_group(f"[!]小包浆Vlog-抖音上传异常,视频最终上传失败，请尽快排查原因", WECOM_WEBHOOK)
    except Exception as e:
        log_handler(f"[!] 抖音上传过程异常: {type(e).__name__} | {str(e).splitlines()[0]}")

def should_wait_preview(task):
    if not task:
        return False
    target_channels = [
        "youtubemix",
        "wildkitchen7",
        "african_wildlife_tour",
        "africanlifestriez"
    ]
    return task.get("channel_id") in target_channels

#抖音队列与 worker结束 ======================================================

class DouyinUploader:
    def __init__(self, page, log_handler=None):
        self.page = page
        self.timeout = 60_000
        self.log_handler = log_handler or (lambda msg: None)
        self.tags = self.load_tags_from_config()
        self._has_checked_login = False

    def load_tags_from_config(self):
        tags = []
        base_dir = get_base_dir()
        config_path = os.path.join(base_dir, "config", "channels.ini")
        config = configparser.ConfigParser(allow_no_value=True)
        if os.path.exists(config_path):
            config.read(config_path, encoding="utf-8")
            if "tags" in config:
                tags = [k.strip() for k in config["tags"].keys() if k.strip()]
        return tags

    def log(self, msg):
        if self.log_handler:
            self.log_handler(msg)

    async def is_page_alive(self):
        if self.page is None:
            return False
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    async def is_login_page(self, page=None):
        pg = page or self.page
        try:
            await pg.wait_for_load_state('domcontentloaded', timeout=15_000)
            has_qr = await pg.locator('span.selected-w_E01s', has_text="扫码登录").count()
            has_code = await pg.locator('span.selected-w_E01s', has_text="验证码登录").count()
            if has_qr > 0 or has_code > 0:
                return True
            has_qr2 = await pg.locator('span', has_text="扫码登录").count()
            has_code2 = await pg.locator('span', has_text="验证码登录").count()
            if has_qr2 > 0 or has_code2 > 0:
                return True
            return False
        except Exception as e:
            if "Execution context was destroyed" in str(e):
                self.log("[i] 检测到抖音页面跳转，判定为已登录")
                return False
            self.log(f"[!] 检测抖音登录页面异常: {type(e).__name__} | {str(e).splitlines()[0]}")
            return False

    async def ensure_logged_in(self):
        if self._has_checked_login:
            return
        alive = await self.is_page_alive()
        if not alive:
            self.log("[!] 检测到抖音页面已关闭")
            raise Exception("页面未初始化")
        # 只在不是主页时跳主页
        if not self.page.url.startswith("https://creator.douyin.com/creator-micro/content/manage"):
            await self.page.goto("https://creator.douyin.com/creator-micro/content/manage", timeout=self.timeout)
        try:
            self.log("[✓] 正在检测抖音创作者中心主页登录状态...")
            await asyncio.sleep(1.0)
            is_login = await self.is_login_page()
            if is_login:
                self.log("[!] 抖音Cookie 失效或未登录，请扫码登录")
                await self.wait_for_login()
            else:
                self.log("[✓] 抖音Cookie 登录成功，已进入创作中心主页")
        except Exception as e:
            self.log(f"[!] 抖音页面检测异常: {type(e).__name__} | {str(e).splitlines()[0]}，尝试扫码登录")
            await self.wait_for_login()
        self._has_checked_login = True

    async def wait_for_login(self):
        try:
            self.log("[✓] 请扫码登录抖音账号...")
            for _ in range(60):
                is_login = not await self.is_login_page()
                if is_login:
                    self.log("[✓] 抖音登录成功")
                    return
                await asyncio.sleep(2)
            self.log("[!] 抖音登录超时，请检查网络或扫码是否成功")
        except TimeoutError:
            self.log("[!] 抖音登录超时，请检查网络或扫码是否成功")

    #自动封面函数
    async def set_cover(self):
        try:
            #self.log("[✓] 正在设置抖音视频封面(竖)...")
            vertical_cover_area = self.page.locator('div.coverControl-CjlzqC[style*="width: 90px"]')
            await vertical_cover_area.wait_for(timeout=10_000)

            # 只选出含“选择封面”的 filter-k_CjvJ（不是 background-OpVteV！）
            filter_btn = vertical_cover_area.locator('.filter-k_CjvJ:has-text("选择封面")')
            await filter_btn.wait_for(timeout=10_000)
            await filter_btn.click()
            await asyncio.sleep(1.0)

            # 点击弹窗“完成”按钮
            done_btn = self.page.locator('button.semi-button.secondary-zU1YLr span.semi-button-content', has_text="完成")
            parent_btn = done_btn.locator('..')
            await parent_btn.wait_for(timeout=10_000)
            await parent_btn.click()
            await asyncio.sleep(1.0)

            #self.log("[✓] 抖音封面设置成功（竖封面），使用默认首帧作为封面。")
        except Exception as e:
            self.log(f"[!] 抖音设置封面失败: {type(e).__name__} | {str(e).splitlines()[0]}")
            notify_wecom_group(f"[!]小包浆Vlog-抖音设置封面失败，请尽快查看原因", WECOM_WEBHOOK)
            
    async def fill_tags(self):
        if not self.tags or len(self.tags) < 2:
            self.log("[!] 可用标签数不足2个，不自动填写标签")
            return
        try:
            tag_input_box = self.page.locator('div[data-placeholder="添加作品简介"]')
            await tag_input_box.wait_for(timeout=10_000)
            await tag_input_box.click()
            selected_tags = random.sample(self.tags, 3)
            for tag in selected_tags:
                await self.page.keyboard.type(f'#{tag}')
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(0.4)
            #self.log(f"[✓] 已自动填写抖音标签：{' '.join('#'+t for t in selected_tags)}")
        except Exception as e:
            self.log(f"[!] 自动填写抖音标签时失败: {type(e).__name__} | {str(e).splitlines()[0]}")
            notify_wecom_group(f"[!]小包浆Vlog-自动填写抖音标签时失败，请尽快处理", WECOM_WEBHOOK)
    
    async def upload_video(self, video_path, task=None):
        try:
            self.log(f"[✓] 正在上传视频到抖音...")
            
            # 检查登录
            if await self.is_login_page():
                self.log("[!] 抖音当前未登录，请先扫码登录后再上传")
                notify_wecom_group(f"[!]小包浆Vlog-抖音当前未登录，请尽快处理", WECOM_WEBHOOK)
                return False

            try:
                hd_publish_btn = self.page.locator('span#douyin-creator-master-side-upload.header-button-text-Ww8aQU')
                await hd_publish_btn.wait_for(timeout=10_000)
                await hd_publish_btn.evaluate('node => node.closest("button").click()')
                #self.log("[✓] 已点击抖音高清发布按钮")
            except Exception as e:
                self.log(f"[!] 未找到或无法点击抖音“高清发布”按钮，降级为直接跳转: {type(e).__name__} | {str(e).splitlines()[0]}")
                await self.page.goto('https://creator.douyin.com/creator-micro/content/upload', timeout=self.timeout)

            await self.page.wait_for_url(re.compile(r"https://creator\.douyin\.com/creator-micro/content/upload.*"), timeout=15_000)

            if not os.path.exists(video_path):
                self.log(f"[!] 抖音视频文件不存在: {video_path}")
                return False

            input_file = self.page.locator('input[type="file"]')
            await input_file.set_input_files(video_path)
            #self.log("[✓] 抖音视频文件已选择")
            
            #自动填写标签
            await self.fill_tags()
            
            #自动设置封面
            await self.set_cover()
            
            #长视频要等待预览视频出现
            #wait_preview = should_wait_preview(task)
            #if wait_preview:
            #    preview_tab = self.page.locator('[class*="tabItem"]', has_text="预览视频")
            #    try:
            #        await preview_tab.wait_for(timeout=180_000)
            #        self.log("[✓] 视频预览已生成")
            #    except TimeoutError:
            #        self.log("[!] 视频预览未生成，上传可能失败")

            # 所有视频都要等待预览视频出现
            preview_tab = self.page.locator('[class*="tabItem"]', has_text="预览视频")
            try:
                await preview_tab.wait_for(timeout=180_000)
                self.log("[✓] 抖音视频预览已生成")
            except TimeoutError:
                self.log("[!] 视频预览未生成，上传可能失败")

            # 发布视频
            publish_button = self.page.locator(
                'div[class^="content-confirm-container"] button',
                has_text="发布"
            )
            try:
                await publish_button.wait_for(timeout=self.timeout)
                await publish_button.click()
                #self.log("[✓] 点击抖音发布按钮")

                try:
                    await self.page.wait_for_url(re.compile(r"https://creator\.douyin\.com/creator-micro/content/manage.*"), timeout=60_000)
                    #self.log("[✓] 页面跳转到抖音发布管理页，发布成功")
                    return True
                except TimeoutError:
                    self.log("[!] 未检测到跳转抖音发布管理页，上传可能失败")
                    return False

            except TimeoutError:
                self.log("[!] 抖音发布按钮未加载")
                return False

        except Exception as e:
            msg = f"[!] 抖音上传异常: {type(e).__name__} | {str(e).splitlines()[0]}"
            self.log(msg)
            notify_wecom_group(f"[!]小包浆Vlog-抖音上传异常,视频最终上传失败，请尽快排查原因", WECOM_WEBHOOK)
            return False