import requests
from bs4 import BeautifulSoup
import configparser
import re
from datetime import datetime, timezone
import os
import logging
import asyncio

from utils.video_downloader import VideoDownloader
from utils.douyin_uploader import DouyinUploader
from utils.video_history import VideoHistory  # 需实现 is_processed(video_id), mark_processed(video_id)

def load_links_from_ini(path):
    config = configparser.ConfigParser()
    config.read(path, encoding='utf-8')
    if 'links' in config:
        return list(config['links'].values())
    return []

def is_tiktok(url):
    return 'tiktok.com' in url

def is_instagram(url):
    return 'instagram.com' in url

def get_recent_instagram_reel_links(profile_url, max_count=4):
    try:
        resp = requests.get(profile_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Instagram] Failed to fetch {profile_url}: {e}")
        return []
    soup = BeautifulSoup(resp.text, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        if '/reel/' in a['href']:
            full_link = 'https://www.instagram.com' + a['href']
            if full_link not in links:
                links.append(full_link)
            if len(links) >= max_count:
                break
    return links

def get_recent_tiktok_video_links(profile_url, max_count=4):
    try:
        resp = requests.get(profile_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[TikTok] Failed to fetch {profile_url}: {e}")
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

def check_instagram_reel_new(reel_url, threshold_seconds=120):
    try:
        resp = requests.get(reel_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Instagram] Failed to fetch reel {reel_url}: {e}")
        return False
    soup = BeautifulSoup(resp.text, 'html.parser')
    time_tag = soup.find('time', attrs={'datetime': True})
    if time_tag:
        try:
            publish_time = datetime.strptime(time_tag['datetime'], "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            diff = (now - publish_time).total_seconds()
            return diff <= threshold_seconds
        except Exception as e:
            print(f"[Instagram] Failed to parse time for {reel_url}: {e}")
            return False
    else:
        print(f"[Instagram] No <time> tag found in {reel_url}")
    return False

def check_tiktok_video_new(video_url, max_minutes=1):
    try:
        resp = requests.get(video_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[TikTok] Failed to fetch video {video_url}: {e}")
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
        print(f"[TikTok] No publish time span found in {video_url}")
    return False

def get_video_id_from_url(url):
    # TikTok: https://www.tiktok.com/@user/video/7506491557038640406
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    # Instagram: https://www.instagram.com/reel/DKZSBrMN51Z/
    m = re.search(r'/reel/([\w-]+)/', url)
    if m:
        return m.group(1)
    return None

async def process_and_upload(downloader, uploader, history, channel_id, video_url, video_id):
    downloaded_path = downloader.download_video(
        channel_id=channel_id,
        video_url=video_url,
        video_id=video_id
    )
    if downloaded_path:
        try:
            await uploader.ensure_logged_in()
            success = await uploader.upload_video(downloaded_path)
        except Exception as e:
            logging.warning(f"[!] 上传失败: {e}")
            success = False
        if success:
            history.mark_processed(video_id)
            try:
                os.remove(downloaded_path)
            except Exception as e:
                logging.warning(f"[!] 删除本地文件失败: {e}")

def main():
    all_links = load_links_from_ini('config/channels.ini')
    if not all_links:
        print("No channel links found in config/channels.ini [links]")
        return

    logging.basicConfig(level=logging.INFO)
    downloader = VideoDownloader()
    uploader = DouyinUploader(log_handler=logging.info)
    history = VideoHistory("utils/video_history.json")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    tasks = []
    for link in all_links:
        if is_instagram(link):
            print(f"[Instagram] Checking {link}")
            for reel_url in get_recent_instagram_reel_links(link):
                video_id = get_video_id_from_url(reel_url)
                if not video_id:
                    continue
                if history.is_processed(video_id):
                    print(f"[✓] 已处理过: {video_id}")
                    continue
                if check_instagram_reel_new(reel_url):
                    print(f"New Instagram reel: {reel_url}")
                    tasks.append(
                        process_and_upload(downloader, uploader, history, "instagram", reel_url, video_id)
                    )
        elif is_tiktok(link):
            print(f"[TikTok] Checking {link}")
            for video_url in get_recent_tiktok_video_links(link):
                video_id = get_video_id_from_url(video_url)
                if not video_id:
                    continue
                if history.is_processed(video_id):
                    print(f"[✓] 已处理过: {video_id}")
                    continue
                if check_tiktok_video_new(video_url):
                    print(f"New TikTok video: {video_url}")
                    tasks.append(
                        process_and_upload(downloader, uploader, history, "tiktok", video_url, video_id)
                    )
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks))

if __name__ == '__main__':
    main()