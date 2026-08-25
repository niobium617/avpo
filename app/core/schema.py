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
# 景别（M6 阶段一：分镜脚本标注）
ShotSize = Literal["远景", "全景", "中景", "近景", "特写", "空镜", ""]
# LLM/生图渠道：siliconflow（FLUX + DeepSeek-V3）或 dashscope（通义万相 + qwen）
ProviderKind = Literal["siliconflow", "dashscope"]

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
    provider: ProviderKind = "siliconflow"
    model: str = "black-forest-labs/FLUX.1-schnell"   # dashscope 渠道用 wanx2.1-t2i-turbo
    size: str = "16:9"
    # M6-7.1：每镜候选图张数（阶段二「3~5 版候选筛选」）；JSON 可改，UI 暂不暴露
    candidates: int = Field(default=3, ge=1, le=6)


class LLMConfig(StrictModel):
    provider: ProviderKind = "siliconflow"
    # siliconflow 渠道的 DeepSeek-V3（deepseek-chat 是 DeepSeek 官方 API 的命名）；
    # dashscope 渠道用 qwen-plus
    model: str = "deepseek-ai/DeepSeek-V3"


class ProjectConfig(StrictModel):
    tts: TTSConfig = Field(default_factory=TTSConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    style: str = "default"   # M3-4.4 风格模板 id（templates/，default = MVP 固定样式）


# ---------------------------------------------------------------- 内容

class Brief(StrictModel):
    """阶段一「前期策划」创作简报：主题/世界观/画风 + 风格关键词包。

    M6 定位：AI 是协作者 —— brief 由创作者填写，作为分镜与生图的「不变量」输入。
    palette/lighting/character 即「风格关键词包」结构化字段（主色调/光影/人物特征），
    生图拼装时注入（见 app/core/styles.py compose_image_prompt）。
    bgm_hint 为 BGM 节奏/卡点描述（情绪锚点前置，M8 卡点剪辑的输入）。
    """

    theme: str = ""             # 主题
    worldview: str = ""         # 世界观
    art_style: str = ""         # 整体画风（如实写末日 / 漫画风）
    duration: str = ""          # 时长（如 "60s"）
    platform: str = ""          # 发布平台
    protagonist: str = ""       # 主角形象
    plot: str = ""              # 核心剧情
    emotion: str = ""           # 情绪基调
    palette: str = ""           # 主色调（风格关键词包）
    lighting: str = ""          # 光影指令（如 "volumetric lighting from upper left"）
    character: str = ""         # 人物核心特征（风格关键词包）
    bgm_hint: str = ""          # BGM 节奏/卡点描述（重拍/高潮/骤停）


class ReferenceImage(StrictModel):
    """参考图（多角度图组：正面/侧面/45° 仰视，按 angle/role 标签区分）。

    支持图生图的渠道作为固定 ControlNet 式输入注入生图（M6-7.4）；不支持的渠道
    降级为「仅风格关键词包」模式（见 ImageProvider.supports_reference_image）。
    """

    id: str
    path: str                   # 相对项目目录，如 assets/ref_1.png
    angle: str = ""             # 拍摄角度标签（正面/侧面/45°仰视…）
    role: str = ""              # 用途/角色标签（主角/场景/道具…）


class Scene(StrictModel):
    """一个分镜：一段口播文案 + 一张图（候选多张）+ 一个运镜。

    M6 扩展（阶段一/二）：shot_size 景别、planned_duration_ms 规划时长（对齐 BGM
    节奏）、sfx 音效描述、image_candidates 候选图资产 id 列表（人审选中的
    image_asset_id 供时间线使用）、end_image_asset_id 为 M7「首尾帧」预埋字段。
    """

    scene_id: str
    narration: str = ""
    visual: str = ""            # 画面描述（中文，人看）
    image_prompt: str = ""      # 生图提示词（英文，可变「主体+动作+场景」模块）
    image_asset_id: str | None = None   # 人审选中的候选图（时间线用）
    image_candidates: list[str] = Field(default_factory=list)   # 候选图资产 id（gen_assets 写入）
    end_image_asset_id: str | None = None   # M7 首尾帧：镜头结束帧（阶段三动态化输入）
    motion: MotionKind = "none"
    shot_size: ShotSize = ""    # 景别
    planned_duration_ms: int | None = None   # 规划时长（预估；时间线以配音实测为准）
    sfx: str = ""               # 音效描述（阶段四；素材自备 assets/sfx/）
    start_ms: int = 0           # 时间线全局起点（app/timeline/builder.py 组装时写入）
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
    duration_ms: int | None = None   # 组装后 clip 级实际时长；None 时导出取素材自身时长


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
    reference_asset_id: str | None = None   # M6-7.6 图生图精修：生成时使用的参考图资产 id
    cost: float = 0.0
    status: TaskStatus = "pending"


class ExportInfo(StrictModel):
    format: str = "jianying"
    path: str | None = None     # 相对项目目录，如 exports/proj_001_draft
    jianying_version: str | None = None   # 钉住的验证版本
    status: TaskStatus = "pending"


class PipelineError(StrictModel):
    """pipeline 节点失败摘要（app/core/state.py 写入，status 命令可读）。

    kind/hint 为 M3-4.3 扩展：错误分类 + 修复动作（app/core/errors.py classify 得出），
    老项目 JSON 无此字段时默认空字符串，向后兼容。
    """

    node: str
    error: str
    kind: str = ""
    hint: str = ""


# ---------------------------------------------------------------- 顶层

class Project(StrictModel):
    project_id: str
    title: str = ""
    schema_version: str = "0.2"   # M6-7.1：0.1 → 0.2（增量字段，旧 JSON 直接加载，load 时内存升版）
    config: ProjectConfig = Field(default_factory=ProjectConfig)
    brief: Brief | None = None    # M6-7.1 阶段一策划简报（None = 旧项目/未策划）
    reference_images: list[ReferenceImage] = Field(default_factory=list)   # M6-7.1 参考图组
    pipeline: dict[str, TaskStatus] = Field(
        default_factory=lambda: {node: "pending" for node in PIPELINE_NODES}
    )
    errors: list[PipelineError] = Field(default_factory=list)
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

        # 配音 / 场景图 / 候选图 / 首尾帧引用存在的 asset
        if self.voiceover.asset_id and self.voiceover.asset_id not in asset_ids:
            raise ValueError(f"voiceover 引用了不存在的 asset_id: {self.voiceover.asset_id}")
        for scene in self.scenes:
            if scene.image_asset_id and scene.image_asset_id not in asset_ids:
                raise ValueError(f"scene {scene.scene_id} 引用了不存在的 asset_id: {scene.image_asset_id}")
            for cid in scene.image_candidates:
                if cid not in asset_ids:
                    raise ValueError(
                        f"scene {scene.scene_id} 的候选图引用了不存在的 asset_id: {cid}"
                    )
            if scene.end_image_asset_id and scene.end_image_asset_id not in asset_ids:
                raise ValueError(
                    f"scene {scene.scene_id} 的首尾帧引用了不存在的 asset_id: {scene.end_image_asset_id}"
                )

        # 参考图 id 唯一
        ref_ids = [r.id for r in self.reference_images]
        if len(ref_ids) != len(set(ref_ids)):
            dup = sorted({i for i in ref_ids if ref_ids.count(i) > 1})
            raise ValueError(f"reference_images id 重复: {dup}")

        return self
