"""从本地 txt / md 文件导入"""
from __future__ import annotations

from pathlib import Path

from .base import Chapter, NovelImporter


class FileImporter(NovelImporter):
    """
    支持的输入：
      - 单个 .txt / .md 文件（内部按章节标题切分）
      - 一个目录（把里面所有 .txt / .md 按文件名排序拼接）
      - 文件列表（逐个读取拼接）
    """

    def __init__(self, path: str | Path | list[str | Path]):
        self.paths: list[Path] = []
        if isinstance(path, (list, tuple)):
            self.paths = [Path(p) for p in path]
        else:
            p = Path(path)
            if p.is_dir():
                self.paths = sorted(
                    p.glob("*.txt")
                ) + sorted(p.glob("*.md"))
            else:
                self.paths = [p]

    def fetch(self) -> list[Chapter]:
        if not self.paths:
            raise FileNotFoundError("没有找到任何 txt / md 文件")

        all_chapters: list[Chapter] = []
        for fp in self.paths:
            text = fp.read_text(encoding="utf-8")
            # 如果文件名本身就是"第N章 xxx"格式，尝试作为单章直接使用
            stem = fp.stem
            chapters = self.split_into_chapters(text, title_hint=stem)
            for c in chapters:
                c.source = str(fp)
            all_chapters.extend(chapters)

        # 重新编号（避免不同文件间编号冲突）
        all_chapters.sort(key=lambda c: c.chapter_no)
        for i, c in enumerate(all_chapters, 1):
            c.chapter_no = i

        return all_chapters
