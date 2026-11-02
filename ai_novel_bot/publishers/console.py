"""
控制台发布器 - 测试/本地使用
不联网，只把文章存本地并打印预览
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import WeChatConfig
from ..storage.state import ChapterRecord


class ConsolePublisher:
    """本地文件 + 控制台输出"""

    def __init__(self, wechat_cfg: WeChatConfig, out_dir: Path | None = None):
        self.cfg = wechat_cfg
        self.out_dir = Path(out_dir or Path.cwd() / "output")
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, chapter: ChapterRecord, novel_title: str) -> str:
        """把章节保存为 Markdown 文件，返回文件路径"""
        slug = f"chapter_{chapter.chapter_no:04d}"
        path = self.out_dir / f"{novel_title}_{slug}.md"

        md = f"# {novel_title}\n\n## 第{chapter.chapter_no}章 {chapter.title}\n\n"
        md += chapter.content
        md += f"\n\n---\n*生成于 {chapter.generated_at}*"

        path.write_text(md, encoding="utf-8")

        print(f"\n{'='*60}")
        print(f"[ConsolePublisher] 第{chapter.chapter_no}章 《{chapter.title}》")
        print(f"  字数: {chapter.word_count}")
        print(f"  文件: {path}")
        print(f"{'='*60}\n")
        # 只预览前 400 字
        preview = chapter.content[:400].replace("\n", " ")
        print(preview, "...\n")

        return str(path)
