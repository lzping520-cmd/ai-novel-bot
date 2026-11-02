"""导入器基类 & 数据结构"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Chapter:
    """单章原始内容"""
    chapter_no: int
    title: str
    content: str                   # 纯文本正文
    source: str = ""               # 来源描述（URL / 文件路径等）

    @property
    def word_count(self) -> int:
        return len(self.content.strip())


class NovelImporter(ABC):
    """导入器基类"""

    @abstractmethod
    def fetch(self) -> list[Chapter]:
        """返回按章节序号排序的章节列表"""

    # ---------- 通用工具 ----------
    @staticmethod
    def split_into_chapters(text: str, title_hint: str = "") -> list[Chapter]:
        """
        把一整块长文本按"第N章" / "# 章节名"等标题切成章节
        同时支持 Markdown 格式和纯文本格式
        """
        # 匹配章节标题的正则（覆盖常见网文格式）
        patterns = [
            r'^#\s*(第[零一二三四五六七八九十百千万\d]+[章节回卷].+)$',       # Markdown: # 第一章 xxx
            r'^(第[零一二三四五六七八九十百千万\d]+[章节回卷]\s*.+)$',        # 纯文本顶格: 第一章 xxx
            r'^(\d+[、.．]\s*.+)$',                                           # 1. xxx / 1、xxx
            r'^Chapter\s*(\d+)',                                              # 英文
        ]
        combined = "|".join(f"(?:{p})" for p in patterns)

        lines = text.splitlines()
        chapter_starts: list[tuple[int, str]] = []  # (行号, 标题)

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            m = re.match(combined, stripped, flags=re.MULTILINE)
            if m:
                # 提取标题（取第一个非空 group）
                title = next((g for g in m.groups() if g), stripped)
                chapter_starts.append((i, title))

        # 如果没找到任何章节分隔，就把整个文本作为第一章
        if not chapter_starts:
            return [Chapter(
                chapter_no=1,
                title=title_hint or "第一章",
                content=text.strip(),
            )]

        chapters: list[Chapter] = []
        for idx, (start_line, title) in enumerate(chapter_starts):
            end_line = chapter_starts[idx + 1][0] if idx + 1 < len(chapter_starts) else len(lines)
            body = "\n".join(lines[start_line + 1:end_line]).strip()

            # 从标题里提取章节号
            no = NovelImporter._extract_chapter_no(title) or idx + 1
            chapters.append(Chapter(
                chapter_no=no,
                title=NovelImporter._clean_title(title),
                content=body,
            ))

        return chapters

    @staticmethod
    def _extract_chapter_no(title: str) -> int | None:
        m = re.search(r'第\s*([零一二三四五六七八九十百千万\d]+)\s*[章节回卷]', title)
        if not m:
            return None
        raw = m.group(1)
        if raw.isdigit():
            return int(raw)
        # 简单的中文数字转换（1-99 够用）
        cn_map = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                  '六': 6, '七': 7, '八': 8, '九': 9}
        if raw == '十':
            return 10
        if raw.startswith('十') and len(raw) == 2:
            return 10 + cn_map.get(raw[1], 0)
        if raw.endswith('十') and len(raw) == 2:
            return cn_map.get(raw[0], 0) * 10
        if len(raw) == 2 and '十' in raw:
            tens = cn_map.get(raw[0], 0)
            ones = cn_map.get(raw[-1], 0)
            return tens * 10 + ones
        return cn_map.get(raw)

    @staticmethod
    def _clean_title(title: str) -> str:
        """去掉 markdown 标记和多余空白"""
        return re.sub(r'^[#\s]+', '', title).strip()
