"""AVPO 数据模型 v0.1 —— pydantic schema，project.json 的唯一真理源。

设计要点（见 EXECUTION_PLAN §3）：
- 口播视频的原子单元是 scene（一段文案 + 一张图 + 一个运镜 + 一段字幕）。
- 每个任务带 status（pending/running/done/failed）→ 断点续跑 = 扫描 failed/pending 重跑。
- prompt_hash + seed → 相同 prompt 不重复调 API；cost 记在任务上 → 成本可汇总。
- 字幕与配音共用一份时间戳来源，不会错位。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TaskStatus = Literal["pending", "running", "done", "failed"]
MotionKind = Literal["zoom_in_slow", "zoom_out", "pan_left", "pan_right", "none"]
AssetKind = Literal["image", "audio", "video"]

# 编排层状态机节点（IMPLEMENTATION_PLAN §5）
PIPELINE_NODES = ("direct", "confirm", "gen_assets", "timeline", "export")


class StrictModel(BaseModel):
    """本模块所有模型的基类：禁未知字段（改错字段当场报错），赋值时也校验。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------- 配置

class TTSConfig(StrictModel):
    engine: str = "edge-tts"
    voice: str = "zh-CN-YunxiNeural"
    rate: str = "+0%"


class ImageConfig(StrictModel):
    model: str = "black-forest-labs/FLUX.1-schnell"
    size: str = "16:9"


class LLMConfig(StrictModel):
    model: str = "deepseek-chat"


class ProjectConfig(StrictModel):
    tts: TTSConfig = Field(default_factory=TTSConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


# ---------------------------------------------------------------- 内容

class Scene(StrictModel):
    """一个分镜：一段口播文案 + 一张图 + 一个运镜。"""

    scene_id: str
    narration: str = ""
    visual: str = ""            # 画面描述（中文，人看）
    image_prompt: str = ""      # 生图提示词（英文，喂 FLUX）
    image_asset_id: str | None = None
    motion: MotionKind = "none"
    status: TaskStatus = "pending"
    cost: dict[str, float] = Field(default_factory=dict)   # 如 {"image": 0.02, "llm": 0.001}


class Voiceover(StrictModel):
    """整片一条配音。duration_ms 以实际音频文件为准（mutagen 读取）。"""

    asset_id: str | None = None
    status: TaskStatus = "pending"
    duration_ms: int | None = None


class Subtitle(StrictModel):
    scene_id: str
    start_ms: int
    end_ms: int
    text: str


class VideoClip(StrictModel):
    asset_id: str
    start_ms: int = 0
    duration_ms: int
    motion: MotionKind = "none"


class AudioClip(StrictModel):
    asset_id: str
    offset_ms: int = 0


class Timeline(StrictModel):
    """MVP 固定三轨中的两条素材轨（字幕轨 = subtitles 字段）。"""

    video: list[VideoClip] = Field(default_factory=list)
    voiceover: list[AudioClip] = Field(default_factory=list)


class Asset(StrictModel):
    type: AssetKind
    path: str                   # 相对项目目录，如 assets/s1_v1.png
    model: str | None = None
    prompt_hash: str | None = None
    seed: int | None = None
    cost: float = 0.0
    status: TaskStatus = "pending"


class ExportInfo(StrictModel):
    format: str = "jianying"
    path: str | None = None     # 相对项目目录，如 exports/proj_001_draft
    jianying_version: str | None = None   # 钉住的验证版本
    status: TaskStatus = "pending"


# ---------------------------------------------------------------- 顶层

class Project(StrictModel):
    project_id: str
    title: str = ""
    schema_version: str = "0.1"
    config: ProjectConfig = Field(default_factory=ProjectConfig)
    pipeline: dict[str, TaskStatus] = Field(
        default_factory=lambda: {node: "pending" for node in PIPELINE_NODES}
    )
    scenes: list[Scene] = Field(default_factory=list)
    voiceover: Voiceover = Field(default_factory=Voiceover)
    subtitles: list[Subtitle] = Field(default_factory=list)
    timeline: Timeline = Field(default_factory=Timeline)
    assets: dict[str, Asset] = Field(default_factory=dict)
    export: ExportInfo = Field(default_factory=ExportInfo)

    @model_validator(mode="after")
    def _check_references(self) -> "Project":
        """跨字段引用完整性：id 唯一、外键存在、pipeline 节点合法。"""
        # scene_id 唯一
        scene_ids = [s.scene_id for s in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            dup = sorted({i for i in scene_ids if scene_ids.count(i) > 1})
            raise ValueError(f"scene_id 重复: {dup}")

        # pipeline 只允许已知节点
        unknown = set(self.pipeline) - set(PIPELINE_NODES)
        if unknown:
            raise ValueError(f"pipeline 含未知节点: {sorted(unknown)}")

        # 字幕引用存在的 scene
        for sub in self.subtitles:
            if sub.scene_id not in scene_ids:
                raise ValueError(f"字幕引用了不存在的 scene_id: {sub.scene_id}")

        # 时间线素材引用存在的 asset
        asset_ids = set(self.assets)
        for clip in self.timeline.video:
            if clip.asset_id not in asset_ids:
                raise ValueError(f"时间线视频轨引用了不存在的 asset_id: {clip.asset_id}")
        for clip in self.timeline.voiceover:
            if clip.asset_id not in asset_ids:
                raise ValueError(f"时间线音频轨引用了不存在的 asset_id: {clip.asset_id}")

        # 配音 / 场景图引用存在的 asset
        if self.voiceover.asset_id and self.voiceover.asset_id not in asset_ids:
            raise ValueError(f"voiceover 引用了不存在的 asset_id: {self.voiceover.asset_id}")
        for scene in self.scenes:
            if scene.image_asset_id and scene.image_asset_id not in asset_ids:
                raise ValueError(f"scene {scene.scene_id} 引用了不存在的 asset_id: {scene.image_asset_id}")

        return self
