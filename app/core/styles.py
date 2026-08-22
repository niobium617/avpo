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

from app.core.schema import StrictModel

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
