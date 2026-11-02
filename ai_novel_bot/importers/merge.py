"""
把导入的章节合并进状态仓库，并自动提炼续写锚点
"""
from __future__ import annotations

import re
from typing import Iterable

from ..storage.state import ChapterRecord, NovelState, StateStore
from ..utils import setup_logger
from .base import Chapter

log = setup_logger()


def _last_paragraph(text: str, min_len: int = 80, max_len: int = 500) -> str:
    """取正文最后一段作为续写锚点"""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return ""
    tail = paragraphs[-1]
    # 如果最后一段太短，往上再拼一段
    if len(tail) < min_len and len(paragraphs) >= 2:
        tail = paragraphs[-2] + "\n" + tail
    return tail[-max_len:]


def _extract_characters(text: str, top_n: int = 5) -> str:
    """
    极简人物名提取：用中文姓氏白名单 + 长度 + 高频过滤
    """
    # 常见中文姓氏（覆盖 99% 情况）
    surnames = set(
        "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
        "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
        "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
        "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计成戴宋茅庞熊纪舒屈项祝董梁杜阮"
        "蓝闵席季麻贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支"
        "柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢"
        "滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷"
        "车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜"
        "黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡"
        "劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎"
        "充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东"
        "欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰"
        "巢关蒯相查后荆红游竺权逯盖益桓公万俟司马上官欧阳夏侯诸葛闻人东方"
        "赫连皇甫尉迟公羊澹台公冶宗政濮阳淳于单于太叔申屠公孙仲孙轩辕令狐"
        "钟离宇文长孙慕容鲜于闾丘司徒司空丌官司寇子车颛孙端木巫马公西漆雕"
        "乐正壤驷公良拓跋夹谷宰父谷梁晋楚阎法汝鄢涂钦段干百里东郭南门呼延"
        "归海羊舌微生岳帅缑亢况郈有琴梁丘左丘东门西门商牟佘佴伯赏南宫墨哈"
        "谯笪年爱阳佟"
    )

    # 用正则扫：从姓氏开始取 2-4 字（姓+1~3 个名）
    # 先把文本切成只含汉字的块
    han_blocks = re.findall(r'[\u4e00-\u9fff]{2,}', text)

    candidates: list[str] = []
    for block in han_blocks:
        for i in range(len(block)):
            if block[i] not in surnames:
                continue
            # 姓后面取 1-3 字作为名字候选
            for name_len in range(1, 4):
                end = i + 1 + name_len
                if end > len(block):
                    break
                cand = block[i:end]
                # 过滤：名字部分不能包含常见动词/副词
                bad = {"从", "被", "把", "让", "向", "在", "到", "去", "来",
                       "是", "有", "不", "也", "都", "就", "还", "又", "要",
                       "会", "能", "可", "好", "大", "小", "多", "少", "第",
                       "说", "看", "想", "做", "出", "上", "下", "里", "外"}
                rest = cand[1:]  # 去掉姓
                if any(ch in bad for ch in rest):
                    continue
                if 2 <= len(cand) <= 4:
                    candidates.append(cand)

    from collections import Counter
    c = Counter(candidates)
    # 只保留出现 ≥2 次的，且排除在停用词里的
    stop_all = {
        "我们", "你们", "他们", "自己", "什么", "怎么", "这里", "那里",
        "这个", "那个", "不是", "就是", "然后", "但是", "还是", "已经",
        "可以", "可能", "应该", "没有", "有些", "一些", "一下", "一样",
        "先生", "小姐", "少爷", "老爷", "夫人", "公子", "姑娘", "道士",
        "和尚", "女子", "男人", "女人", "老人", "小孩", "少年", "青年",
    }
    top = [
        name for name, cnt in c.most_common(top_n * 3)
        if name not in stop_all and cnt >= 1
    ][:top_n]
    return "、".join(top) if top else ""


def merge_chapters_into_state(
    store: StateStore,
    chapters: Iterable[Chapter],
    novel_title: str = "",
) -> NovelState:
    """
    把导入的章节逐条写入状态仓库，同时更新续写锚点
    返回更新后的 NovelState
    """
    state = store.state
    chapters = list(chapters)

    # 1. 基本字段
    if novel_title:
        state.novel_title = novel_title

    # 2. 逐章写入
    total_words_before = state.total_words
    for ch in chapters:
        # 跳过已存在的章节号（避免重复导入）
        if any(c.chapter_no == ch.chapter_no for c in state.chapters):
            log.info("  跳过已存在的第 %d 章", ch.chapter_no)
            continue

        # 生成一个简短摘要
        summary = ch.content[:60].replace("\n", "") + ("…" if len(ch.content) > 60 else "")

        rec = ChapterRecord(
            chapter_no=ch.chapter_no,
            title=ch.title,
            summary=summary,
            content=ch.content,
            word_count=ch.word_count,
            generated_at="imported",
            published=True,          # 导入的都视为已发布（避免重复发微信）
            publish_url=ch.source,
        )
        state.chapters.append(rec)
        state.total_words += ch.word_count
        if ch.chapter_no > state.current_chapter:
            state.current_chapter = ch.chapter_no

    # 3. 重排
    state.chapters.sort(key=lambda c: c.chapter_no)

    # 4. 更新 story_so_far（最近 5 章摘要 + 最后一章末尾作为硬锚点）
    state.story_so_far = _build_continuation_anchor(state)

    # 5. 自动提取人物表（基于全文高频词）
    full_text = "\n".join(c.content for c in state.chapters)
    chars = _extract_characters(full_text)
    if chars:
        state.characters = f"（自动从已有正文提取）核心人物: {chars}"

    store.save()

    added = state.total_words - total_words_before
    log.info(
        "✅ 导入完成: %d 章, 累计 %d 字 (新增 %d 字)",
        len(state.chapters), state.total_words, added,
    )
    log.info("  当前续写锚点（末段最后 200 字预览）: %s",
             state.story_so_far[-200:])
    return state


def _build_continuation_anchor(state: NovelState, keep_summary: int = 5) -> str:
    """
    构建续写锚点 = 最近几章摘要 + 最后一章的原文末尾（作为硬约束）
    """
    recent = state.chapters[-keep_summary:]
    summary_part = "\n".join(
        f"【第{c.chapter_no}章 {c.title}】{c.summary}" for c in recent
    )

    # 取最后一章的原文末尾作为硬锚点
    if state.chapters:
        last = state.chapters[-1]
        tail = _last_paragraph(last.content)
        if tail:
            summary_part += (
                f"\n\n⬇️⬇️ 必须从这里接下去（原文末尾，不能改不能跳过）⬇️⬇️\n"
                f"【第{last.chapter_no}章 结尾原文】\n{tail}\n"
                f"⬆️⬆️ 以上是硬锚点 ⬆️⬆️"
            )

    return summary_part
