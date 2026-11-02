"""
导入器：把外部已有小说内容喂进状态仓库，实现"接着写"
"""
from .base import NovelImporter, Chapter
from .file_importer import FileImporter
from .wechat_importer import WechatArticleImporter

__all__ = ["NovelImporter", "Chapter", "FileImporter", "WechatArticleImporter"]
