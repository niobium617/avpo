"""M3-4.2 成本统计测试：汇总明细 + 预算告警（纯 SUM，不二次对账）。"""

from app.core.cost import summarize
from app.core.schema import Project


def test_summarize_sample_project(sample_project):
    """汇总 = Σ scene.cost（llm+image），资产侧不与场景侧双计。"""
    s = summarize(sample_project)
    # s1: llm=0.001 + image=0.02；s2 无成本记录
    assert s.total == 0.021
    assert s.by_scene["s1"] == {"image": 0.02, "llm": 0.001}
    assert s.by_scene["s2"] == {}
    # 资产侧明细只列出有成本的资产（图），配音/零成本资产不进明细
    assert s.by_asset == {"a_img_1": 0.02, "a_img_2": 0.02}


def test_budget_alert(sample_project):
    s = summarize(sample_project, budget=0.01)
    assert s.over_budget is True
    s2 = summarize(sample_project, budget=1.0)
    assert s2.over_budget is False


def test_default_budget_is_5(sample_project):
    from app.core.cost import DEFAULT_BUDGET

    assert DEFAULT_BUDGET == 5.0
    assert summarize(sample_project).budget == 5.0


def test_empty_project_zero_cost(store):
    project = Project(project_id="p_empty")
    store.create(project)
    s = summarize(store.load("p_empty"))
    assert s.total == 0.0
    assert s.by_scene == {} and s.by_asset == {}
    assert s.over_budget is False
