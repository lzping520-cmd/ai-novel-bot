"""
生成器基类 & Prompt 模板

v2 改进：
  - 设定固化：前 3 章完整文本始终喂给 AI，永不丢失世界观/人物设定
  - 续写锚点：上一章原文末尾 + 硬约束，确保无缝衔接
  - 一致性检查器：生成后自动打分（人名/状态/剧情），低于阈值自动重写
  - 字数精准控制：过少自动补写，过多自动截断
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

from ..config import AIConfig, NovelConfig
from ..storage.state import NovelState, ChapterRecord


SYSTEM_PROMPT = """你是一位在起点/番茄小说连载过百万字的顶级网文作者。你的目标是让读者追更到凌晨3点还不想睡。

【写作铁律（违反即失败）】
🔒 一致性铁律：
- 所有人物姓名、外号、道号必须与人物表完全一致
- 人物状态必须连贯——上一章重伤，这一章不可能活蹦乱跳
- 地点/门派/星球名前后绝对不能变

🎯 网文节奏铁律（每章都要做到）：
1. **开头 200 字内抓住读者** — 用一个悬念/冲突/意外开场，不能慢热铺垫
2. **每 500 字必须有一个小高潮** — 打斗高潮、对话高潮、心理高潮都行
3. **对话占比 30-50%** — 大段独白和纯描写会让读者跳读
4. **人物说话必须有差异** — 主角和反派、老者和少年、男女主角用词必须不同，读者闭着眼能猜出是谁在说话
5. **结尾必须留钩子** — 最后 1-2 句话制造悬念，让读者想点下一章（这是决定订阅率的关键）

✍️ 语言要求：
- 现代中文，口语化但有文学质感，绝对不能有翻译腔（"他认为道"× → "他暗道"✓）
- 善用**身体微反应**代替直白情绪：不说"张三很生气"，写"张三指节攥得发白，指甲掐进掌心却浑然不觉"
- 善用**短句制造紧张感**：打斗/对峙时句子要短。悠闲场景可以长句铺陈
- 每段对话不超过 3 句就要穿插动作/环境描写，不要大段对话像剧本

🚫 绝对禁止：
- AI 腔："不禁"、"竟然"、"仿佛"、"宛如"、"不由得" 不要连续出现超过 2 次
- 元叙述："本章完"、"且听下回分解"、"让我们拭目以待"
- 人物说话像念说明书：要有情绪、有停顿、有言不由衷"""


CHAPTER_TEMPLATE = """═══════════════════════════════════
【小说标题】{title}
【类型风格】{genre} / {style}
【世界观】{world_setting}
【主要人物】{main_characters}
【整体大纲】{outline}
═══════════════════════════════════

【进度】第 {chapter_no} 章 · 累计 {total_words} 字

【设定固化（不可违背）· 前3章原文】
{setting_seed}

【最近 {recent} 章剧情摘要】
{story_so_far}

{continuation_warning}

═══════════════════════════════════
【本章写作指令】

① 字数：约 {target} 字（误差 ±15%）
② 结构：
   - 开头 200 字 → 用悬念/冲突/意外开场（不要用"话说"、"且说"开头）
   - 中间 70% → 推进剧情 + 对话 + 场景 + 小高潮
   - 结尾 → 留一个**让读者想骂街的钩子**（例如：他推开门，看到了本不该出现在这里的人——）

③ 输出格式：
   第一行：# 章节标题（6-12字，要抓眼球，不要用"第X章"当标题）
   然后：正文（纯正文，不要任何解释或作者按）
   最后一行（不加 ``` 包裹）：
   {{"summary": "30字内的本章摘要", "new_memory": "本章新增的伏笔/状态变化（可空）"}}

⚠️ 再次提醒：人物一致性 > 一切。对话要像不同的人说的话。
═══════════════════════════════════

现在开始创作："""


CONTINUATION_MODE = """
⚠️⚠️⚠️ 重要：这是「续写模式」，不是从零开写！
- 上一章的原文结尾已用「⬇️⬇️」标记，你必须 **从那一段原文的最后一个字之后，紧接着往下写**。
- 绝对不能重写上一章已经写过的内容，不能复述前面的情节。
- 绝对不能让场景/人物/状态出现跳跃性矛盾。
- 如果上一章结尾是"他抬起手，指尖触到了那枚冰凉的铜钱——"，你必须从这个动作之后继续。
"""


# ===== 网文质量审计器（取代纯一致性检查）=====
QUALITY_AUDIT_PROMPT = """你是一位毒舌的网文主编，专门挑AI写的小说毛病。请审计下面这章新写的内容。

【人物表】
{characters}

【设定基线（前3章原文）】
{setting_seed}

【最近5章摘要】
{story_so_far}

【本次新写章节（第{chapter_no}章）】
{new_chapter}

请按以下维度打分（每项 0-100），并列出问题：

1. 【一致性】consistency — 人名/状态/地点/门派是否与前文矛盾？
2. 【人物声音差异化】voice_diff — 不同人物说话的用词/语气/口头禅是否有区分？还是全员同声？
3. 【身体微反应】body_cue — 是否有"指节攥白/喉结滚动/瞳孔收缩"等身体微反应代替直白情绪？
4. 【AI腔浓度】ai_tone — "不禁/竟然/仿佛/宛如/不由得" 等AI高频词是否过多？翻译腔是否明显？
5. 【节奏与钩子】hook — 开头是否200字内抓眼球？结尾是否留了让读者想看下一章的钩子？
6. 【对话密度】dialogue — 对话占比是否在30%-50%之间？还是大段独白/纯描写？

输出 JSON（不要用 ``` 包裹）：
{{
  "consistency_pass": true/false,
  "score": 0-100,
  "dimensions": {{
    "consistency": 0-100,
    "voice_diff": 0-100,
    "body_cue": 0-100,
    "ai_tone": 0-100,
    "hook": 0-100,
    "dialogue": 0-100
  }},
  "critical_issues": ["最影响阅读的2-3个问题，优先列一致性问题"],
  "suggestion": "具体怎么改，给AI的修改指令（50字内）"
}}"""


class BaseGenerator(ABC):
    """AI 生成器抽象基类"""

    def __init__(self, ai_cfg: AIConfig, novel_cfg: NovelConfig):
        self.ai = ai_cfg
        self.novel = novel_cfg
        self._max_rewrites = 3           # 最多重写几次
        self._consistency_pass_threshold = 70  # 分数低于这个值就重写

    @abstractmethod
    def _chat(self, messages: list[dict]) -> str:
        """调用模型，返回完整文本"""

    def generate_chapter(self, state: NovelState, chapter_no: int | None = None) -> tuple[str, str, str]:
        """
        生成下一章（带一致性检查 + 自动重写 + 字数精准控制）
        返回: (章节标题, 章节正文, 摘要)
        """
        # 确定这次要写的章节号（可能是废弃章重写）
        if chapter_no is None:
            chapter_no = state.current_chapter + 1

        total_words = state.total_words

        # 续写模式：检查最后一章的原文末尾
        is_continuation = self._has_valid_continuation_anchor(state)
        continuation_warning = CONTINUATION_MODE if is_continuation else ""

        user_prompt = CHAPTER_TEMPLATE.format(
            title=self.novel.title,
            genre=self.novel.genre,
            style=self.novel.style,
            world_setting=self.novel.world_setting or "暂无特殊设定",
            main_characters=state.characters or self.novel.main_characters or "主角待定，请在第一章塑造",
            outline=self.novel.outline or "暂无大纲，请自由发挥一个有趣的开篇",
            chapter_no=chapter_no,
            total_words=total_words,
            setting_seed=state.setting_seed or "（这是开篇第一章，设定由你在本章内建立）",
            recent=5,
            story_so_far=state.story_so_far or "这是开篇第一章，此前无正文。",
            continuation_warning=continuation_warning,
            target=self.novel.target_words_per_chapter,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # 生成 + 检查 + 自动重写
        title, body, summary = None, "", ""
        for attempt in range(1, self._max_rewrites + 1):
            raw = self._chat(messages)
            title, body, summary = self._parse_output(raw, chapter_no)

            # 字数精准控制
            body = self._enforce_word_count(body, self.novel.target_words_per_chapter)

            # 一致性检查（只在有前文章节时检查）
            has_context = bool(state.setting_seed) or bool(state.story_so_far)
            if has_context and attempt < self._max_rewrites:
                ok, score, issues, suggestion = self._check_consistency(state, chapter_no, body)
                if ok or not issues:
                    break
                print(f"  [一致性检查] 第{chapter_no}章 得分 {score}/100，重写第 {attempt+1} 次：{issues[0] if issues else ''}")
                # 把检查结果喂回 AI，让它知道问题在哪
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"❌ 你的版本有以下问题：{'; '.join(issues)}\n"
                               f"修改建议：{suggestion}\n"
                               f"请重写第 {chapter_no} 章，修正上述问题后再输出。"
                })
            else:
                break

        return title, body, summary

    # ------ 工具方法 ------
    def _has_valid_continuation_anchor(self, state: NovelState) -> bool:
        """检查是否有有效的续写锚点（最后一章未废弃且有原文）"""
        active = [c for c in state.chapters if not c.discarded]
        if not active:
            return False
        last = active[-1]
        return bool(last.content.strip())

    def _build_continuation_anchor(self, state: NovelState, last_chapter_no: int | None = None) -> str:
        """构建续写锚点：上一章原文末尾 + 标记"""
        active = [c for c in state.chapters if not c.discarded]
        if last_chapter_no:
            target = next((c for c in active if c.chapter_no == last_chapter_no), None)
            if target:
                active = [target]
        if not active:
            return ""
        last = active[-1]
        tail = last.content.strip()[-300:]  # 取最后 300 字足够
        return (
            f"\n\n⬇️⬇️ 必须从这里接下去（原文末尾，不能改不能跳过）⬇️⬇️\n"
            f"【第{last.chapter_no}章 结尾原文】\n{tail}\n"
            f"⬆️⬆️ 以上是硬锚点 ⬆️⬆️"
        )

    def _enforce_word_count(self, body: str, target: int) -> str:
        """字数精准控制：过少补写，过多截断"""
        actual = len(body)
        low = target * 0.8
        high = target * 1.2

        if actual < low:
            # 太少 → 调强 Prompt，让 AI 补写
            try:
                extend_prompt = (
                    f"你刚才写了一段约 {actual} 字的小说，但需要 {target} 字。"
                    f"请在保持原文风格、人物、情节完全不变的前提下，"
                    f"对以下内容进行**扩写**——增加更多的环境描写、人物心理活动、对话细节。"
                    f"直接输出扩写后的完整文本，不要解释。\n\n原文：\n{body}"
                )
                ext = self._chat([
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": extend_prompt},
                ])
                # 取最后一次 AI 给的有效 body
                _, body, _ = self._parse_output(ext, 0)
                if len(body) < low:
                    # 补写后还是少，保留原 body（至少是连贯的）
                    return actual < low * 0.5 and self._expand_simple(body, target) or body
                return body
            except Exception:
                return self._expand_simple(body, target)

        if actual > high:
            # 太多 → 在最接近 target 字数的完整段落处截断
            paragraphs = body.split("\n")
            result = []
            total = 0
            for p in paragraphs:
                if total + len(p) > target and result:
                    break
                result.append(p)
                total += len(p)
            return "\n".join(result).strip()

        return body

    @staticmethod
    def _expand_simple(body: str, target: int) -> str:
        """简单补写：重复最后一个段落直到够字数（兜底用）"""
        if not body:
            return body
        paragraphs = body.split("\n")
        last = paragraphs[-1]
        while len(body) < target:
            body = body + "\n" + last
        return body

    def _check_consistency(self, state: NovelState, chapter_no: int, new_chapter: str) -> tuple[bool, int, list[str], str]:
        """跑网文质量审计，返回 (是否通过, 综合分数, 关键问题列表, 修改建议)"""
        # ---- 先跑零成本的纯代码审计（省 token）----
        cheap_score, cheap_issues = self._cheap_quality_scan(new_chapter)

        prompt = QUALITY_AUDIT_PROMPT.format(
            characters=state.characters or self.novel.main_characters or "暂无",
            setting_seed=state.setting_seed or "暂无",
            story_so_far=state.story_so_far or "暂无",
            chapter_no=chapter_no,
            new_chapter=new_chapter[:3000],  # 审计稍长一点更准确
        )
        try:
            raw = self._chat([
                {"role": "system", "content": "你是毒舌网文主编，严格审计。只输出 JSON，不要任何额外文字。"},
                {"role": "user", "content": prompt},
            ])
            data = self._parse_json_from_text(raw)
            if not data:
                # AI 审计失败，降级用纯代码审计
                return cheap_score >= self._consistency_pass_threshold, cheap_score, cheap_issues, "（AI审计不可用，纯代码扫描结果）"

            passed = data.get("consistency_pass", True)
            ai_score = data.get("score", 80)
            # 兼容新旧字段名
            ai_issues = data.get("critical_issues") or data.get("issues") or []
            suggestion = data.get("suggestion", "") or ""

            # 综合分数：AI 审计 70% + 纯代码 30%（纯代码客观但维度少）
            final_score = round(ai_score * 0.7 + cheap_score * 0.3)
            all_issues = list(dict.fromkeys(ai_issues + cheap_issues))  # 去重保序

            # 打印各维度分数（方便调试）
            dims = data.get("dimensions", {})
            if dims:
                dim_str = "  ".join(f"{k}={v}" for k, v in dims.items())
                print(f"  [质量审计] 第{chapter_no}章 | AI打分={ai_score} 纯代码={cheap_score} 综合={final_score}")
                print(f"    维度: {dim_str}")
            elif cheap_issues:
                print(f"  [质量审计] 第{chapter_no}章 | 纯代码扫描发现: {cheap_issues}")

            return passed and final_score >= self._consistency_pass_threshold, final_score, all_issues, suggestion
        except Exception:
            return cheap_score >= self._consistency_pass_threshold, cheap_score, cheap_issues, "（AI审计异常，纯代码扫描结果）"

    @staticmethod
    def _cheap_quality_scan(text: str) -> tuple[int, list[str]]:
        """
        零成本纯代码质量扫描——AI 腔高频词 + 对话密度 + 钩子检测
        返回 (0-100 分数, 问题列表)
        """
        issues: list[str] = []
        score = 100

        # 1️⃣ AI 腔高频词扫描
        ai_tone_words = ["不禁", "竟然", "仿佛", "宛如", "不由得", "霎时间", "顿时", "只见", "只听"]
        hit_words = []
        for w in ai_tone_words:
            count = text.count(w)
            if count >= 2:  # 同一词出现 2 次以上 → 扣分
                hit_words.append(f"{w}×{count}")
                score -= count * 3
        if hit_words:
            issues.append(f"AI腔词过多: {', '.join(hit_words)}")

        # 2️⃣ 对话占比统计
        dialogue_chars = sum(1 for c in text if c in '"「」"')  # 粗略算
        total_chars = len(text)
        dialogue_ratio = dialogue_chars / max(total_chars, 1)
        # 粗略换算：引号出现次数 / 总字符
        # 更准确的算法是算包含对话的行数
        lines = text.split("\n")
        dialogue_lines = sum(1 for l in lines if '"' in l or "「" in l or "」" in l)
        dialogue_line_ratio = dialogue_lines / max(len(lines), 1)

        if dialogue_line_ratio < 0.15:  # 对话行占比 < 15% → 大段独白
            issues.append(f"对话太少（对话行占比仅 {dialogue_line_ratio:.0%}），超过60%是纯描写/独白")
            score -= 15
        elif dialogue_line_ratio > 0.75:  # 对话行占比 > 75% → 像剧本
            issues.append(f"对话过多（对话行占比 {dialogue_line_ratio:.0%}），缺少场景/动作描写")
            score -= 8

        # 3️⃣ 结尾钩子检测：最后 100 字里是否有悬念标记
        tail = text[-100:] if len(text) > 100 else text
        hook_markers = ["——", "？", "……", "却没想到", "竟然发现", "推开门", "转头一看", "瞳孔骤缩"]
        has_hook = any(m in tail for m in hook_markers)
        if not has_hook:
            issues.append("结尾没有钩子（最后100字没有悬念/意外）")
            score -= 10

        # 4️⃣ 翻译腔检测："他认为道" / "说道" / "问道" 连续出现
        bad_patterns = ["认为道", "说道道", "不禁想到"]
        for p in bad_patterns:
            if p in text:
                issues.append(f"翻译腔: 出现'{p}'")
                score -= 5

        return max(0, score), issues

    @staticmethod
    def _parse_json_from_text(text: str) -> dict | None:
        """从可能含多余文字的文本里提取 JSON 对象"""
        if not text:
            return None
        text = text.strip()
        # 直接尝试解析
        try:
            return json.loads(text)
        except Exception:
            pass
        # 用正则找第一个 {...}
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return None

    # ------ 输出解析 ------
    @staticmethod
    def _parse_output(raw: str, chapter_no: int) -> tuple[str, str, str]:
        lines = raw.splitlines()
        title = f"第{chapter_no}章"
        body_start = 0

        # 找标题行
        for i, line in enumerate(lines[:5]):
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                body_start = i + 1
                break
            if stripped.startswith("第") and "章" in stripped:
                title = stripped
                body_start = i + 1
                break

        # 分离最后一行 JSON 元信息
        body_lines = lines[body_start:]
        meta_json = None
        if body_lines:
            last = body_lines[-1].strip()
            if last.startswith("{") and last.endswith("}"):
                meta_json = last
                body_lines = body_lines[:-1]

        summary = ""
        if meta_json:
            try:
                meta = json.loads(meta_json)
                summary = meta.get("summary", "")
            except Exception:
                pass

        body = "\n".join(body_lines).strip()
        return title, body, summary
