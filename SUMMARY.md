# AVPO 方案与技术总览

> 一句话：AI 视频工作流操作系统 —— 连接剧本、AI 生成模型与剪映的智能中间层,消除工具间搬运与对齐。

## 一、MVP 工作流（营销口播视频）

```
一句话创意
   │ DeepSeek:文案润色 + 拆解分镜
   ▼
分镜草稿 ──── 人工确认一次
   │ edge-tts:配音(含 word 时间戳)
   │ SiliconFlow FLUX:每场景生图(2~3 张备选)
   ▼
素材自动归档 → project.json 更新(prompt_hash 去重缓存,seed 可复现)
   │ 字幕轨道 = TTS 时间戳,零成本对齐
   ▼
时间线组装(时长以实际音频为准)
   │ pyJianYingDraft 生成明文草稿
   ▼
剪映草稿目录/zip → 剪映打开精修 → 成片
```

目标：文案到可打开草稿 ≤ 5 分钟,人工搬运/对齐减少 80%+。

## 二、架构分层（代码内体现,不跑独立服务）

| 层 | 模块 | 职责 |
|---|---|---|
| 用户层 | cli.py | MVP 无 UI,CLI 驱动;阶段 1 加 Streamlit |
| 编排层 | core 状态机 | 任务 status(pending/running/done/failed)驱动,断点续跑 |
| 能力层 | director/ tts/ vision/ | 4 个外部 API 的统一封装:LLM、配音、生图、时长 |
| 上下文层 | core project.py | project.json 唯一真理源 + git 版本历史 |
| 导出层 | export/ | 剪映草稿生成(社区库),钉版本 + 打开验证 |

## 三、技术栈

| 用途 | 选型 | 备注 |
|---|---|---|
| 语言/数据 | Python 3.11 + pydantic v2 | schema 即校验 |
| 版本管理 | git(data/ 目录) | 免费快照/回溯 |
| LLM | DeepSeek(经 SiliconFlow) | ¥2/1M in,分镜/提示词转换 |
| 生图 | SiliconFlow FLUX.1-schnell/dev | $0.005~0.03/张,国内直连 |
| 配音 | edge-tts | 免费;失效则切豆包 TTS(模块内封装) |
| 剪辑导出 | pyJianYingDraft(pip) | 明文草稿,剪映 6+ 可读 |
| 模板兜底 | jy-draftc | 解密手工模板处理复杂效果 |
| 辅助 | typer / mutagen / openai SDK | CLI / 音频时长 / API 协议 |

**特点**：纯 Python 胶水层,无自建服务、无自训练模型,全部依赖国内直连 API → 单人可行。

## 四、里程碑（4 周）

| 阶段 | 内容 | 验收 |
|---|---|---|
| M0 第 1 周 | 数据层 + 剪映导出 PoC | 剪映成功打开生成草稿(视频+音频+字幕) |
| M1 第 2 周 | 素材链路(TTS/生图/归档) | edge-tts 20 条稳定性实测通过 |
| M2 第 3 周 | 端到端管线 | 60s 口播文案→草稿 ≤5 分钟 |
| M3 第 4 周 | 健壮性(重试/断点/缓存/成本) | 导出成功率 ≥90%,3 模板回归 |

## 五、成本

- 每条 60s 口播 < ¥1(生图是大头 ~¥0.4-0.7,DeepSeek <¥0.1,配音免费)
- 月产 100 条 < ¥100;生图缓存 + 默认 schnell + 重抽上限 3 张已内置

## 六、关键风险与对策

| 风险 | 对策 |
|---|---|
| 剪映格式版本漂移/6.0+ 加密 | 钉版本;pyJianYingDraft 社区跟进;jy-draftc 模板兜底;每次打开验证 |
| edge-tts 失效 | M1 首周实测;fallback 豆包 TTS |
| 生图质量不稳 | 每场景 2~3 张备选;分镜级人工确认 |
| 角色一致性 | MVP 砍掉;阶段 1 只做参考图注入,承诺"改善"非"保证" |

## 七、个人所需基础（搭配 Claude）

- **必须**：Python 基本功(能审 schema)、JSON 建模思维、API 集成套路(.env/重试/排查)、git 基本功(diff 审查/revert 兜底)、测试与验收习惯(golden sample + 剪映打开验证)
- **加分**：读开源代码(pyJianYingDraft 跟进)、CLI 调试、基础逆向思维(jy-draftc 机制)
- **与 Claude 协作**：小步任务 + 明确验收标准;Claude 产出默认 review 后合并

## 八、阶段 1 演进

Streamlit 工作台 → 角色 Bible + 参考图注入 → Whisper 字幕(自带音频场景) → SQLite → 成本仪表盘 → PR/DaVinci XML(优先级低于剪映)

## 参考资料

- [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) · [jy-draftc](https://github.com/wenshui330/jy-draftc) · [jy-draft-port](https://github.com/zzz1999/jy-draft-port) · [剪映加密说明](https://github.com/renezander030/capcut-cli/blob/master/docs/jianying-encryption.md) · [SiliconFlow 定价](https://vantaige.io/zh/ai-tool/siliconflow)
