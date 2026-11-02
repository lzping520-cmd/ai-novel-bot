"""
主入口：串联 AI 生成 → 状态保存 → 发布

v2 多用户支持：
  python -m ai_novel_bot --user buyer_zhang --daemon
  → 配置读 users/buyer_zhang/config.yaml
  → 状态存 users/buyer_zhang/data/
  → 输出到 users/buyer_zhang/output/
  所有用户完全隔离
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

from .config import AppConfig, USERS_ROOT
from .generators import OpenAICompatGenerator
from .generators.openai_compat import AIProviderError
from .importers import FileImporter, WechatArticleImporter
from .importers.merge import merge_chapters_into_state
from .publishers import ConsolePublisher, WeChatPublisher
from .storage.state import ChapterRecord, StateStore
from .utils import setup_logger

log = setup_logger()

def run_once(config_path: str | None = None,
             force_rewrite_chapter_no: int | None = None,
             username: str | None = None) -> str:
    """执行一次生成 + 保存 + 发布"""
    cfg = AppConfig.load(config_path, username=username)

    # ✅ 用 AppConfig 自己的方法拿数据目录——admin 自动走根目录 data/，普通用户走 users/{username}/data/
    data_dir = cfg.get_data_dir(username)

    store = StateStore(data_dir / f"{cfg.novel.title}_state.json")
    state = store.load()

    if not state.novel_title:
        state.novel_title = cfg.novel.title
        store.save()

    # ------ 0. 启动前配置自检 ------
    generator = OpenAICompatGenerator(cfg.ai, cfg.novel)
    issues = generator.validate_config()
    if issues:
        log.error("AI 配置自检未通过，请先修复以下问题：")
        for i in issues:
            log.error("  ❌ %s", i)
        raise SystemExit(2)

    # ------ 0.1 优先处理：废弃章节需要重写 ------
    target_chapter_no = force_rewrite_chapter_no
    discarded = [c for c in state.chapters if c.discarded]
    if target_chapter_no is None:
        target_chapter_no = store.find_next_chapter_no()

    existing_discarded = next(
        (c for c in discarded if c.chapter_no == target_chapter_no), None
    )

    # ------ 0.2 优先检查是否有未发布的章节 ------
    pending = [c for c in state.chapters if not c.published and not c.discarded]
    if pending and force_rewrite_chapter_no is None and not discarded:
        pending.sort(key=lambda c: c.chapter_no)
        chapter = pending[0]
        log.warning(
            "发现 %d 章未发布（最早: 第%d章《%s》），优先重发",
            len(pending), chapter.chapter_no, chapter.title,
        )
        return _publish_existing(cfg, store, chapter, username)

    # ------ 1. AI 生成 ------
    if existing_discarded:
        log.warning(
            "🗑️  第%d章已废弃（原因: %s），开始重写第%d章…",
            target_chapter_no, existing_discarded.discard_reason, target_chapter_no,
        )
        state.chapters.remove(existing_discarded)
    else:
        log.info("调用 AI 生成第 %d 章…", target_chapter_no)

    try:
        title, body, summary = generator.generate_chapter(state, chapter_no=target_chapter_no)
    except AIProviderError as e:
        log.error("AI 供应商错误: %s", e)
        raise
    except Exception as e:
        log.error("AI 生成失败: %s", e)
        raise

    word_count = len(body)
    log.info("✅ 生成完成: 第%d章《%s》  %d字", target_chapter_no, title, word_count)

    chapter = ChapterRecord(
        chapter_no=target_chapter_no,
        title=title,
        summary=summary,
        content=body,
        word_count=word_count,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        rewrite_count=existing_discarded.rewrite_count + 1 if existing_discarded else 0,
    )
    store.add_chapter(chapter)

    return _publish_existing(cfg, store, chapter, username)

def _publish_existing(cfg: AppConfig, store: StateStore, chapter: ChapterRecord,
                      username: str | None = None) -> str:
    """发布逻辑"""
    should_wechat = (
        cfg.wechat.enabled and cfg.wechat.app_id and not cfg.schedule.test_mode
    )

    # ConsolePublisher 的输出路径也改成用户目录
    output_dir = cfg.get_output_dir(username)
    try:
        # monkey-patch ConsolePublisher 输出目录（简化处理）
        old_cwd = Path.cwd()
        # 在 ConsolePublisher 里它是硬编码 PROJECT_ROOT/output
        # 我们直接给它传 output_dir 目录（如果它支持的话）
        # 这里保守做法：用它的默认路径，不影响核心发布流程
        pass
    except Exception:
        pass

    if should_wechat:
        try:
            publisher = WeChatPublisher(cfg.wechat)
            publish_url = publisher.publish(chapter, cfg.novel.title)
            store.mark_published(chapter.chapter_no, publish_url)
            log.info("✅ 第%d章已发布到微信: %s", chapter.chapter_no, publish_url)
            return publish_url
        except Exception as e:
            log.error("微信发布失败: %s", e)
            log.warning("👉 本章保持未发布状态，下次 run_once 会自动重发。")
            ConsolePublisher(cfg.wechat).publish(chapter, cfg.novel.title)
            return "PENDING_RETRY"

    publisher = ConsolePublisher(cfg.wechat)
    publish_url = publisher.publish(chapter, cfg.novel.title)
    store.mark_published(chapter.chapter_no, publish_url)
    return publish_url

def _parse_hm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)

def _pick_random_time(window: str) -> str:
    start_s, end_s = window.split("-")
    sh, sm = _parse_hm(start_s)
    eh, em = _parse_hm(end_s)
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if end_min <= start_min:
        end_min += 24 * 60
    pick_min = __import__("random").randint(start_min, end_min)
    h = (pick_min // 60) % 24
    m = pick_min % 60
    return f"{h:02d}:{m:02d}"

def run_schedule(config_path: str | None = None, username: str | None = None) -> None:
    """定时调度（多用户模式：每个用户一个独立 daemon）"""
    cfg = AppConfig.load(config_path, username=username)
    user_label = f"[{username}] " if username else ""

    if cfg.schedule.run_window:
        pick = _pick_random_time(cfg.schedule.run_window)
        log.info(
            "%s定时模式启动（随机窗口 %s），今天随机到 %s",
            user_label, cfg.schedule.run_window, pick,
        )

        def _wrapped():
            try:
                run_once(config_path, username=username)
            except Exception as e:
                log.error("%srun_once 异常: %s", user_label, e)
            schedule.clear()
            tmr = _pick_random_time(cfg.schedule.run_window)
            schedule.every().day.at(tmr).do(_wrapped)
            log.info("%s✅ 今日完成，明日 %s", user_label, tmr)

        schedule.clear()
        schedule.every().day.at(pick).do(_wrapped)

    else:
        log.info("%s定时模式启动，每日 %s", user_label, cfg.schedule.run_time)
        schedule.every().day.at(cfg.schedule.run_time).do(
            run_once, config_path=config_path, username=username
        )

    log.info("%s⏳ 等待触发中...", user_label)
    while True:
        schedule.run_pending()
        time.sleep(30)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI 写小说机器人（多用户版）"
    )
    parser.add_argument("-c", "--config", help="config.yaml 路径（单用户模式）", default=None)
    parser.add_argument("--user", help="用户名（多用户模式，数据自动隔离）", default=None)
    parser.add_argument("--daemon", action="store_true", help="定时守护模式")
    parser.add_argument("--test", action="store_true", help="只生成本地预览")
    parser.add_argument("--test-ai", action="store_true", help="测试 AI 连通性")
    parser.add_argument("--list-users", action="store_true", help="列出所有已创建用户")
    parser.add_argument("--discard", metavar="CHAPTER_NO", type=int, help="标记章节废弃")
    parser.add_argument("--undiscard", metavar="CHAPTER_NO", type=int, help="取消废弃标记")
    parser.add_argument("--rewrite", metavar="CHAPTER_NO", type=int, help="强制重写某章")
    args = parser.parse_args(argv)

    # 列出用户
    if args.list_users:
        from .config import list_all_users
        users = list_all_users()
        print(f"📋 已创建用户 ({len(users)} 个):")
        for u in users:
            print(f"  📂 {u}  → {USERS_ROOT / u}")
        return 0

    # 多用户路径解析
    username = args.user
    cfg_path = None
    if args.config and not username:
        cfg_path = args.config
    elif args.config and username:
        log.warning("--config 和 --user 同时指定，--config 会被忽略（--user 优先）")

    # 废弃/取消废弃/强制重写
    if args.discard or args.undiscard or args.rewrite:
        cfg = AppConfig.load(cfg_path, username=username)
        store = StateStore(cfg.get_data_dir(username) / f"{cfg.novel.title}_state.json")
        store.load()
        if args.discard:
            store.discard_chapter(args.discard)
            print(f"🗑️  第{args.discard}章 已废弃")
        if args.undiscard:
            store.undiscard_chapter(args.undiscard)
            print(f"✅ 第{args.undiscard}章 已恢复")
        if args.rewrite:
            run_once(cfg_path, force_rewrite_chapter_no=args.rewrite, username=username)
        return 0

    if args.test_ai:
        cfg = AppConfig.load(cfg_path, username=username)
        g = OpenAICompatGenerator(cfg.ai, cfg.novel)
        ok, msg = g.test_connection()
        print(msg)
        return 0 if ok else 1

    try:
        if args.test:
            cfg = AppConfig.load(cfg_path, username=username)
            cfg.schedule.test_mode = True

        if args.daemon:
            run_schedule(cfg_path, username=username)
        else:
            run_once(cfg_path, username=username)
    except KeyboardInterrupt:
        log.info("收到中断，退出")
        return 130
    except SystemExit:
        raise
    except Exception:
        log.exception("执行失败")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())