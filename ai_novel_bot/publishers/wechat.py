"""
微信公众号发布器

权限说明：
  - 所有公众号都能创建草稿（draft/add）
  - 只有「认证的服务号」才能自动提交发布（freepublish/submit）
  - 其他类型：自动发布会返回 48001，草稿箱里手动点群发即可
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ..config import WeChatConfig
from ..utils import setup_logger
from ..storage.state import ChapterRecord

log = setup_logger()

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
ADD_DRAFT_URL = "https://api.weixin.qq.com/cgi-bin/draft/add"
PUBLISH_URL = "https://api.weixin.qq.com/cgi-bin/freepublish/submit"
PUBLISH_RESULT_URL = "https://api.weixin.qq.com/cgi-bin/freepublish/get"
UPLOAD_MATERIAL_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"

class WeChatAPIError(RuntimeError):
    def __init__(self, errcode: int, errmsg: str, raw: Any = None):
        super().__init__(f"微信接口错误 [{errcode}] {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg
        self.raw = raw

class WeChatPublisher:

    def __init__(self, cfg: WeChatConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self._token: str = ""
        self._token_expire: float = 0.0

    # ---------- Token ----------
    def _get_token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_expire - 60:
            return self._token

        if not self.cfg.app_id or not self.cfg.app_secret:
            raise RuntimeError("微信 AppID / AppSecret 未配置")

        resp = self.session.get(
            TOKEN_URL,
            params={"grant_type": "client_credential",
                    "appid": self.cfg.app_id, "secret": self.cfg.app_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise WeChatAPIError(data.get("errcode", -1), data.get("errmsg", ""), data)

        self._token = data["access_token"]
        self._token_expire = now + data.get("expires_in", 7200)
        return self._token

    def _post(self, url: str, data: dict, params: dict | None = None) -> dict:
        params = params or {}
        params.setdefault("access_token", self._get_token())
        resp = self.session.post(
            url, params=params,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
        if "errcode" in result and result["errcode"] != 0:
            raise WeChatAPIError(result["errcode"], result.get("errmsg", ""), result)
        return result

    def _post_file(self, url: str, file_path: Path, params: dict | None = None) -> dict:
        params = params or {}
        params.setdefault("access_token", self._get_token())
        with open(file_path, "rb") as f:
            files = {"media": (file_path.name, f, "image/jpeg")}
            resp = self.session.post(url, params=params, files=files, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if "errcode" in result and result["errcode"] != 0:
            raise WeChatAPIError(result["errcode"], result.get("errmsg", ""), result)
        return result

    # ---------- 封面图 ----------
    def _resolve_thumb_media_id(self) -> str:
        direct = getattr(self.cfg, "thumb_media_id", "") or ""
        if direct:
            return direct

        cover_path = getattr(self.cfg, "cover_image", "") or ""
        if cover_path:
            p = Path(cover_path)
            if not p.exists():
                raise RuntimeError(f"封面图文件不存在: {cover_path}")
            log.info("上传封面图: %s", cover_path)
            data = self._post_file(UPLOAD_MATERIAL_URL, p, params={"type": "thumb"})
            media_id = data.get("media_id", "")
            if not media_id:
                raise RuntimeError(f"封面图上传失败，返回: {data}")
            log.info("封面图上传成功，media_id=%s（建议填到 config.yaml 缓存）", media_id)
            return media_id

        raise RuntimeError(
            "微信公众号现在强制要求封面图！\n"
            "  A) config.yaml 的 wechat 段加：thumb_media_id: '从公众号后台素材管理里复制'\n"
            "  B) 或者加：cover_image: 'D:/AIBOT/cover.jpg' 让代码自动上传"
        )

    # ---------- HTML ----------
    @staticmethod
    def _build_html(title: str, body: str) -> str:
        paragraphs = [p.strip() for p in body.splitlines() if p.strip()]
        parts = [
            f'<section style="text-align:center;font-size:20px;font-weight:bold;'
            f'color:#333;padding:20px 0 10px;">{title}</section>'
        ]
        for p in paragraphs:
            parts.append(
                f'<p style="text-indent:2em;font-size:16px;line-height:1.9;'
                f'color:#333;margin:12px 0;">{p}</p>'
            )
        return "".join(parts)

    # ---------- 草稿 / 发布 ----------
    def create_draft(self, chapter: ChapterRecord, novel_title: str,
                     thumb_media_id: str) -> str:
        html = self._build_html(f"第{chapter.chapter_no}章 {chapter.title}", chapter.content)
        article = {
            "articles": [{
                "title": f"{novel_title} · 第{chapter.chapter_no}章 {chapter.title}",
                "author": "星焰平际",
                "digest": chapter.summary or f"第{chapter.chapter_no}章，精彩继续。",
                "content": html,
                "content_source_url": "",
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 1,
                "only_fans_can_comment": 0,
            }]
        }
        data = self._post(ADD_DRAFT_URL, article)
        return data["media_id"]

    def publish_draft(self, media_id: str) -> str:
        data = self._post(PUBLISH_URL, {"media_id": media_id})
        return data["publish_id"]

    def get_publish_result(self, publish_id: str, max_wait: int = 60) -> dict:
        start = time.time()
        while time.time() - start < max_wait:
            data = self._post(PUBLISH_RESULT_URL, {"publish_id": publish_id})
            if data.get("publish_status") == 0:
                return data
            time.sleep(2)
        raise TimeoutError("微信发布超时")

    # ---------- 入口 ----------
    def publish(self, chapter: ChapterRecord, novel_title: str) -> str:
        print(f"[WeChatPublisher] 正在创建草稿：第{chapter.chapter_no}章《{chapter.title}》")

        # 0. 封面图
        thumb_id = self._resolve_thumb_media_id()

        # 1. 创建草稿（所有公众号都有权限）
        media_id = self.create_draft(chapter, novel_title, thumb_id)
        print(f"  ✅ 草稿创建成功！media_id: {media_id}")

        # 2. 尝试自动发布（只有认证服务号有权限）
        try:
            publish_id = self.publish_draft(media_id)
            print(f"  🚀 已提交自动发布，publish_id: {publish_id}")
            result = self.get_publish_result(publish_id)
            article_url = result.get("article_url", "")
            if article_url:
                print(f"  ✅ 发布完成！URL: {article_url}")
                return article_url
            return media_id
        except WeChatAPIError as e:
            if e.errcode == 48001:
                print(f"  ⚠️ 自动发布无权限（48001），已存为草稿")
                print(f"  📝 请到公众号后台 → 素材管理 → 草稿箱 → 手动点「群发」")
                # 抛异常让外层不 mark_published，下次 run_once 会继续尝试创建草稿
                raise RuntimeError(
                    f"第{chapter.chapter_no}章草稿已创建（media_id={media_id}），"
                    f"但自动发布无权限（48001）。请在公众号后台手动群发。"
                )
            raise