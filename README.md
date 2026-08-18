# AVPO —— AI 视频工作流操作系统

> 连接剧本、AI 生成模型与剪映的智能中间层，消除工具间搬运与对齐。
> 一句话创意 → 分镜 → 配音 → 生图 → 字幕 → 剪映草稿，全自动。

## MVP 工作流（营销口播视频）

```
一句话创意
   │ DeepSeek：文案润色 + 拆解分镜
   ▼
分镜草稿 ──── 人工确认一次
   │ edge-tts：配音（含 word 时间戳）
   │ SiliconFlow FLUX：每场景生图（2~3 张备选）
   ▼
素材自动归档 → project.json 更新（prompt_hash 去重缓存，seed 可复现）
   │ 字幕轨道 = TTS 时间戳，零成本对齐
   ▼
时间线组装（时长以实际音频为准）
   │ pyJianYingDraft 生成明文草稿
   ▼
剪映草稿目录/zip → 剪映打开精修 → 成片
```

## 快速开始

```bash
# 0. 环境：Python >= 3.11
py -3.11 -m venv .venv
.venv/Scripts/activate            # Windows Git Bash

# 1. 安装
pip install -e .                  # 安装运行依赖 + avpo 命令

# 2. 配置（可选，素材链路 M1 起需要）
cp .env.example .env              # 填入 SILICONFLOW_API_KEY

# 3. 使用
avpo new proj_001 --title "AI 产品口播"
avpo status proj_001
```

## 目录结构

```
AVPO/
├── app/
│   ├── core/         # pydantic schema、project.json 读写、git 提交封装
│   ├── director/     # AI 导演助手：文案 → 分镜（DeepSeek）
│   ├── tts/          # 配音 + word 时间戳 → 字幕
│   ├── vision/       # FLUX 生图 + prompt_hash 缓存
│   ├── timeline/     # 时长计算、运镜分配、时间线组装
│   ├── export/       # 剪映草稿生成（pyJianYingDraft 封装）
│   └── cli.py        # avpo new / status / run ...
├── data/             # 项目数据（独立 git 仓库，每次保存自动提交 = 免费版本历史）
├── templates/        # 手工模板草稿（jy-draftc 兜底路线用）
└── tests/            # golden sample 回归 + schema/存储/CLI 测试
```

## 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M0 第 1 周 | 数据层 + 剪映导出 PoC | 剪映成功打开生成草稿（视频+音频+字幕）|
| M1 第 2 周 | 素材链路（TTS/生图/归档）| edge-tts 20 条稳定性实测通过 |
| M2 第 3 周 | 端到端管线 | 60s 口播文案→草稿 ≤5 分钟 |
| M3 第 4 周 | 健壮性（重试/断点/缓存/成本）| 导出成功率 ≥90%，3 模板回归 |

完整方案见 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)、[SUMMARY.md](SUMMARY.md)。

## 开发

```bash
pip install -e .[dev]
pytest                            # 测试临时文件走 F:/tmp/avpo-pytest（见 conftest.py）
```
