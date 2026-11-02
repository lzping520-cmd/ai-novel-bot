"""
AI 写小说机器人 · Streamlit 控制台
v4 最终版：多用户隔离 + admin 安全 + session_state 正确清理 + 完整 widget key
启动：python -m streamlit run ai_novel_bot/ui.py --server.port 8501 --server.address 0.0.0.0
"""
from __future__ import annotations
import sys, json, hashlib, re, os
from pathlib import Path
from datetime import datetime

import streamlit as st
import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_novel_bot.config import (
    AppConfig, PROJECT_ROOT, USERS_ROOT,
    get_user_dir, get_user_config_path, get_user_data_dir, get_user_output_dir,
    ensure_user_initialized,
)
from ai_novel_bot.storage.state import StateStore
from ai_novel_bot.generators import OpenAICompatGenerator
from ai_novel_bot.publishers import WeChatPublisher

st.set_page_config(page_title="AI 小说控制台", page_icon="📚", layout="wide")

# ============================================================
# 🔐 用户数据库（生产中建议用 SQLite）
# ============================================================
AUTH_DB = {
    "admin":    (hashlib.md5("121379".encode()).hexdigest(),  "admin",    999, 99),
    "NN": (hashlib.md5("123456".encode()).hexdigest(),     "basic",      1,  1),
    "lgh": (hashlib.md5("123456".encode()).hexdigest(),     "pro",        3,  3),
}
PLAN_INFO = {
    "admin":     {"name": "管理员", "max_novels": 999, "max_daily": 99},
    "basic":     {"name": "基础版", "max_novels":   1, "max_daily":  1},
    "pro":       {"name": "进阶版", "max_novels":   3, "max_daily":  3},
    "unlimited": {"name": "买断版", "max_novels": 999, "max_daily": 99},
}


# ============================================================
# 🧹 session_state 工具：换用户时彻底清缓存
# ============================================================
def _clear_session_state_except(*keep_keys):
    """清掉 session_state，只保留指定 key（用于退出/换用户）"""
    for key in list(st.session_state.keys()):
        if key not in keep_keys:
            del st.session_state[key]


# ============================================================
# 🛡️ 登录页
# ============================================================
if "username" not in st.session_state:
    st.title("🔐 AI 写小说机器人")
    st.caption("🔒 付费版 · 数据完全隔离 · 每日自动生成微信公众号草稿")
    with st.form("login_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        u = c1.text_input("用户名", key="login_user_input")
        p = c2.text_input("密码", type="password", key="login_pass_input")
        if st.form_submit_button("🚀 登录", use_container_width=True):
            if u in AUTH_DB and AUTH_DB[u][0] == hashlib.md5(p.encode()).hexdigest():
                st.session_state.username = u
                st.session_state.plan = AUTH_DB[u][1]
                st.success(f"✅ 欢迎回来，{u}！")
                st.rerun()
            else:
                st.error("❌ 用户名或密码错误")
    st.divider()
    st.caption("💡 闲鱼购买后获取账号密码。测试账号：admin / admin888")
    st.stop()


# ============================================================
# 📁 用户专属路径（admin 特殊：用根目录，不怕删 users/）
# ============================================================
USERNAME = st.session_state.username
PLAN = st.session_state.plan if "plan" in st.session_state else AUTH_DB[USERNAME][1]
IS_ADMIN = (USERNAME == "admin")
PLAN_NAME = PLAN_INFO.get(PLAN, PLAN_INFO["basic"])["name"]

if IS_ADMIN:
    USER_DIR = PROJECT_ROOT
    CFG_PATH = PROJECT_ROOT / "config.yaml"
    DATA_DIR = PROJECT_ROOT / "data"
    OUTPUT_DIR = PROJECT_ROOT / "output"
else:
    ensure_user_initialized(USERNAME)
    USER_DIR = get_user_dir(USERNAME)
    CFG_PATH = get_user_config_path(USERNAME)
    DATA_DIR = get_user_data_dir(USERNAME)
    OUTPUT_DIR = get_user_output_dir(USERNAME)

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 🛠️ 工具
# ============================================================
def _reload():
    st.session_state.cfg = AppConfig.load(CFG_PATH)
    st.session_state.store = StateStore(DATA_DIR / f"{st.session_state.cfg.novel.title}_state.json")
    st.session_state.state = st.session_state.store.load()
    st.session_state._loaded_for_user = USERNAME  # 标记：这份缓存属于谁

def _is_cache_stale():
    """缓存属于其他用户？强制清掉"""
    cached_user = st.session_state.get("_loaded_for_user")
    return cached_user != USERNAME

# 缓存失效检查
if st.session_state.get("cfg") is not None and _is_cache_stale():
    _clear_session_state_except("username", "plan")
    st.rerun()

if st.session_state.get("cfg") is None:
    _reload()

def _switch_novel(new_title: str) -> bool:
    existing = _list_my_novels()
    pi = PLAN_INFO.get(PLAN, PLAN_INFO["basic"])
    if new_title not in existing and len(existing) >= pi["max_novels"]:
        st.error(f"❌ 最多 {pi['max_novels']} 本小说，先删一本或升级套餐")
        return False
    raw = CFG_PATH.read_text(encoding="utf-8")
    new_raw = re.sub(r'(?m)^  title: .*$', f'  title: "{new_title}"', raw)
    CFG_PATH.write_text(new_raw, encoding="utf-8")
    _reload()
    return True

def _list_my_novels() -> list[str]:
    if not DATA_DIR.exists():
        return []
    titles = []
    for f in DATA_DIR.glob("*_state.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            titles.append(d.get("novel_title") or f.stem.replace("_state", ""))
        except Exception:
            titles.append(f.stem)
    return sorted(titles)

def _delete_my_novel(title: str):
    sf = DATA_DIR / f"{title}_state.json"
    if sf.exists():
        sf.unlink()
        for md in OUTPUT_DIR.glob(f"{title}_chapter_*.md"):
            md.unlink()


# ============================================================
# 📋 侧边栏
# ============================================================
with st.sidebar:
    st.header(f"📚 AI 写小说 · {PLAN_NAME}")
    st.caption(f"👤 用户: **{USERNAME}** {'🛡️ admin模式' if IS_ADMIN else ''}")
    st.caption(f"📁 你的数据目录: `{USER_DIR}`")

    pi = PLAN_INFO.get(PLAN, PLAN_INFO["basic"])
    if pi["max_novels"] < 999:
        st.info(f"💡 限制：{pi['max_novels']}本小说 · 每日最多{pi['max_daily']}章")

    st.divider()
    st.subheader("📖 我的小说")
    current_title = st.session_state.cfg.novel.title
    my_novels = _list_my_novels()

    if not my_novels:
        st.caption("（还没有小说）")

    for t in my_novels:
        is_cur = "（当前）" if t == current_title else ""
        if st.button(f"📖 {t}{is_cur}", key=f"sw_{USERNAME}_{t}"):
            _switch_novel(t)
            st.rerun()
        if len(my_novels) > 1 and t != current_title:
            if st.button("🗑️", key=f"del_{USERNAME}_{t}"):
                _delete_my_novel(t)
                st.rerun()

    st.divider()
    st.caption(f"➕ 新建（剩余: {max(0, pi['max_novels'] - len(my_novels))}）")
    new_title = st.text_input("新标题", key="side_new_title_input")
    if st.button("🚀 创建新小说", key="side_create_btn", use_container_width=True):
        if not new_title.strip():
            st.warning("请输入标题")
        elif _switch_novel(new_title.strip()):
            st.rerun()

    st.divider()
    st.subheader("🧠 AI 状态")
    try:
        g = OpenAICompatGenerator(st.session_state.cfg.ai, st.session_state.cfg.novel)
        ok, msg = g.test_connection()
        if ok:
            st.success(f"✅ AI 连通\n{st.session_state.cfg.ai.provider}/{st.session_state.cfg.ai.model}")
        else:
            st.error("❌ AI 不通")
    except Exception as e:
        st.error(f"❌ 配置有误: {e}")

    st.divider()
    col_refresh, col_logout = st.columns(2)
    if col_refresh.button("🔄 刷新", key="side_refresh_btn"):
        _reload()
        st.rerun()
    if col_logout.button("👤 退出登录", key="side_logout_btn"):
        _clear_session_state_except()  # 清所有
        st.rerun()


# ============================================================
# 📊 主面板
# ============================================================
tab_overview, tab_generate, tab_publish, tab_config = st.tabs([
    "📊 总览", "✍️ 生成/重写", "📤 发布", "⚙️ 配置"
])


# ============ Tab 1: 总览 ============
with tab_overview:
    state = st.session_state.state
    chapters = state.chapters
    active = [c for c in chapters if not c.discarded]
    discarded = [c for c in chapters if c.discarded]
    published = [c for c in active if c.published]
    pending = [c for c in active if not c.published]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📚 总章节", len(active))
    c2.metric("✅ 已发布", len(published))
    c3.metric("⏳ 待发布", len(pending))
    c4.metric("🗑️ 已废弃", len(discarded))
    c5.metric("▶️ 下一章", st.session_state.store.find_next_chapter_no())

    if state.setting_seed:
        st.info(f"🧊 设定固化已激活（{len(state.setting_seed)}字）")

    st.divider()
    st.subheader(f"📜 《{st.session_state.cfg.novel.title}》")
    st.caption(f"数据位置: `{DATA_DIR}` · 仅 {USERNAME} 可见")

    if not chapters:
        st.warning("还没有章节 → 去「生成/重写」tab 点 🚀")
    else:
        for c in sorted(chapters, key=lambda x: x.chapter_no, reverse=True):
            if c.discarded:
                badge, color = "🗑️ 已废弃", "red"
            elif c.published:
                badge, color = "✅ 已发", "green"
            else:
                badge, color = "⏳ 待发", "orange"

            title_prefix = f"(重写×{c.rewrite_count}) " if c.rewrite_count > 0 else ""
            exp_key = f"exp_{USERNAME}_{c.chapter_no}"
            with st.expander(f"第{c.chapter_no}章 · 《{title_prefix}{c.title}》 · {c.word_count}字 · :{color}[{badge}]"):
                if c.discarded:
                    st.warning(f"🗑️ 原因: {c.discard_reason}")
                st.markdown(f"**摘要**: {c.summary or '(无)'}")
                st.markdown(f"**生成时间**: {c.generated_at}")

                col_a, col_b = st.columns(2)
                if c.discarded:
                    if col_a.button("↩️ 取消废弃", key=f"undisc_{USERNAME}_{c.chapter_no}"):
                        st.session_state.store.undiscard_chapter(c.chapter_no)
                        st.rerun()
                else:
                    if not c.published:
                        if col_a.button("✅ 手动标记已发布", key=f"pub_{USERNAME}_{c.chapter_no}"):
                            st.session_state.store.mark_published(c.chapter_no, "MANUAL")
                            st.rerun()
                    else:
                        if col_a.button("↩️ 取消已发布", key=f"unpub_{USERNAME}_{c.chapter_no}"):
                            for cc in st.session_state.store.load().chapters:
                                if cc.chapter_no == c.chapter_no:
                                    cc.published = False
                            st.session_state.store.save()
                            st.rerun()
                    if col_b.button("🗑️ 废弃 + 重写", key=f"disc_{USERNAME}_{c.chapter_no}"):
                        st.session_state.store.discard_chapter(c.chapter_no, "UI 手动标记")
                        st.rerun()

                # ---- 章节内容：默认预览 + 展开全文 ----
                full_key = f"show_full_{USERNAME}_{c.chapter_no}"
                preview_limit = 500

                if len(c.content) > preview_limit:
                    preview = c.content[:preview_limit] + "\n\n...（点击下方按钮展开全文）"
                    st.code(preview)

                    col_view, col_copy = st.columns([1, 1])
                    if col_view.button("📖 展开全文", key=f"view_{USERNAME}_{c.chapter_no}"):
                        st.session_state[full_key] = not st.session_state.get(full_key, False)

                    if st.session_state.get(full_key, False):
                        st.text_area(
                            f"📄 第{c.chapter_no}章 全文（{c.word_count}字）",
                            c.content,
                            height=500,
                            key=f"full_text_{USERNAME}_{c.chapter_no}",
                        )

                    if col_copy.button("📋 复制全文到剪贴板", key=f"copy_{USERNAME}_{c.chapter_no}"):
                        try:
                            import pyperclip
                            pyperclip.copy(c.content)
                            st.success("✅ 已复制到剪贴板")
                        except ImportError:
                            st.code(c.content)
                            st.caption("⚠️ 未安装 pyperclip，上面是完整内容，手动全选复制吧")
                else:
                    # 内容本身就不长，直接显示
                    st.code(c.content)


# ============ Tab 2: 生成 ============
with tab_generate:
    st.subheader("✍️ 生成下一章 / 重写废弃章节")
    next_no = st.session_state.store.find_next_chapter_no()
    discarded_list = [c for c in st.session_state.state.chapters if c.discarded]
    pending_list = [c for c in st.session_state.state.chapters if not c.published and not c.discarded]

    if discarded_list:
        st.warning(f"🗑️ 有 {len(discarded_list)} 章已废弃，将重写第 {min(c.chapter_no for c in discarded_list)} 章")
    if pending_list:
        st.info(f"⏳ 还有 {len(pending_list)} 章未发布，不会生成新章节")

    col1, col2, col3 = st.columns(3)
    run_btn = col1.button("🚀 生成 / 重写", type="primary", use_container_width=True, key="gen_run_btn")
    test_btn = col2.button("🧪 只本地预览", use_container_width=True, key="gen_test_btn")
    force_no = col3.number_input("🔧 强制重写指定章节号", min_value=1, max_value=9999, value=next_no, key="gen_force_no_input")
    force_btn = col3.button("⚠️ 强制重写", use_container_width=True, key="gen_force_btn")

    if run_btn or test_btn or force_btn:
        with st.spinner("AI 写作中...（2-3 分钟）"):
            try:
                if test_btn:
                    cfg = AppConfig.load(CFG_PATH)
                    cfg.schedule.test_mode = True
                    CFG_PATH.write_text(
                        open(CFG_PATH, encoding="utf-8").read().replace("test_mode: false", "test_mode: true"),
                        encoding="utf-8"
                    )
                from ai_novel_bot.main import run_once

                # ✅ 关键：必须传 USERNAME，否则 run_once 会存到 admin 的数据目录！
                target_no = force_no if force_btn else None
                result = run_once(
                    config_path=str(CFG_PATH) if IS_ADMIN else None,
                    force_rewrite_chapter_no=target_no,
                    username=USERNAME,
                )

                st.success(f"✅ 完成: {result}")
                _reload()
                st.rerun()
            except Exception as e:
                st.error(f"❌ 失败: {e}")
                import traceback
                st.code(traceback.format_exc())

    st.divider()
    st.subheader("🔄 批量同步手动发布")
    st.caption("在公众号小说入口手动发了某几章，一次性同步给代码")

    max_pub = max((c.chapter_no for c in st.session_state.state.chapters if c.published), default=0)
    mark_no = st.number_input("已手动发布到第几章", min_value=0, max_value=9999, value=max_pub, key="gen_mark_no_input")
    if st.button(f"✅ 把 1-{mark_no} 全部标记为已发布", key="gen_mark_btn"):
        store = st.session_state.store
        synced = 0
        for c in store.state.chapters:
            if c.chapter_no <= int(mark_no) and not c.discarded and not c.published:
                c.published = True
                if not c.publish_url:
                    c.publish_url = "MANUAL_VIA_NOVEL_ENTRY"
                synced += 1
        store.save()
        st.success(f"✅ 已同步 {synced} 章")
        _reload()
        st.rerun()


# ============ Tab 3: 发布 ============
with tab_publish:
    st.subheader("📤 微信公众号发布")
    cfg = st.session_state.cfg
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("启用", "✅" if cfg.wechat.enabled else "❌")
    c2.metric("AppID", f"{cfg.wechat.app_id[:12]}..." if cfg.wechat.app_id else "未配置")
    c3.metric("Secret", f"{cfg.wechat.app_secret[:8]}..." if cfg.wechat.app_secret else "未配置")
    c4.metric("封面ID", f"{cfg.wechat.thumb_media_id[:10]}..." if cfg.wechat.thumb_media_id else "未配置")

    st.divider()
    st.subheader("⚠️ 发布方式说明")
    st.info("""
    | 方式 | API 支持 | 说明 |
    |---|---|---|
    | **自动创建草稿箱** | ✅ 所有公众号 | AI 存草稿箱 |
    | **自动群发发布** | ❌ 只有认证服务号 | 95% 用户无此权限 |
    | **原创声明** | ❌ API 不支持 | 需手动去公众号「小说入口」发布 |
    """)

    if st.button("🔁 测试微信连通性", key="pub_test_btn"):
        try:
            pub = WeChatPublisher(cfg.wechat)
            token = pub._get_token()
            st.success(f"✅ access_token: {token[:20]}...")
        except Exception as e:
            st.error(f"❌ 微信 API 不通: {e}")


# ============ Tab 4: 配置 ============
with tab_config:
    st.subheader(f"⚙️ 配置（保存到 `{CFG_PATH}`）")
    st.caption(f"💡 {'admin 可改全部字段' if IS_ADMIN else '非 admin：AI 模型由管理员统一配置，你只需填自己的 Key / 微信 / 小说设定'}")

    RO = "🔒 仅 admin 可改" if not IS_ADMIN else "✅ 可编辑"

    if st.session_state.get("cfg_form") is None or st.session_state.get("_cfg_form_user") != USERNAME:
        c = st.session_state.cfg
        st.session_state.cfg_form = {
            "ai_provider": c.ai.provider,
            "ai_base_url": c.ai.base_url,
            "ai_model": c.ai.model,
            "ai_api_key": c.ai.api_key,
            "ai_temperature": c.ai.temperature,
            "ai_max_tokens": c.ai.max_tokens,
            "ai_timeout": getattr(c.ai, "timeout", 300),
            "novel_title": c.novel.title,
            "novel_genre": c.novel.genre,
            "novel_style": c.novel.style,
            "novel_words": c.novel.target_words_per_chapter,
            "novel_world": c.novel.world_setting,
            "novel_chars": c.novel.main_characters,
            "novel_outline": c.novel.outline,
            "wx_enabled": c.wechat.enabled,
            "wx_app_id": c.wechat.app_id,
            "wx_app_secret": c.wechat.app_secret,
            "wx_thumb_media_id": getattr(c.wechat, "thumb_media_id", ""),
            "wx_author": getattr(c.wechat, "author", ""),
            "sched_mode": "window" if getattr(c.schedule, "run_window", "") else "time",
            "sched_time": c.schedule.run_time,
            "sched_window": getattr(c.schedule, "run_window", "") or "08:00-09:30",
            "sched_test": c.schedule.test_mode,
        }
        st.session_state._cfg_form_user = USERNAME

    form = st.session_state.cfg_form

    # AI 模型
    with st.expander("🧠 AI 模型", expanded=True):
        col1, col2, col3 = st.columns(3)
        if IS_ADMIN:
            form["ai_provider"] = col1.selectbox("provider",
                ["doubao","qwen","deepseek","zhipu","moonshot","openai","custom"],
                index=["doubao","qwen","deepseek","zhipu","moonshot","openai","custom"].index(form["ai_provider"])
                    if form["ai_provider"] in ["doubao","qwen","deepseek","zhipu","moonshot","openai","custom"] else 0,
                key=f"cfg_ai_provider_{USERNAME}")
            form["ai_base_url"] = col2.text_input(f"base_url {RO}", form["ai_base_url"], key=f"cfg_ai_base_{USERNAME}")
            form["ai_model"] = col3.text_input(f"model {RO}", form["ai_model"], key=f"cfg_ai_model_{USERNAME}")
        else:
            col1.info(f"provider: **{form['ai_provider']}** {RO}")
            col2.info(f"base_url: `{form['ai_base_url']}` {RO}")
            col3.info(f"model: **{form['ai_model']}** {RO}")

        form["ai_api_key"] = st.text_input(
            "api_key（用你自己的 AI 平台 Key）", form["ai_api_key"], type="password",
            key=f"cfg_ai_key_{USERNAME}",
            help="去豆包/DeepSeek/千问等平台注册获取。管理员已统一配置模型和中转地址。"
        )

        col1, col2, col3 = st.columns(3)
        if IS_ADMIN:
            form["ai_temperature"] = col1.slider(f"temperature {RO}", 0.0, 1.5, form["ai_temperature"], 0.05,
                                                  key=f"cfg_ai_temp_{USERNAME}")
            form["ai_max_tokens"] = col2.number_input(f"max_tokens {RO}", 500, 8000, form["ai_max_tokens"], 100,
                                                       key=f"cfg_ai_maxtok_{USERNAME}")
            form["ai_timeout"] = col3.number_input(f"timeout（秒）{RO}", 30, 1800, form["ai_timeout"], 30,
                                                     key=f"cfg_ai_timeout_{USERNAME}")
        else:
            st.caption(f"temperature={form['ai_temperature']}  max_tokens={form['ai_max_tokens']}  timeout={form['ai_timeout']}  · {RO}")

    # 小说设定
    with st.expander("📖 小说设定（没思绪？让AI帮你！）", expanded=True):
        col1, col2 = st.columns(2)
        form["novel_title"] = col1.text_input("标题", form["novel_title"], key=f"cfg_novel_title_{USERNAME}")
        form["novel_genre"] = col2.text_input("类型（玄幻/都市/悬疑/言情...）", form["novel_genre"], key=f"cfg_novel_genre_{USERNAME}")
        form["novel_style"] = st.text_input("风格", form["novel_style"], key=f"cfg_novel_style_{USERNAME}")
        form["novel_words"] = st.number_input("每章目标字数", 500, 10000, form["novel_words"], 100, key=f"cfg_novel_words_{USERNAME}")

        # ===== AI 一键生成设定 =====
        gen_col1, gen_col2 = st.columns([3, 1])
        gen_btn = gen_col1.button(
            "✨ AI 帮我生成世界观 + 人物 + 大纲",
            key=f"cfg_ai_gen_{USERNAME}",
            use_container_width=True,
        )
        regenerate_flag = gen_col2.button(
            "🔁 不满意再来",
            key=f"cfg_ai_regen_{USERNAME}",
            use_container_width=True,
        )

        if gen_btn or regenerate_flag:
            # 前置校验
            t = (form.get("novel_title") or "").strip()
            g = (form.get("novel_genre") or "").strip()
            if not t:
                st.error("❌ 请先填写「标题」，AI 需要知道写什么故事")
            elif not g:
                st.error("❌ 请先填写「类型」，比如玄幻、都市、悬疑、言情...")
            else:
                with st.spinner("🧠 AI 构思中...（约 10-30 秒）"):
                    try:
                        # 从 session_state 拿最新配置来构造 generator（不用 cfg_form 的旧缓存）
                        _cfg = st.session_state.cfg
                        gen = OpenAICompatGenerator(_cfg.ai, _cfg.novel)

                        prompt = f"""你是一位资深网文策划编辑。请根据以下小说雏形，生成一份**吸引人、有市场潜力**的完整设定。

📌 标题：{t}
📌 类型：{g}
📌 风格：{form.get('novel_style') or '节奏紧凑、悬念密集'}

---
请严格按以下格式输出（用【】标签分隔三个部分，**不要输出任何额外解释**）：

【世界观】
（100-200字，要有独特设定/金手指规则/势力格局，让读者一看就想追）

【人物】
主角：姓名 + 年龄/身份 + 核心动机 + 金手指/特殊能力
女主角/重要配角：姓名 + 与主角的关系 + 性格
主要对手/反派：（可选）姓名 + 威胁点

【大纲】
第一卷 · 卷名（20-30字概述）
第二卷 · 卷名（20-30字概述）
第三卷 · 卷名（20-30字概述）
第四卷 · 卷名（20-30字概述）
（3-5卷，每卷标注一个核心转折点）

⚠️ 要求：
- 世界观要有**独特点**，不要老套
- 主角要有**明确的欲望/创伤/目标**
- 每卷要有**钩子**，让读者想看下一卷
- 语言生动，有画面感"""

                        raw = gen._chat([
                            {"role": "system", "content": "你是专业的网文策划编辑，擅长设计世界观、塑造有魅力的角色、编织有悬念的大纲。输出严格按【世界观】【人物】【大纲】三段格式。"},
                            {"role": "user", "content": prompt},
                        ])

                        # ---- 解析 AI 输出 ----
                        world = chars = outline = ""
                        import re as _re
                        # 匹配 【世界观】xxx【人物】xxx【大纲】xxx 或 Markdown ### 世界观 等变体
                        _pattern = _re.compile(
                            r'[【\[#]*\s*(世界观|人物|大纲)[】\]#]*\s*\n?(.*?)(?=[【\[#]*\s*(世界观|人物|大纲)[】\]#]*|\Z)',
                            _re.DOTALL
                        )
                        for m in _pattern.finditer(raw):
                            section = m.group(1)
                            content = m.group(2).strip()
                            if section in ("世界观", "世界观"):
                                world = content
                            elif section in ("人物", "角色"):
                                chars = content
                            elif section in ("大纲", "剧情"):
                                outline = content

                        # 如果正则只匹配到部分，fallback：按换行 + 关键词切
                        if not (world and chars and outline):
                            st.warning("⚠️ AI 输出格式略有不同，正在尝试兜底解析...")
                            # 简单 fallback：按关键词位置切
                            for label, store in [("世界观", "world"), ("人物", "chars"), ("大纲", "outline")]:
                                idx = raw.find(label)
                                if idx >= 0:
                                    next_labels = [raw.find(l, idx + len(label)) for l in ["人物", "大纲"]]
                                    next_idx = min([n for n in next_labels if n > 0], default=len(raw))
                                    chunk = raw[idx + len(label):next_idx].strip()
                                    # 去掉开头的 【】[] 等标点
                                    chunk = _re.sub(r'^[】\]\s:：\-—]+', '', chunk).strip()
                                    if store == "world" and not world:
                                        world = chunk
                                    elif store == "chars" and not chars:
                                        chars = chunk
                                    elif store == "outline" and not outline:
                                        outline = chunk

                        # 如果还是全空，直接把 raw 放进世界观让用户手动分
                        if not (world or chars or outline):
                            world = f"（AI 输出未按标准格式，原文如下，请手动分割到三个框）\n\n{raw}"

                        # 写入 form 字典
                        form["novel_world"] = world
                        form["novel_chars"] = chars
                        form["novel_outline"] = outline

                        # ⚠️ Streamlit 关键补丁：text_area 已有 session_state 值时会忽略新的 value 参数
                        # 必须手动把 AI 生成值写入 widget key 对应的 session_state，否则 rerun 后仍是旧值
                        st.session_state[f"cfg_novel_world_{USERNAME}"] = world
                        st.session_state[f"cfg_novel_chars_{USERNAME}"] = chars
                        st.session_state[f"cfg_novel_outline_{USERNAME}"] = outline

                        st.success("✅ AI 生成完毕！下方三个框已自动填入，满意就点💾保存，不满意点「🔁 不满意再来」")
                        st.rerun()

                    except Exception as e:
                        err = str(e)
                        # 友好提示不同错误
                        if "401" in err or "Unauthorized" in err:
                            st.error("❌ API Key 错误或未填写。请在上方「🧠 AI 模型」填好 api_key")
                        elif "404" in err:
                            st.error("❌ model 名或 base_url 错误。请确认 AI 配置")
                        elif "timed out" in err or "Timeout" in err:
                            st.error("❌ AI 响应超时了，重试一次？（如果频繁超时可以把 timeout 调大）")
                        elif "ConnectionError" in err or "无法连接" in err:
                            st.error("❌ 连不上 AI 接口，检查网络 / base_url 是否可达")
                        else:
                            st.error(f"❌ AI 生成失败: {err}")

        st.divider()
        form["novel_world"] = st.text_area("🌍 世界观", form["novel_world"], height=200, key=f"cfg_novel_world_{USERNAME}")
        form["novel_chars"] = st.text_area("👥 人物", form["novel_chars"], height=200, key=f"cfg_novel_chars_{USERNAME}")
        form["novel_outline"] = st.text_area("🗺️ 大纲", form["novel_outline"], height=250, key=f"cfg_novel_outline_{USERNAME}")

    # 微信
    with st.expander("📤 微信公众号", expanded=False):
        form["wx_enabled"] = st.checkbox("启用微信发布", form["wx_enabled"], key=f"cfg_wx_enabled_{USERNAME}")
        col1, col2 = st.columns(2)
        form["wx_app_id"] = col1.text_input("AppID", form["wx_app_id"], key=f"cfg_wx_appid_{USERNAME}")
        form["wx_app_secret"] = col2.text_input("AppSecret", form["wx_app_secret"], type="password", key=f"cfg_wx_secret_{USERNAME}")
        col3, col4 = st.columns(2)
        form["wx_thumb_media_id"] = col3.text_input("封面 thumb_media_id", form["wx_thumb_media_id"], key=f"cfg_wx_thumb_{USERNAME}")
        form["wx_author"] = col4.text_input("作者名", form["wx_author"], key=f"cfg_wx_author_{USERNAME}")

        with st.expander("📖 图文教程：这些值怎么填", expanded=False):
            st.markdown("""
            **① AppID + AppSecret**
            1. 打开 [mp.weixin.qq.com](https://mp.weixin.qq.com) 登录你的公众号
            2. 左侧 → **设置与开发** → **基本配置**
            3. AppID 直接复制；AppSecret 点「重置」扫码验证后显示（**只显示一次！赶紧复制**）
            4. 下方 **IP 白名单** 填上服务器公网 IP（查你当前 IP：https://ip138.com）

            **② 封面 thumb_media_id**
            左侧 → **素材管理** → **图片** → 上传封面图（正方形 1:1，≤2MB）→ 鼠标悬停图片 → 点「详细」→ 复制 media_id

            **③ 作者名**（可选）随便填，显示在文章标题下面

            ### ⚠️ 重要限制
            | 功能 | 谁能用 |
            |---|---|
            | 自动创建草稿箱 | ✅ 所有公众号 |
            | 自动群发发布 | ❌ 只有认证服务号 |
            | 原创声明开关 | ❌ API 草稿没有，需手动去公众号「小说入口」发布 |
            """)

    # 定时调度
    with st.expander("⏰ 定时调度", expanded=False):
        form["sched_mode"] = st.radio("模式", ["固定时间", "随机窗口"],
            index=0 if form["sched_mode"] == "time" else 1, horizontal=True,
            key=f"cfg_sched_mode_{USERNAME}")
        col1, col2 = st.columns(2)
        if form["sched_mode"] == "固定时间":
            form["sched_time"] = col1.text_input("每日 HH:MM", form["sched_time"], key=f"cfg_sched_time_{USERNAME}")
            form["sched_window"] = ""
        else:
            form["sched_window"] = col1.text_input("窗口 HH:MM-HH:MM", form["sched_window"], key=f"cfg_sched_window_{USERNAME}")
            form["sched_time"] = ""
        form["sched_test"] = st.checkbox("测试模式（不推微信）", form["sched_test"], key=f"cfg_sched_test_{USERNAME}")

    # 预览 + 保存
    st.divider()
    preview_yaml = f"""# 配置（用户: {USERNAME}）
ai:
  provider: {form['ai_provider']}
  base_url: "{form['ai_base_url']}"
  model: {form['ai_model']}
  api_key: "{form['ai_api_key']}"
  temperature: {form['ai_temperature']}
  max_tokens: {form['ai_max_tokens']}
  timeout: {form['ai_timeout']}
novel:
  title: "{form['novel_title']}"
  genre: "{form['novel_genre']}"
  style: "{form['novel_style']}"
  target_words_per_chapter: {form['novel_words']}
  chapters_per_day: 1
  world_setting: |
{chr(10).join('    ' + l for l in form['novel_world'].splitlines())}
  main_characters: |
{chr(10).join('    ' + l for l in form['novel_chars'].splitlines())}
  outline: |
{chr(10).join('    ' + l for l in form['novel_outline'].splitlines())}
wechat:
  enabled: {'true' if form['wx_enabled'] else 'false'}
  app_id: "{form['wx_app_id']}"
  app_secret: "{form['wx_app_secret']}"
  thumb_media_id: "{form['wx_thumb_media_id']}"
  author: "{form['wx_author']}"
  use_draft: true
schedule:
  run_time: "{form['sched_time']}"
  run_window: "{form['sched_window']}"
  test_mode: {'true' if form['sched_test'] else 'false'}
"""
    st.code(preview_yaml, language="yaml")

    col_save, col_test = st.columns(2)
    if col_save.button("💾 保存", type="primary", key=f"cfg_save_{USERNAME}"):
        try:
            CFG_PATH.write_text(preview_yaml, encoding="utf-8")
            st.session_state.cfg_form = None
            _reload()
            st.success(f"✅ 已保存到 `{CFG_PATH}`")
            st.rerun()
        except Exception as e:
            st.error(f"保存失败: {e}")
    if col_test.button("🧪 测 AI 连通性", key=f"cfg_test_ai_{USERNAME}"):
        try:
            cfg = AppConfig.load(CFG_PATH)
            g = OpenAICompatGenerator(cfg.ai, cfg.novel)
            ok, msg = g.test_connection()
            st.success(msg) if ok else st.error(msg)
        except Exception as e:
            st.error(str(e))
