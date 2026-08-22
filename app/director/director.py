"""分镜生成（IMPLEMENTATION_PLAN 2.7）。

DeepSeek（SiliconFlow 渠道）→ 强制 JSON output（response_format=json_object）
→ pydantic 校验（app/core/schema.Scene）→ 返回可落盘场景列表。

核心校验：所有 scene 的 narration 按顺序拼接（去空白）必须与原文（去空白）
完全一致 —— 文案不允许增删改，这是字幕/配音对齐的前提。
校验失败：把错误反馈给模型重试 1 次；仍失败抛 FatalError（带修复提示）。
"""

import json
import re

import openai

from app.core.schema import Scene, StrictModel
from app.core.state import FatalError

BASE_URL = "https://api.siliconflow.cn/v1"
MAX_STORYBOARD_ATTEMPTS = 2
# 估账常量（¥/百万 token）：SiliconFlow DeepSeek-V3 $0.29/$1.15 折算，M3 成本账本再精确化
COST_PER_M_IN = 2.1
COST_PER_M_OUT = 8.3

SYSTEM_PROMPT = """你是短视频分镜师。把口播文案拆成 3~6 个分镜，输出严格 JSON。

规则：
1. narration 必须逐字取自原文：所有 scene 的 narration 按顺序拼接（忽略空白）后与原文（忽略空白）完全一致，不允许增删改任何字；
2. 每个 scene 包含：scene_id（s1、s2…）、narration、visual（中文画面描述，1 句话）、image_prompt（英文，喂生图模型（FLUX 或通义万相）：具象主体、光影、构图、风格，40 词内）、motion（zoom_in_slow / zoom_out / pan_left / pan_right / none 之一）；
3. 只输出 JSON：{"scenes": [ {...}, ... ]}"""


class StoryboardOutput(StrictModel):
    scenes: list[Scene]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


class Director:
    """文案 → 分镜（LLM）。"""

    def __init__(self, api_key: str, model: str = "deepseek-ai/DeepSeek-V3", base_url: str | None = None):
        self.client = openai.OpenAI(base_url=base_url or BASE_URL, api_key=api_key)
        self.model = model

    def storyboard(self, text: str, motion_hint: str = "") -> tuple[list[Scene], float]:
        """返回 (场景列表, LLM 成本元)。校验失败重试 1 次，仍失败抛 FatalError。

        Args:
            text: 口播文案全文。
            motion_hint: M3-4.4 风格模板的运镜指导（追加到系统提示词，控制运镜分配）。
        """
        if not text.strip():
            raise ValueError("文案为空，无法分镜")
        target = _normalize(text)
        system = SYSTEM_PROMPT
        if motion_hint:
            system += f"\n4. 本片运镜风格要求：{motion_hint}"
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"文案：\n{text}"},
        ]

        total_cost = 0.0
        last_err: Exception | None = None
        for _ in range(MAX_STORYBOARD_ATTEMPTS):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            total_cost += self._cost(resp.usage)
            content = resp.choices[0].message.content
            try:
                return self._parse(content, target), total_cost
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = exc
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"输出校验失败：{exc}。请重新输出严格 JSON，narration 必须逐字覆盖原文。"},
                ]

        raise FatalError(f"分镜 JSON 校验失败: {last_err}", hint="换一条更规整的文案重试")

    def _parse(self, content: str, target: str) -> list[Scene]:
        data = json.loads(content)
        scenes = StoryboardOutput.model_validate(data).scenes
        if not scenes:
            raise ValueError("scenes 为空")

        # scene_id 唯一
        ids = [s.scene_id for s in scenes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"scene_id 重复: {ids}")

        # narration 逐字覆盖原文（去空白比对）
        joined = _normalize("".join(s.narration for s in scenes))
        if joined != target:
            raise ValueError(f"narration 拼接与原文不一致（原文 {len(target)} 字，得到 {len(joined)} 字）")

        # 清空 LLM 不该控制的字段：状态与资产由管线负责
        for scene in scenes:
            scene.status = "pending"
            scene.image_asset_id = None
            scene.cost = {}
        return scenes

    @staticmethod
    def _cost(usage) -> float:
        if usage is None:
            return 0.0
        return (
            usage.prompt_tokens * COST_PER_M_IN + usage.completion_tokens * COST_PER_M_OUT
        ) / 1_000_000
