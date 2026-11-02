"""
从微信公众号文章 URL 抓取正文
⚠️ 微信公众号没有开放"读取已发布文章"的公开 API，所以这条路走的是 HTTP 直接抓取
  （和浏览器打开 mp.weixin.qq.com/s/xxx 一样）

使用场景：
  - 你自己已经发布的文章，直接把 URL 列表给它，它会逐篇抓正文
  - 或者你把公众号文章"复制全文"贴到 txt 里，用 FileImporter 更稳

注意：
  - 如果文章被删除/设置为仅粉丝可见/需要打开历史文章，直接抓可能失败
  - 抓取频率别太高，否则可能触发微信的反爬
"""
from __future__ import annotations

import re
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from ..utils import setup_logger
from .base import Chapter, NovelImporter

log = setup_logger()


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class WechatArticleImporter(NovelImporter):
    """
    按 URL 列表抓取公众号文章正文
    URL 形如 https://mp.weixin.qq.com/s/xxxxxxxxxxxxx
    """

    def __init__(self, urls: Iterable[str]):
        self.urls: list[str] = list(urls)
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def fetch(self) -> list[Chapter]:
        chapters: list[Chapter] = []
        for i, url in enumerate(self.urls, 1):
            log.info("抓取第 %d 篇: %s", i, url)
            try:
                title, content = self._fetch_one(url)
                chapters.append(Chapter(
                    chapter_no=i,
                    title=title,
                    content=content,
                    source=url,
                ))
            except Exception as e:
                log.error("抓取失败 %s: %s", url, e)

        return chapters

    def _fetch_one(self, url: str) -> tuple[str, str]:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")

        # 标题 —— 公众号文章标题藏在 <h1 id="activity-name"> 里
        title_tag = soup.find("h1", id="activity-name")
        title = title_tag.get_text(strip=True) if title_tag else "未命名章节"

        # 正文 —— 在 <div id="js_content"> 里
        content_tag = soup.find("div", id="js_content")
        if not content_tag:
            raise RuntimeError("找不到正文 div#js_content，文章可能已被删除或需要登录")

        # 把 <p><br/> 等标签转成换行，去掉所有标签
        for br in content_tag.find_all("br"):
            br.replace_with("\n")
        for p in content_tag.find_all("p"):
            p.append("\n")

        content = content_tag.get_text("\n", strip=False)
        # 清理多余空行
        content = re.sub(r'\n{3,}', '\n\n', content).strip()

        return title, content
