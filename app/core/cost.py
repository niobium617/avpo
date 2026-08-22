"""成本账本（IMPLEMENTATION_PLAN 4.2 / §5）。

账本规则（§5）：每次 API 调用成功即写 cost 到对应资产/场景，avpo cost 只是 SUM——
不做二次对账。记账点：
- LLM 分镜：direct 一次调用成本均摊到各场景 → scene.cost["llm"]；
- 生图：同笔成本记 scene.cost["image"]（场景）与 asset.cost（资产）→ 汇总只取
  场景侧，避免双计；
- 配音（edge-tts 免费）恒 0，无记录。

total = Σ scene.cost（llm + image）。by_asset 仅供资产级明细展示。
"""

from dataclasses import dataclass, field

from app.core.schema import Project

DEFAULT_BUDGET = 5.0      # 默认预算 ¥5/项目（IMPLEMENTATION_PLAN 4.2）


@dataclass
class CostSummary:
    total: float
    budget: float
    by_scene: dict[str, dict[str, float]] = field(default_factory=dict)
    by_asset: dict[str, float] = field(default_factory=dict)

    @property
    def over_budget(self) -> bool:
        return self.total > self.budget


def summarize(project: Project, budget: float = DEFAULT_BUDGET) -> CostSummary:
    """汇总项目成本：按场景（llm/image）+ 按资产（有成本的）。纯 SUM，不二次对账。"""
    by_scene = {s.scene_id: dict(s.cost) for s in project.scenes}
    by_asset = {aid: a.cost for aid, a in project.assets.items() if a.cost > 0}
    total = round(sum(v for costs in by_scene.values() for v in costs.values()), 6)
    return CostSummary(total=total, budget=budget, by_scene=by_scene, by_asset=by_asset)
