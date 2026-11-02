"""AI 生成器"""
from .base import BaseGenerator
from .openai_compat import OpenAICompatGenerator

__all__ = ["BaseGenerator", "OpenAICompatGenerator"]
