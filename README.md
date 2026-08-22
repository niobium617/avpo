# AVPO —— AI 视频工作流操作系统

> 连接剧本、AI 生成模型与剪映的智能中间层，消除工具间搬运与对齐。
> 一句话创意 → 分镜 → 配音 → 生图 → 字幕 → 剪映草稿，全自动。

**当前状态：MVP 可交付（M0/M1/M2/M3 全部完成，退出条件达标）**

## MVP 工作流（营销口播视频）

```
一句话创意
   │ LLM（qwen-plus / DeepSeek-V3）：拆解分镜（强制 JSON，narration 逐字校验）
   ▼
分镜草稿 ──── 人工确认一次（avpo run --yes 可跳过）
   │ edge-tts：配音（word 时间戳）+ 生图（通义万相 / FLUX，prompt_hash 缓存）
   ▼
素材自动归档 → project.json 更新（状态与产物同一次 git 提交 = 断点续跑基础）
   │ 字幕 = TTS 时间戳聚合，零成本对齐
   ▼
时间线组装（场景时长 = 配音实际时长，mutagen 读取）
   │ pyJianYingDraft 生成明文草稿（字幕样式按风格模板 + 可选 BGM）
   ▼
剪映草稿目录/zip → 剪映打开精修 → 成片
```

## 快速开始

```bash
# 0. 环境：Python >= 3.11（Windows 11 实测，剪映 9.7.1 钉版）
py -3.11 -m venv .venv
.venv/Scripts/activate            # Windows Git Bash

# 1. 安装
pip install -e .                  # 安装运行依赖 + avpo 命令

# 2. 配置渠道（素材链路需要）
cp .env.example .env              # 填入 DASHSCOPE_API_KEY（千问）或 SILICONFLOW_API_KEY

# 3. 使用
avpo new proj_001 --title "AI 产品口播" --style fast_talk
avpo run proj_001 --text "口播文案全文..." --yes      # 一键全自动：分镜→素材→时间线→导出
avpo status proj_001                                   # 状态树（节点/场景/素材）
avpo cost proj_001                                     # 成本明细 + 超预算告警
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `avpo new <pid> [--style 模板] [--provider 渠道]` | 建项目；风格模板 default/fast_talk/emotional/explainer |
| `avpo run <pid> --text "..." [--yes]` | 一键流水线：direct→confirm→gen_assets→timeline→export |
| `avpo direct <pid> --text "..."` / `gen-assets` / `timeline` / `export` | 单节点执行（已 done 节点自动跳过 = 断点续跑） |
| `avpo status <pid>` | 打印状态树：节点状态、场景、素材、成本 |
| `avpo cost <pid> [--budget 5]` | 成本汇总（LLM + 生图，纯 SUM 不二次对账），超预算告警 |
| `avpo new --provider siliconflow` | 换渠道（siliconflow FLUX / dashscope 通义万相） |

## 风格模板（M3）

`templates/*.json`，每条 = 运镜指导（注入分镜 LLM）+ 字幕样式 + 可选 BGM：

| 模板 | 适用 | 特点 |
|---|---|---|
| `fast_talk` | 带货/资讯 | 运镜频繁切换，字幕醒目低位 |
| `emotional` | 故事/情感 | 慢运镜 + BGM（放 `assets/bgm.mp3` 即生效，缺失自动降级） |
| `explainer` | 知识/解说 | 字幕大字号偏上，运镜克制 |

## 成本

- 记账：每次 API 调用成功即写 cost 到场景/资产（LLM 分镜成本均摊到场景，生图记
  场景 + 资产），`avpo cost` 只是 SUM，不做二次对账。
- 参考单价：通义万相 wanx2.1-t2i-turbo ≈ ¥0.16/张；FLUX.1-schnell ≈ ¥0.02/张；
  qwen-plus ≈ ¥0.002/千 token；edge-tts 配音免费。
- 预算：默认 ¥5/项目，`avpo cost --budget 10` 自定义；超出显示红色告警。

## 健壮性（M3）

- **断点续跑**：状态与产物同一次 git 提交落盘；kill -9 后重跑，配音/生图走缓存
  （sidecar + prompt_hash），0 次重复 API 调用；改文案缓存自动失效。
- **错误分类**：API（余额/限流/网络）vs 格式校验 vs 剪映草稿，失败时打印修复动作
  （`[分类] 错误详情（修复: ...）`）。
- **失败重试**：Transient 重试 ×3（指数退避），Fatal 不重试；下游节点在上游成功后
  自动重置 pending 重跑。

## 架构

```
┌────────── app/cli.py（typer）──────────┐
│  new / run / status / cost / 单节点    │
└──────┬─────────────────────────────────┘
       │ app/core/pipeline.py（节点编排，下游失效自动重跑）
┌──────▼──────┐ ┌───────────┐ ┌─────────────┐
│ state.py    │ │ schema.py │ │ project.py  │
│ 状态机      │ │ pydantic  │ │ 原子写+git  │
│ 重试/落盘   │ │ 单一真相源 │ │ 提交=历史   │
└──────┬──────┘ └───────────┘ └─────────────┘
       │ 子模块（各节点 fn）
┌──────▼──────────────────────────────────┐
│ director/  文案→分镜（LLM 强制 JSON）    │
│ tts/       edge-tts 配音 + 字幕 + 缓存   │
│ vision/    生图（多渠道）+ prompt_hash   │
│ timeline/  全局时间轴（mutagen 实长）    │
│ export/    剪映草稿（pyJianYingDraft）   │
│ core/      cost 账本 / errors 分类 /     │
│            styles 模板注册表             │
└──────────────────────────────────────────┘
        │ 产物
        ▼
data/projects/<pid>/project.json + assets/ + exports/<pid>_draft/
```

## 目录结构

```
AVPO/
├── app/
│   ├── core/         # schema、store、状态机、pipeline、成本、错误分类、风格模板
│   ├── director/     # AI 导演助手：文案 → 分镜（LLM 强制 JSON + 逐字校验）
│   ├── tts/          # edge-tts 配音 + word 时间戳 + sidecar 缓存 → 字幕
│   ├── vision/       # 生图（dashscope 千问 / siliconflow FLUX）+ prompt_hash 缓存
│   ├── timeline/     # 时间线组装（场景时长 = 配音实际时长）
│   ├── export/       # 剪映草稿生成（pyJianYingDraft 封装，模板字幕样式 + BGM）
│   └── cli.py        # avpo 命令入口
├── templates/        # 风格模板 JSON（fast_talk / emotional / explainer）
├── data/             # 项目数据（独立 git 仓库，每次保存自动提交 = 免费版本历史）
└── tests/            # 全链路回归（含 3 模板 × 3 条）、断点续跑、成本、错误分类测试
```

## 里程碑

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| M0 | 数据层 + 剪映导出 PoC | 剪映成功打开生成草稿（三轨） | ✅ |
| M1 | 素材链路（TTS/生图/归档/分镜）| 真实文案自动产出配音+字幕+图 | ✅ |
| M2 | 端到端管线（一键 run/并发提速）| 60s 口播全自动 ≤5min（实测 1.8min）| ✅ |
| M3 | 健壮性（断点/成本/错误/模板）| 断点 0 重复调用、成本告警、3 模板回归、打开成功率 3/3 | ✅ |

完整方案见 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)、[SUMMARY.md](SUMMARY.md)、[docs/PROGRESS.md](docs/PROGRESS.md)。

## 开发

```bash
pip install -e .[dev]
pytest                            # 测试临时文件走 F:/tmp/avpo-pytest（见 conftest.py）
pytest -m live                    # 真实链路（调用外部 API，按 .env 渠道）
```

## 已知限制

- 运镜：pan_left/pan_right 暂无剪映入场动画枚举，降级为静态（`none`），不影响成片；
- BGM：需自备音频文件（`assets/bgm.mp3`），模板不携带素材；
- 渠道 key 只放 `.env`，不入库（公开仓库规范）。
