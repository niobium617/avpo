"""风格模板（IMPLEMENTATION_PLAN 4.4）。

模板 = 分镜运镜指导 + 字幕样式 + BGM（可选），JSON 文件位于 templates/<id>.json：
- fast_talk 快节奏口播：运镜频繁切换，字幕醒目；
- emotional 情感向：慢运镜 + BGM（约定 assets/bgm.mp3，文件缺失降级跳过不报错）；
- explainer 解说向：字幕样式不同（大字号顶部）。
- default 内建：MVP 固定样式（6 号低位描边），无 BGM —— 老项目兼容。

接入点：
- avpo new --style <id> → project.config.style（schema 扩展字段，老 JSON 默认 default）；
- direct 节点把模板 motion_hint 注入分镜提示词（app/director/director.py）；
- 导出节点按模板应用字幕样式 + BGM 轨（app/export/jianying.py）。
"""

import json
from pathlib import Path

from pydantic import Field

from app.core.schema import Brief, Scene, StrictModel

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"   # app/core/styles.py → 项目根/templates


class SubtitleStyle(StrictModel):
    size: float = 6.0            # 字号
    y: float = -0.8              # 画布低位（剪映导入字幕惯例）
    border_width: float = 40.0   # 黑描边宽度


class StyleTemplate(StrictModel):
    id: str
    name: str
    description: str = ""
    motion_hint: str = ""                       # 注入 director 系统提示词的运镜指导
    # M6-7.2 提示词模块（阶段二「提示词模块化」：固定「画风+光影+画质」模块）：
    prompt_prefix: str = ""                     # 画风模块（英文，如 "flat illustration, bold shapes"）
    prompt_lighting: str = ""                   # 光影模块（独立字段 —— 光影逻辑是画风一致的关键败点）
    quality_suffix: str = ""                    # 画质模块（英文，如 "ultra detailed, sharp focus"）
    shot_size_hint: str = ""                    # 景别指导（中文，注入 director 系统提示词）
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    bgm: str | None = None                      # 相对项目目录；文件缺失降级跳过


DEFAULT_STYLE = StyleTemplate(
    id="default", name="默认", description="MVP 固定样式：6 号低位黑描边字幕，无 BGM",
)


def load_style(style_id: str) -> StyleTemplate:
    """按 id 加载模板；default 返回内建（老项目兼容）。"""
    if style_id == "default":
        return DEFAULT_STYLE
    path = TEMPLATES_DIR / f"{style_id}.json"
    if not path.is_file():
        raise ValueError(
            f"未知风格模板: {style_id}（可用: {', '.join(list_styles())}）"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return StyleTemplate(**data)


def list_styles() -> list[str]:
    """可用模板 id（default 优先展示，其后按文件名排序）。"""
    return ["default"] + sorted(p.stem for p in TEMPLATES_DIR.glob("*.json"))


def compose_image_prompt(style: StyleTemplate, brief: Brief | None, scene: Scene) -> str:
    """拼装最终生图提示词（M6-7.2，生成时唯一拼装点）。

    提示词模块化：固定「画风 + 光影 + 画质」模块只换不写，变量部分是创作者可编辑的
    scene.image_prompt（「主体 + 动作 + 场景」）。顺序：
        style.prompt_prefix → 光影(brief.lighting 覆盖 style.prompt_lighting)
        → brief.palette（主色调）→ brief.character（人物特征）
        → scene.image_prompt → style.quality_suffix
    非空模块以 ", " 连接 —— 确定性拼装（同输入同输出）是 prompt_hash 缓存稳定的前提。
    """
    brief = brief or Brief()          # None（旧项目）按空 Brief 拼装，退化为纯模板+场景模块
    parts = [
        style.prompt_prefix,
        brief.lighting.strip() or style.prompt_lighting,
        brief.palette,
        brief.character,
        scene.image_prompt,
        style.quality_suffix,
    ]
    return ", ".join(p for p in parts if p.strip())
