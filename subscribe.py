import aiohttp
import asyncio
import logging
import time

async def subscribe_channel(channel_id: str, callback_url: str) -> tuple[bool, str]:
    return await _submit_subscription(channel_id, callback_url, mode="subscribe")

async def unsubscribe_channel(channel_id: str, callback_url: str) -> tuple[bool, str]:
    return await _submit_subscription(channel_id, callback_url, mode="unsubscribe")

async def _submit_subscription(channel_id: str, callback_url: str, mode: str, retry: int = 3, delay: int = 3) -> tuple[bool, str]:
    hub_url = 'https://pubsubhubbub.appspot.com/subscribe'
    topic = f'https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}'
    data = {
        'hub.mode': mode,
        'hub.topic': topic,
        'hub.callback': callback_url,
        'hub.verify': 'async'
    }
    for attempt in range(retry):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(hub_url, data=data) as resp:
                    if resp.status == 202:
                        msg = f"[✓] {mode.upper()} 成功: {channel_id}"
                        return True, msg
                    else:
                        response_text = await resp.text()
                        msg = f"[!] {mode.upper()} 失败: {resp.status} - {response_text}"
        except Exception as e:
            msg = f"[!] 网络异常 ({mode}, 尝试 {attempt+1}/{retry}): {e}"
        if attempt < retry - 1:
            await asyncio.sleep(delay)
    msg = f"[!] {mode.upper()} 最终失败: {channel_id}"
    return False, msg