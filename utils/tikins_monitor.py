import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone
import os
import logging
import asyncio
import time

from utils.video_downloader import VideoDownloader
from utils.douyin_uploader import DouyinUploader
from utils.video_history import VideoHistory  # 需实现 is_processed(platform, video_id), mark_processed(platform, video_id)

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def load_links_from_ini(path):
    links = []
    in_links_section = False
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('['):
                in_links_section = (line.lower() == '[links]')
                continue
            if in_links_section and line and not line.startswith(';') and not '=' in line:
                if line.startswith('http'):
                    links.append(line)
    return links

def is_tiktok(url):
    return 'tiktok.com' in url

def get_recent_tiktok_video_links(profile_url, max_count=4):
    try:
        resp = requests.get(profile_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log(f"[TikTok] 获取 {profile_url} 失败: {e}")
        return []
    soup = BeautifulSoup(resp.text, 'html.parser')
    links = []
    for div in soup.find_all('div', class_=re.compile(r'DivItemContainerV2')):
        # 跳过已置顶
        if div.find(string=re.compile('已置顶')):
            continue
        a = div.find('a', href=True)
        if a and '/video/' in a['href']:
            link = a['href'] if a['href'].startswith('http') else 'https://www.tiktok.com' + a['href']
            if link not in links:
                links.append(link)
            if len(links) >= max_count:
                break
    return links

def check_tiktok_video_new(video_url, max_minutes=1):
    try:
        resp = requests.get(video_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log(f"[TikTok] 获取视频 {video_url} 失败: {e}")
        return False
    soup = BeautifulSoup(resp.text, 'html.parser')
    # 寻找类似“14 小时前”/“1分钟前”/“23秒前” 的span
    match = soup.find('span', string=re.compile(r'(秒|分钟)前'))
    if match:
        text = match.get_text(strip=True)
        if '秒前' in text:
            return True
        elif '1分钟前' in text:
            return True
        elif m := re.match(r'(\d+)分钟前', text):
            minutes = int(m.group(1))
            return minutes <= max_minutes
    else:
        log(f"[TikTok] {video_url} 未找到发布时间")
    return False

def get_video_id_from_url(url):
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    return None

async def process_and_upload(downloader, uploader, history, platform, video_url, video_id):
    downloaded_path = downloader.download_video(
        channel_id=platform,
        video_url=video_url,
        video_id=video_id
    )
    if downloaded_path:
        try:
            await uploader.ensure_logged_in()
            success = await uploader.upload_video(downloaded_path)
        except Exception as e:
            log(f"[!] 上传失败: {e}")
            success = False
        if success:
            history.mark_processed(platform, video_id)

def check_tiktok_links(links, first_run=False):
    loop = asyncio.get_event_loop()
    tasks = []
    for link in links:
        found_new = False
        if is_tiktok(link):
            if first_run:
                log(f"[TikTok] 检查 {link}")
            for video_url in get_recent_tiktok_video_links(link):
                video_id = get_video_id_from_url(video_url)
                if not video_id:
                    continue
                if history.is_processed("tiktok", video_id):
                    log(f"[✓] 已处理过: {video_id}")
                    continue
                if check_tiktok_video_new(video_url):
                    log(f"[TikTok] 发现新视频: {video_url}")
                    tasks.append(
                        process_and_upload(downloader, uploader, history, "tiktok", video_url, video_id)
                    )
                    found_new = True
            if not found_new:
                if first_run:
                    log(f"[TikTok] 未发现新视频: {link}")
        else:
            if first_run:
                log(f"[?] 不支持的链接: {link}")
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks))
    else:
        if first_run:
            log("所有链接检查完成，没有需要上传的新视频。")

def main():
    links = load_links_from_ini('config/channels.ini')
    if not links:
        log("未在 config/channels.ini 的 [links] 中找到任何链接")
        return

    # 过滤出 TikTok 链接
    tiktok_links = [link for link in links if is_tiktok(link)]
    if not tiktok_links:
        log("未找到任何 TikTok 链接")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    global downloader, uploader, history
    downloader = VideoDownloader()
    uploader = DouyinUploader(log_handler=logging.info)
    history = VideoHistory("utils/video_history.json")

    first_run = True
    while True:
        check_tiktok_links(tiktok_links, first_run=first_run)
        first_run = False
        time.sleep(60)

if __name__ == '__main__':
    main()