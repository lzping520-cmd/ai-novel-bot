"""
小说进度 & 状态持久化管理
使用 JSON 文件存储，简单可靠

新增功能：
  - 章节废弃标记（AI 写砸了可以打"废弃"，自动重写）
  - 多小说存档管理（列出所有存档、切换存档）
  - 设定固化（前 N 章的完整文本永不丢失，始终喂给 AI）
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ChapterRecord:
    """已生成章节记录"""
    chapter_no: int
    title: str
    summary: str
    content: str
    word_count: int
    generated_at: str                    # ISO 时间
    published: bool = False
    publish_url: str = ""
    discarded: bool = False              # ✅ 废弃标记：AI 写砸了 → 标记废弃 → 下次自动重写该章
    discard_reason: str = ""             # ✅ 废弃原因：供日志/UI 展示
    rewrite_count: int = 0              # ✅ 已经重写了几次（防止死循环）


@dataclass
class NovelState:
    """小说全局状态"""
    novel_title: str = ""
    current_chapter: int = 0             # 已完成章节数（不含废弃的）
    total_words: int = 0
    story_so_far: str = ""               # 供下一章续写的剧情摘要
    characters: str = ""                 # 动态更新的人物表
    last_published_at: str = ""
    chapters: list[ChapterRecord] = field(default_factory=list)
    custom_memory: dict[str, Any] = field(default_factory=dict)
    setting_seed: str = ""               # ✅ 设定固化：前 N 章原文提炼，永不丢失

    # ------ 序列化 ------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chapters"] = [asdict(c) for c in self.chapters]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NovelState":
        state = cls()
        for k, v in data.items():
            if k == "chapters":
                state.chapters = [ChapterRecord(**c) for c in v]
            elif hasattr(state, k):
                setattr(state, k, v)
        return state


class StateStore:
    """JSON 文件状态仓库"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state: NovelState | None = None

    # ------ IO ------
    def load(self) -> NovelState:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._state = NovelState.from_dict(json.load(f))
        else:
            self._state = NovelState()
            self.save()
        return self._state

    def save(self) -> None:
        if self._state is None:
            return
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # ------ 便捷访问 ------
    @property
    def state(self) -> NovelState:
        if self._state is None:
            return self.load()
        return self._state

    # ------ 章节管理 ------
    def add_chapter(self, chapter: ChapterRecord) -> None:
        st = self.state
        st.current_chapter = chapter.chapter_no
        st.total_words += chapter.word_count
        st.story_so_far = self._build_recent_summary(st)
        # 设定固化：前 3 章完整文本拼进 setting_seed
        st.setting_seed = self._build_setting_seed(st)
        st.chapters.append(chapter)
        self.save()

    @staticmethod
    def _build_recent_summary(st: NovelState, keep: int = 5) -> str:
        """保留最近 N 章**未废弃**的摘要作为 story_so_far"""
        active = [c for c in st.chapters if not c.discarded]
        recent = active[-keep:]
        return "\n".join(
            f"【第{c.chapter_no}章 {c.title}】{c.summary}" for c in recent
        )

    @staticmethod
    def _build_setting_seed(st: NovelState, keep: int = 3) -> str:
        """
        设定固化：把前 N 章**未废弃**的完整文本拼在一起
        前 N 章是世界观/人物/大纲的基石，必须永不丢失地喂给 AI
        """
        active = [c for c in st.chapters if not c.discarded]
        early = active[:keep]
        if not early:
            return ""
        parts = []
        for c in early:
            parts.append(f"===== 第{c.chapter_no}章《{c.title}》=====\n{c.content}")
        return "\n\n".join(parts)

    def mark_published(self, chapter_no: int, url: str = "") -> None:
        for c in self.state.chapters:
            if c.chapter_no == chapter_no and not c.discarded:
                c.published = True
                c.publish_url = url
        self.save()

    def discard_chapter(self, chapter_no: int, reason: str = "") -> None:
        """
        废弃某一章：标记为 discarded=True
        下次 run_once 的时候，generate_chapter 会**从这个 chapter_no 重新写**
        """
        for c in self.state.chapters:
            if c.chapter_no == chapter_no:
                c.discarded = True
                c.discard_reason = reason or "手动标记废弃"
        # 重算 summary 和 setting_seed（排除废弃章节）
        st = self.state
        st.current_chapter = max(
            (c.chapter_no for c in st.chapters if not c.discarded), default=0
        )
        st.story_so_far = self._build_recent_summary(st)
        st.setting_seed = self._build_setting_seed(st)
        self.save()

    def undiscard_chapter(self, chapter_no: int) -> None:
        """取消废弃标记（误操作时用）"""
        for c in self.state.chapters:
            if c.chapter_no == chapter_no and c.discarded:
                c.discarded = False
                c.discard_reason = ""
        st = self.state
        st.current_chapter = max(
            (c.chapter_no for c in st.chapters if not c.discarded), default=0
        )
        st.story_so_far = self._build_recent_summary(st)
        st.setting_seed = self._build_setting_seed(st)
        self.save()

    def find_next_chapter_no(self) -> int:
        """
        找出下一章应该写的编号
        - 如果有废弃的章节 → 从最小的废弃编号开始重写
        - 否则 → current_chapter + 1
        """
        discarded = [c for c in self.state.chapters if c.discarded]
        if discarded:
            return min(c.chapter_no for c in discarded)
        return self.state.current_chapter + 1

    def get_active_chapters(self) -> list[ChapterRecord]:
        """返回所有未废弃的章节"""
        return [c for c in self.state.chapters if not c.discarded]

    # ------ 多小说存档管理 ------
    @staticmethod
    def list_all_archives(data_dir: Path) -> list[str]:
        """列出 data 目录下所有小说存档（从 state.json 里读标题）"""
        if not data_dir.exists():
            return []
        archives = []
        for f in data_dir.glob("*_state.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                title = data.get("novel_title") or f.stem.replace("_state", "")
                chapters = data.get("chapters", [])
                active_count = sum(1 for c in chapters if not c.get("discarded"))
                archives.append(f"{title} ({active_count}章)")
            except Exception:
                archives.append(f.stem)
        return sorted(archives)


# ------ 模块级便捷函数：列出所有存档 ------
def list_all_archives(data_dir: Path) -> list[str]:
    return StateStore.list_all_archives(data_dir)
