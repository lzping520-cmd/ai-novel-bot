"""发布器"""
from .wechat import WeChatPublisher
from .console import ConsolePublisher

__all__ = ["WeChatPublisher", "ConsolePublisher"]
