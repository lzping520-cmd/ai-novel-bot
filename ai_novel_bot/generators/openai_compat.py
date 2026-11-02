"""
OpenAI 兼容格式生成器
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from ..config import AIConfig
from ..utils import setup_logger
from .base import BaseGenerator

log = setup_logger()

PROVIDER_BASE = {
    "openai":    "https://api.openai.com/v1",
    "doubao":    "https://ark.cn-beijing.volces.com/api/v3",
    "qwen":      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek":  "https://api.deepseek.com/v1",
    "zhipu":     "https://open.bigmodel.cn/api/paas/v4",
    "moonshot":  "https://api.moonshot.cn/v1",
}

DOUBAO_ENDPOINT_RE = re.compile(r"^ep-\d{10,}[a-z0-9\-]*$")

class AIProviderError(RuntimeError):
    def __init__(self, status: int, message: str, raw: Any = None):
        super().__init__(f"[HTTP {status}] {message}")
        self.status = status
        self.message = message
        self.raw = raw

class OpenAICompatGenerator(BaseGenerator):

    def __init__(self, ai_cfg: AIConfig, novel_cfg):
        super().__init__(ai_cfg, novel_cfg)
        self.session = requests.Session()
        self.session.trust_env = False   # ✅ 绕开 Clash/V2Ray 等代理

        base = self.ai.base_url or PROVIDER_BASE.get(self.ai.provider, "")
        if not base:
            raise ValueError(f"未知的 AI provider: {self.ai.provider}，请在 config.yaml 的 ai.base_url 中填写")
        base = base.rstrip("/")
        self.endpoint = f"{base}/chat/completions"

        # 超时时间：优先读配置里的 timeout 字段，默认 300 秒
        self.timeout = getattr(self.ai, "timeout", 300) or 300

    # ----------------------- 配置校验 -----------------------
    def validate_config(self) -> list[str]:
        issues: list[str] = []

        if not self.ai.api_key or self.ai.api_key in ("your_api_key_here", "YOUR_API_KEY", "", "xxx"):
            issues.append(
                "ai.api_key 未填写或仍是占位符！\n"
                "  → 请在 .env 里填 AI_API_KEY=你的真实Key 或在 config.yaml 的 ai.api_key 里填写"
            )

        if not self.ai.model or self.ai.model in ("gpt-4o-mini", "your_model"):
            issues.append(
                f"ai.model 未填写或仍是占位符（当前值: {self.ai.model!r}）！"
            )

        is_using_default_doubao_url = (
            not self.ai.base_url or "ark.cn-beijing.volces.com" in self.ai.base_url
        )
        if self.ai.provider == "doubao" and is_using_default_doubao_url:
            if not DOUBAO_ENDPOINT_RE.match(self.ai.model):
                issues.append(
                    f"豆包 model 字段看起来不是有效的 Endpoint ID（当前: {self.ai.model!r}）"
                )

        return issues

    # ----------------------- 联网诊断 -----------------------
    def test_connection(self) -> tuple[bool, str]:
        issues = self.validate_config()
        if issues:
            return False, "配置自检未通过:\n" + "\n".join(f"  - {i}" for i in issues)

        try:
            payload = {
                "model": self.ai.model,
                "messages": [{"role": "user", "content": "你好，回复一个字"}],
                "max_tokens": 5, "temperature": 0,
            }
            headers = {
                "Authorization": f"Bearer {self.ai.api_key}",
                "Content-Type": "application/json",
            }
            log.info("正在测试连接 → %s  (provider=%s, model=%s)", self.endpoint, self.ai.provider, self.ai.model)
            resp = self.session.post(self.endpoint, headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=30)
            data = resp.json() if resp.content else {}

            if resp.status_code == 200:
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return True, f"✅ 连接成功！AI 回复: {content!r}"

            msg = self._extract_error(resp.status_code, data)
            return False, msg
        except requests.exceptions.ConnectionError as e:
            return False, f"网络连接失败: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"

    @staticmethod
    def _extract_error(status: int, data: dict) -> str:
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message", "")
            code = err.get("code") or err.get("type", "")
            return f"[HTTP {status}] {code}: {msg}\n  (原始: {json.dumps(data, ensure_ascii=False)[:500]})"
        if "message" in data:
            return f"[HTTP {status}] {data.get('code', '')}: {data['message']}\n  (原始: {json.dumps(data, ensure_ascii=False)[:500]})"
        return f"[HTTP {status}] 未知错误格式: {json.dumps(data, ensure_ascii=False)[:500]}"

    # ----------------------- 正式调用（带重试） -----------------------
    def _chat(self, messages: list[dict]) -> str:
        # 构建 payload
        is_ark_direct = (
            self.ai.provider == "doubao"
            and (not self.ai.base_url or "ark.cn-beijing.volces.com" in self.ai.base_url)
        )
        payload: dict[str, Any] = {
            "model": self.ai.model,
            "messages": messages,
            "temperature": self.ai.temperature,
        }
        if self.ai.max_tokens:
            if is_ark_direct:
                payload["max_completion_tokens"] = self.ai.max_tokens
            else:
                payload["max_tokens"] = self.ai.max_tokens

        headers = {
            "Authorization": f"Bearer {self.ai.api_key}",
            "Content-Type": "application/json",
        }

        issues = self.validate_config()
        if issues:
            raise AIProviderError(0, "配置问题:\n" + "\n".join(f"  - {i}" for i in issues))

        log.info("POST %s  model=%s  timeout=%ds", self.endpoint, self.ai.model, self.timeout)

        # ---- 最多重试 2 次（总共跑 3 次） ----
        last_err = None
        for attempt in range(1, 4):
            try:
                resp = self.session.post(
                    self.endpoint, headers=headers,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    timeout=self.timeout,
                )
                break   # 成功拿到响应，跳出重试循环
            except requests.exceptions.Timeout as e:
                last_err = e
                if attempt < 3:
                    wait = attempt * 10
                    log.warning(f"⚠️ 第 {attempt} 次超时，{wait} 秒后重试...")
                    time.sleep(wait)
                    continue
                raise AIProviderError(
                    0,
                    f"连续 {attempt} 次超时（每次 {self.timeout} 秒）。建议：\n"
                    f"  1) config.yaml 里把 ai.timeout 调大（比如 600）\n"
                    f"  2) 换个更快的模型，或减少 target_words_per_chapter"
                ) from e
            except requests.exceptions.ConnectionError as e:
                last_err = e
                if attempt < 3:
                    time.sleep(5)
                    continue
                raise AIProviderError(0, f"无法连接 {self.endpoint}: {e}") from e

        # ---- 解析响应 ----
        if not (200 <= resp.status_code < 300):
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text[:500]}
            raise AIProviderError(resp.status_code, self._extract_error(resp.status_code, data), raw=data)

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIProviderError(
                200,
                f"AI 返回结构异常: {json.dumps(data, ensure_ascii=False)[:800]}",
                raw=data,
            ) from e

        if isinstance(content, list):
            content = "".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )
        return str(content).strip()