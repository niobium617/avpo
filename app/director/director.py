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

from app.core.schema import Brief, Scene, StrictModel
from app.core.state import FatalError

BASE_URL = "https://api.siliconflow.cn/v1"
MAX_STORYBOARD_ATTEMPTS = 2
# 估账常量（¥/百万 token）：SiliconFlow DeepSeek-V3 $0.29/$1.15 折算，M3 成本账本再精确化
COST_PER_M_IN = 2.1
COST_PER_M_OUT = 8.3

SYSTEM_PROMPT = """你是短视频分镜师。把口播文案拆成 3~6 个分镜，输出严格 JSON。

规则：
1. narration 必须逐字取自原文：所有 scene 的 narration 按顺序拼接（忽略空白）后与原文（忽略空白）完全一致，不允许增删改任何字；
2. 每个 scene 包含：scene_id（s1、s2…）、narration、visual（中文画面描述，1 句话）、image_prompt（英文，喂生图模型（FLUX 或通义万相）：具象主体、光影、构图、风格，40 词内）、motion（zoom_in_slow / zoom_out / pan_left / pan_right / none 之一）、shot_size（远景 / 全景 / 中景 / 近景 / 特写 / 空镜 之一）、planned_duration_ms（预估时长毫秒数，整数）、sfx（本镜头音效描述，没有则空字符串）；
3. 只输出 JSON：{"scenes": [ {...}, ... ]}"""

REWRITE_SYSTEM_PROMPT = """你是短视频分镜师。根据创作者的指令重写一个现有分镜，输出严格 JSON。

规则：
1. 只输出单个 scene 对象：{{"scene": {{...}}}}；
2. scene_id 必须保持原值 {scene_id}，不允许改动；
3. narration（口播文案）默认保持原值，只有当创作者指令明确要求修改文案时才允许改动；
4. 每个 scene 包含：scene_id、narration、visual（中文画面描述，1 句话）、image_prompt（英文，40 词内）、motion（zoom_in_slow / zoom_out / pan_left / pan_right / none 之一）、shot_size（远景 / 全景 / 中景 / 近景 / 特写 / 空镜 之一）、planned_duration_ms（预估时长毫秒数，整数）、sfx（音效描述，没有则空字符串）；
5. 只输出 JSON。"""


class StoryboardOutput(StrictModel):
    scenes: list[Scene]


class SingleSceneOutput(StrictModel):
    scene: Scene


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _brief_block(brief: Brief | None) -> str:
    """创作简报 → 提示词块（M6-7.3：把阶段一策划注入分镜，作为视觉不变量）。"""
    if brief is None:
        return ""
    lines = []
    for label, val in (
        ("主题", brief.theme), ("世界观", brief.worldview), ("画风", brief.art_style),
        ("时长", brief.duration), ("平台", brief.platform), ("主角形象", brief.protagonist),
        ("剧情", brief.plot), ("情绪基调", brief.emotion),
        ("主色调", brief.palette), ("光影", brief.lighting), ("人物特征", brief.character),
    ):
        if val.strip():
            lines.append(f"{label}：{val}")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


class Director:
    """文案 → 分镜（LLM）。"""

    def __init__(self, api_key: str, model: str = "deepseek-ai/DeepSeek-V3", base_url: str | None = None):
        self.client = openai.OpenAI(base_url=base_url or BASE_URL, api_key=api_key)
        self.model = model

    def storyboard(
        self,
        text: str,
        motion_hint: str = "",
        *,
        brief: Brief | None = None,
        shot_size_hint: str = "",
    ) -> tuple[list[Scene], float]:
        """返回 (场景列表, LLM 成本元)。校验失败重试 1 次，仍失败抛 FatalError。

        Args:
            text: 口播文案全文。
            motion_hint: M3-4.4 风格模板的运镜指导（追加到系统提示词，控制运镜分配）。
            brief: M6-7.3 创作简报（阶段一策划；注入主题/画风/风格关键词包）。
            shot_size_hint: M6-7.3 风格模板的景别指导。
        """
        if not text.strip():
            raise ValueError("文案为空，无法分镜")
        target = _normalize(text)
        system = SYSTEM_PROMPT
        brief_block = _brief_block(brief)
        if brief_block:
            system += f"\n\n创作简报（整体风格与视觉规范，务必遵守）：\n{brief_block}"
        if motion_hint:
            system += f"\n4. 本片运镜风格要求：{motion_hint}"
        if brief and brief.bgm_hint.strip():
            system += (
                f"\n5. BGM 节奏（planned_duration_ms 应对齐重拍/高潮/骤停等卡点）："
                f"{brief.bgm_hint}"
            )
        if shot_size_hint:
            system += f"\n6. 景别风格要求：{shot_size_hint}"
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
            scene.image_candidates = []
            scene.end_image_asset_id = None
            scene.cost = {}
        return scenes

    def rewrite_scene(
        self,
        scene: Scene,
        instructions: str,
        *,
        brief: Brief | None = None,
        shot_size_hint: str = "",
    ) -> tuple[Scene, float]:
        """按创作者指令局部重写单个分镜（M6-7.3 分镜编辑器「AI 重写」）。

        与 storyboard 不同：只输出一个 scene，scene_id 保持原值；narration 默认
        不变（提示词约束，仅当指令明确要求改文案时才允许）。失败重试 1 次，
        仍失败抛 FatalError。返回 (新场景, LLM 成本元)。
        """
        if not instructions.strip():
            raise ValueError("重写指令为空")
        system = REWRITE_SYSTEM_PROMPT.format(scene_id=scene.scene_id)
        brief_block = _brief_block(brief)
        if brief_block:
            system += f"\n\n创作简报（整体风格与视觉规范，务必遵守）：\n{brief_block}"
        if shot_size_hint:
            system += f"\n景别风格要求：{shot_size_hint}"
        messages: list[dict] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"现有分镜：\n{json.dumps(scene.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                    f"创作者指令：{instructions}"
                ),
            },
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
                data = json.loads(content)
                new_scene = SingleSceneOutput.model_validate(data).scene
                if new_scene.scene_id != scene.scene_id:
                    raise ValueError(
                        f"scene_id 被改写: {new_scene.scene_id}（应保持 {scene.scene_id}）"
                    )
                # 资产/状态字段由管线负责，模型输出一律清空
                new_scene.status = "pending"
                new_scene.image_asset_id = None
                new_scene.image_candidates = []
                new_scene.end_image_asset_id = None
                new_scene.cost = {}
                return new_scene, total_cost
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = exc
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"输出校验失败：{exc}。请重新输出严格 JSON，"
                            f"scene_id 必须保持 {scene.scene_id}。"
                        ),
                    },
                ]

        raise FatalError(f"分镜重写失败: {last_err}", hint="简化重写指令或重试")

    @staticmethod
    def _cost(usage) -> float:
        if usage is None:
            return 0.0
        return (
            usage.prompt_tokens * COST_PER_M_IN + usage.completion_tokens * COST_PER_M_OUT
        ) / 1_000_000
