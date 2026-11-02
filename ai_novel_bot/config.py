"""
全局配置加载模块
优先从 config.yaml 读取，敏感信息从 .env 读取

v3 多用户支持：
  - 系统默认配置：PROJECT_ROOT/config.yaml（admin / cli --config 指定）
  - 用户独立配置：PROJECT_ROOT/users/{username}/config.yaml
  - 用户独立数据：PROJECT_ROOT/users/{username}/data/
  - 用户独立输出：PROJECT_ROOT/users/{username}/output/
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
USERS_ROOT = PROJECT_ROOT / "users"


# ============================================================
# 多用户路径工具
# ============================================================

def get_user_dir(username: str) -> Path:
    """返回用户根目录 users/{username}（自动创建）"""
    udir = USERS_ROOT / username
    udir.mkdir(parents=True, exist_ok=True)
    return udir


def get_user_config_path(username: str) -> Path:
    """返回用户专属 config.yaml 路径"""
    return get_user_dir(username) / "config.yaml"


def get_user_data_dir(username: str) -> Path:
    """返回用户专属 data 目录"""
    d = get_user_dir(username) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_user_output_dir(username: str) -> Path:
    """返回用户专属 output 目录"""
    o = get_user_dir(username) / "output"
    o.mkdir(parents=True, exist_ok=True)
    return o


def ensure_user_initialized(username: str, template_yaml: str | None = None) -> Path:
    """
    确保用户目录已初始化
    第一次登录时：创建带安全默认值的 config.yaml（AI Key / 微信 Secret 等敏感字段强制留空）
    返回用户 config.yaml 路径
    """
    cfg_path = get_user_config_path(username)
    if not cfg_path.exists():
        # 🔒 新用户永远用安全模板——敏感字段（AI Key / 微信 Secret / AppID）**必须为空**
        # 不能复制系统 config.yaml，防止把卖家的 Key 泄露给用户
        if template_yaml:
            cfg_path.write_text(template_yaml, encoding="utf-8")
        else:
            cfg_path.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")

        # 创建子目录
        get_user_data_dir(username)
        get_user_output_dir(username)

    return cfg_path


def list_all_users() -> list[str]:
    """列出所有已创建的用户"""
    if not USERS_ROOT.exists():
        return []
    return sorted(
        d.name for d in USERS_ROOT.iterdir()
        if d.is_dir() and (d / "config.yaml").exists()
    )


# ============================================================
# 默认配置模板（给新用户用）
# ============================================================
_DEFAULT_CONFIG_TEMPLATE = """# AI 模型配置
ai:
  provider: doubao
  base_url: "http://43.136.83.81:10088/v1"
  model: qwen3.8-max
  api_key: ""            # ⚠️ 这里填你自己的 API Key
  temperature: 0.85
  max_tokens: 3500
  timeout: 600

# 小说配置
novel:
  title: "我的小说"
  genre: "玄幻 / 仙侠"
  style: "节奏紧凑、打斗精彩、悬念密集"
  target_words_per_chapter: 2000
  chapters_per_day: 1
  world_setting: |
    （你的世界观设定，AI 会把这里作为创作基础）
  main_characters: |
    （主要人物介绍：姓名 / 性格 / 能力 / 背景）
  outline: |
    （整体故事大纲，分阶段描述剧情走向）

# 微信公众号配置
wechat:
  enabled: true
  app_id: ""              # ⚠️ 你的公众号 AppID
  app_secret: ""          # ⚠️ 你的公众号 AppSecret
  thumb_media_id: ""      # ⚠️ 封面图 media_id（公众号后台素材管理里复制）
  author: ""              # 文章作者名（公众号显示在标题下方）
  use_draft: true

# 定时调度
schedule:
  run_time: "08:00"       # 固定时间每日触发
  run_window: "08:00-09:30"  # 注释掉上面那行启用随机窗口
  test_mode: false
"""


# ============================================================
# 数据类（和之前一样，只是 AppConfig.load 支持 user 参数）
# ============================================================

@dataclass
class AIConfig:
    """AI 模型配置"""
    provider: str = "openai"
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.85
    max_tokens: int = 3000
    timeout: int = 300


@dataclass
class NovelConfig:
    """小说配置"""
    title: str = "星辰之下"
    genre: str = "都市玄幻"
    style: str = "轻松幽默、节奏紧凑"
    target_words_per_chapter: int = 2000
    chapters_per_day: int = 1
    outline: str = ""
    world_setting: str = ""
    main_characters: str = ""


@dataclass
class WeChatConfig:
    """微信公众号配置"""
    enabled: bool = True
    app_id: str = ""
    app_secret: str = ""
    use_draft: bool = True
    thumb_media_id: str = ""
    cover_image: str = ""
    author: str = ""


@dataclass
class ScheduleConfig:
    """调度配置"""
    run_time: str = "08:00"
    run_window: str = ""
    timezone: str = "Asia/Shanghai"
    test_mode: bool = False


@dataclass
class AppConfig:
    ai: AIConfig = field(default_factory=AIConfig)
    novel: NovelConfig = field(default_factory=NovelConfig)
    wechat: WeChatConfig = field(default_factory=WeChatConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    @classmethod
    def load(cls, yaml_path: str | Path | None = None,
             username: str | None = None) -> "AppConfig":
        """
        加载配置
          yaml_path: 直接指定 yaml 文件路径
          username:  按用户加载 → 用 users/{username}/config.yaml
                     两者都没给 → 用 PROJECT_ROOT/config.yaml（系统默认）
        """
        cfg = cls()

        if username:
            # 🔒 admin 特殊：永远用根目录 config.yaml（删 users/ 也不会丢 admin 数据）
            if username == "admin":
                path = PROJECT_ROOT / "config.yaml"
            else:
                # 普通用户走独立目录
                path = ensure_user_initialized(username)
        elif yaml_path:
            path = Path(yaml_path)
        else:
            path = PROJECT_ROOT / "config.yaml"

        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f) or {}

            if "ai" in raw:
                for k, v in raw["ai"].items():
                    if hasattr(cfg.ai, k):
                        setattr(cfg.ai, k, v)
            if "novel" in raw:
                for k, v in raw["novel"].items():
                    if hasattr(cfg.novel, k):
                        setattr(cfg.novel, k, v)
            if "wechat" in raw:
                for k, v in raw["wechat"].items():
                    if hasattr(cfg.wechat, k):
                        setattr(cfg.wechat, k, v)
            if "schedule" in raw:
                for k, v in raw["schedule"].items():
                    if hasattr(cfg.schedule, k):
                        setattr(cfg.schedule, k, v)

        # 环境变量覆盖（最高优先级）
        cfg.ai.api_key = os.getenv("AI_API_KEY", cfg.ai.api_key)
        cfg.ai.base_url = os.getenv("AI_BASE_URL", cfg.ai.base_url)
        cfg.ai.model = os.getenv("AI_MODEL", cfg.ai.model)
        cfg.wechat.app_id = os.getenv("WECHAT_APP_ID", cfg.wechat.app_id)
        cfg.wechat.app_secret = os.getenv("WECHAT_APP_SECRET", cfg.wechat.app_secret)

        return cfg

    def get_data_dir(self, username: str | None = None) -> Path:
        """获取当前配置对应的 data 目录（自动创建）"""
        if username == "admin":
            # 🔒 admin 永远走根目录——防止删 users/ 时丢 admin 数据
            d = PROJECT_ROOT / "data"
        elif username:
            d = get_user_data_dir(username)
        else:
            d = PROJECT_ROOT / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_output_dir(self, username: str | None = None) -> Path:
        """获取当前配置对应的 output 目录"""
        if username == "admin":
            o = PROJECT_ROOT / "output"
        elif username:
            o = get_user_output_dir(username)
        else:
            o = PROJECT_ROOT / "output"
        o.mkdir(parents=True, exist_ok=True)
        return o

    def get_config_path(self, username: str | None = None) -> Path:
        """获取当前配置对应的 config.yaml 路径"""
        if username == "admin":
            return PROJECT_ROOT / "config.yaml"
        if username:
            return get_user_config_path(username)
        return PROJECT_ROOT / "config.yaml"
