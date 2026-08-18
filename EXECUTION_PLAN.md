# AVPO 最终执行方案 v1.0

> AI 视频工作流操作系统 —— 基于原始方案的可行性研究与优化后定稿。
> 日期：2026-08-18　|　形态：个人开发 + 研究（MVP 先行）

---

## 0. 可行性结论（30 秒版）

**可行。** MVP（营销口播视频：文案 → 分镜 → 配音 → 生图 → 字幕 → 剪映草稿）是个人开发者 4~6 周内可交付的范围。核心难度不在 AI（全部走 API），而在**剪映草稿格式**——该风险已被社区开源项目消解（见 §6）。

| 环节 | 风险 | 说明 |
|---|---|---|
| project.json 数据层 | 🟢 低 | 纯工程,用 pydantic 校验 |
| 配音 TTS | 🟢 低 | edge-tts 免费,需 M1 首周实测验证 |
| 生图（SiliconFlow FLUX）| 🟢 低 | 国内直连,~$0.005~0.03/张 |
| 文案/分镜 LLM（DeepSeek）| 🟢 低 | ¥2/1M in,成本可忽略 |
| 剪映草稿生成 | 🟡 中 | **6.0+ 加密** + 版本漂移 → 对策见 §6 |
| 运镜动画（Ken Burns）| 🟡 中 | 用剪映预设动画,不做复杂关键帧 |
| 角色一致性 | 🔴 高 | **MVP 砍掉**,阶段 1 只做参考图注入 |
| AI 视频生成（Sora/Kling 类）| 🔴 高 | **MVP 砍掉**（成本高一个数量级,流程也不需要）|

---

## 1. 我做的 7 个关键优化决策

1. **砍掉 AI 视频生成。** 口播视频 = 静态图 + 运镜动画 + 配音 + 字幕,完全不需要生成视频。成本从 Wan2.2 的 ~$0.29/段 降到 FLUX 的 ~$0.03/张,草稿结构大幅简化,周期缩短 ~2 周。视频生成推迟到阶段 1 评估。

2. **编排先代码后引擎。** MVP 不用 N8N/Dify——它们是多服务部署,单人运维负担大,且断点续跑、人工审核节点都要二次开发。改用 **Python 状态机 + project.json 内任务状态字段**,天然支持失败重试与断点续跑。LangGraph 留到阶段 1,当分支逻辑真的变复杂时再迁移。

3. **剪映导出不自己逆向格式,直接用 pyJianYingDraft**（生成明文草稿,剪映 6+ 可直接打开）。模板法兜底 + jy-draftc 解密手工模板。钉版本 + 每次导出后打开验证（§6）。

4. **存储降级。** 阶段 1 也先用 SQLite + 本地文件系统,PostgreSQL/MinIO 推迟到多人协作阶段。单人项目上 PG+MinIO 是纯运维负债。

5. **数据模型裁剪 + 补字段。** 口播场景 Scene 即 Shot,两级合并为一级；时间线固定三轨（video / voiceover / subtitle）,不做通用 tracks 数组。补上原方案缺失的：每任务 `status`（断点续跑）、`seed` + `prompt_hash`（可复现 + 缓存去重）、`cost`（成本记录）、`duration_ms`（剪辑必需）（§3）。

6. **字幕直接来自 TTS 时间戳。** 配音时拿 word boundaries 生成字幕轨道,MVP 不需要 Whisper。Whisper 只解决"用户自带音频"的场景,留到阶段 1。

7. **版本历史用 git 实现,零代码。** 原方案"每次生成/修改/导出都记录版本"——在 `data/` 下 `git init`,提交即快照,免费获得 diff/回溯/分支。自定义版本系统是典型的过度设计。

---

## 2. 技术栈（定版,全部国内直连）

| 用途 | 选型 | 备注 |
|---|---|---|
| 语言 | Python 3.11 | |
| 数据模型 | pydantic v2 | schema 即校验,一处定义处处复用 |
| 配音 | edge-tts（免费）| **M1 第一天验证**；失效则切火山引擎豆包 TTS（~¥0.3/千字）|
| 生图 | SiliconFlow API：FLUX.1-schnell / dev | $0.005~0.03/张,国内直连 |
| 文案/分镜/提示词转换 | SiliconFlow：DeepSeek | ¥2/1M in,量大无压力 |
| 剪映草稿生成 | pyJianYingDraft（pip）| 音视频/字幕/动画/转场全支持 |
| 剪映模板解密 | jy-draftc（可选）| C++ 单文件,模板兜底路线用 |
| CLI | typer | MVP 不做 UI |
| 音频时长读取 | mutagen | 口播时长由实际音频文件算出 |

**依赖清单：** `pydantic>=2`、`edge-tts`、`openai`（SiliconFlow 兼容 OpenAI 协议）、`pyJianYingDraft`、`typer`、`mutagen`、`requests`

---

## 3. 数据模型 v0.1（裁剪版）

口播视频的原子单元是 **scene**（一段文案 + 一张图 + 一个运镜 + 一段字幕）。原方案 Scene→Shot 两级在这里没有信息量,合并。

```json
{
  "project_id": "proj_001",
  "title": "AI 产品口播",
  "schema_version": "0.1",
  "config": {
    "tts":    { "engine": "edge-tts", "voice": "zh-CN-YunxiNeural", "rate": "+0%" },
    "image":  { "model": "black-forest-labs/FLUX.1-schnell", "size": "16:9" },
    "llm":    { "model": "deepseek-chat" }
  },
  "scenes": [
    {
      "scene_id": "s1",
      "narration": "AI 正在改变内容创作的方式…",
      "visual":    "都市夜景, 数字光效, 电影感",
      "image_prompt": "cinematic city night, digital light, ...",
      "image_asset_id": "a_img_1",
      "motion": "zoom_in_slow",
      "status": "done",
      "cost": { "image": 0.02, "llm": 0.001 }
    }
  ],
  "voiceover": {
    "asset_id": "a_vo",
    "status": "done",
    "duration_ms": 8320
  },
  "subtitles": [
    { "scene_id": "s1", "start_ms": 0, "end_ms": 1450, "text": "AI 正在改变" }
  ],
  "timeline": {
    "video":     [ { "asset_id": "a_img_1", "start_ms": 0, "duration_ms": 8320, "motion": "zoom_in_slow" } ],
    "voiceover": [ { "asset_id": "a_vo", "offset_ms": 0 } ]
  },
  "assets": {
    "a_img_1": {
      "type": "image", "path": "assets/s1_v1.png",
      "model": "FLUX.1-schnell", "prompt_hash": "e4a2...", "seed": 1234,
      "cost": 0.02, "status": "done"
    },
    "a_vo": { "type": "audio", "path": "assets/vo_v1.mp3", "model": "edge-tts", "status": "done" }
  },
  "export": {
    "format": "jianying", "path": "exports/proj_001_draft",
    "jianying_version": "钉住的验证版本", "status": "ready"
  }
}
```

**关键设计（对原方案的修正）：**
- 每任务带 `status`（pending/running/done/failed）→ 断点续跑 = 启动时扫 failed/pending 重跑,状态机就这一条规则。
- `prompt_hash` + `seed` → 相同 prompt 不重复调 API（缓存层,§7 成本控制）；seed 固定可复现。
- `cost` 记在每个任务上 → 成本仪表盘免费获得。
- 字幕与配音共用一份时间戳来源,不会错位。
- **字数 → 时长估算**仅用于分镜预览；真实时长以 TTS 产出的音频为准（mutagen 读 duration）,随后 scene 的 duration 与时间线全部基于它计算。

---

## 4. 仓库结构

```
AVPO/
├── app/
│   ├── core/         # pydantic schema、project.json 读写、git 提交封装
│   ├── director/     # AI 导演助手：文案 → 分镜（DeepSeek,输出带人工确认）
│   ├── tts/          # 配音 + word 时间戳 → 字幕
│   ├── vision/       # FLUX 生图 + prompt_hash 缓存
│   ├── timeline/     # 时长计算、运镜分配、时间线组装
│   ├── export/       # 剪映草稿生成（pyJianYingDraft 封装）
│   └── cli.py        # avpo new / direct / gen / export / status
├── data/             # git init 于此:版本历史免费
│   └── projects/<pid>/   # project.json + assets/ + exports/
├── templates/        # 手工模板草稿（jy-draftc 解密后的明文,兜底用）
├── tests/            # golden sample 回归
└── requirements.txt
```

MVP 无 UI,CLI 够用且快。阶段 1 再叠 Streamlit 工作台。

---

## 5. 实现顺序与里程碑（4 周,含验收标准）

| 里程碑 | 内容 | 验收标准（exit criteria） |
|---|---|---|
| **M0 · 第 1 周** | 数据层 + 剪映导出 PoC | 手工写一个最小 project.json → `avpo export` → 生成的草稿在剪映**成功打开**,可见 1 段视频 + 1 段音频 + 字幕 |
| **M1 · 第 2 周** | 素材链路 | 文案 → edge-tts 出配音（含时间戳）→ FLUX 出图 → 自动归档进 assets/ 并更新 project.json。**edge-tts 稳定性实测**（3 天内连续跑 20 条不挂）；不挂 → 定版,挂 → 切豆包 TTS |
| **M2 · 第 3 周** | 端到端管线 | 一句话创意 → 分镜草稿（DeepSeek）→ **人工确认一次** → 素材全自动 → 时间线 → 剪映草稿。目标：60 秒口播,从文案到可打开工程 **≤ 5 分钟** |
| **M3 · 第 4 周** | 健壮性 | 失败重试、断点续跑（kill 进程后重跑不重复调 API）、生图缓存、成本统计、git 回溯。3 个不同风格模板各产 3 条视频回归。**导出成功率 ≥ 90%** |

M0 的 PoC 是最高优先级——它验证整个方案里唯一的"不确定技术",如果剪映打不开,第一时间暴露,而不是等两周后。

---

## 6. 剪映导出策略（本方案最关键的一节）

**外部事实（研究结论）：**
- 剪映 **6.0 起 `draft_content.json` 加密**（AES 载荷,仅中国版；国际版 CapCut 不加密）。
- 社区两条现成路线,都不需要自己逆向：
  - **生成**：pyJianYingDraft —— Python 库,支持视频/图片/音频/字幕/动画/转场/滤镜/模板替换,`pip install` 即可。**生成的是明文草稿,剪映 6+ 可以直接打开**（打开后剪映自己加密）。
  - **解密**：jy-draftc —— 调用剪映自带 `videoeditor.dll` 的 EncryptUtils 做解密/回加密,支持 10.3~11.1。

**导出实现（路线 A,主）：**
1. pyJianYingDraft 按 project.json 生成草稿：素材（图+音频）、字幕轨道（.srt 导入或文本 API）、入场动画。
2. 素材文件拷贝进草稿目录,引用相对路径,`zip` 打包 → 完整可迁移。
3. 人工导入：草稿目录放进剪映草稿文件夹即出现（不做自动导出——剪映 7+ 隐藏了导出控件,uiautomation 不可靠）。

**兜底（路线 B）：**
复杂效果（花字、气泡、特殊运镜）pyJianYingDraft 不支持时,用 jy-draftc 解密一个**手工做好的模板草稿**,pyJianYingDraft 模板模式替换素材与文本,再回加密。成本:模板解密只做一次。

**守则：**
- 钉住一个剪映版本做验证（记录 `jianying_version` 在 export 里）。
- **每次导出后必须人工打开验证一次**并记录结果——这是 M0/M3 验收的核心动作。
- 剪映升级后格式漂移 → 优先等 pyJianYingDraft 社区跟进,而非自己修。

---

## 7. 成本估算

| 项目 | 单价 | 每条 60s 口播用量 | 小计 |
|---|---|---|---|
| 生图 FLUX.1-schnell | ~¥0.04/张 | 8~15 张（含废片重抽）| ¥0.4~0.7 |
| DeepSeek 分镜/文案/提示词 | ~¥0.001/千字 | 全文 <5 万字 | <¥0.1 |
| edge-tts 配音 | 免费 | — | ¥0 |
| 合计 | | | **< ¥1/条** |

月产 100 条 < ¥100。**生图是唯一大头**,故缓存（prompt_hash 去重）+ 默认 schnell + 失败重抽上限（每 scene 最多 3 张）写入系统,不是建议。

---

## 8. 验证体系（防"能跑但不可信"）

1. **schema 校验**：所有 project.json 读写过 pydantic,改错字段当场报错。
2. **golden sample 回归**：固定输入 → 固定输出逐字段比对,防止模型/格式悄悄变化。
3. **剪映打开验证记录**：每里程碑手动打开导出草稿,结果写入 `tests/opened_log.md`。
4. **成本断言**：每条视频结束后打印实际成本,超阈值报警。
5. **断点测试**：kill -9 后重跑,断言无重复 API 调用（缓存命中日志）。

---

## 9. 风险清单

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| 剪映格式版本漂移 | 高 | 高 | 钉版本；依赖 pyJianYingDraft 社区；模板法兜底；打开验证 |
| 6.0+ 草稿加密 | 必然 | 中 | 明文生成路径可读；jy-draftc 仅用于模板解密 |
| edge-tts 失效/限流 | 中 | 中 | M1 先验证；fallback 豆包 TTS（接口差异封装在 tts 模块内）|
| 生图质量不稳定 | 中 | 中 | 每 scene 抽 2~3 张备选；分镜级人工确认一次 |
| 角色一致性做不好 | 高 | 低（MVP 无此需求）| 已砍；阶段 1 参考图注入,承诺"改善"而非"保证" |
| SiliconFlow 实名认证 | 必然 | 低 | 用户本人持有中国身份证,注册时一次搞定 |

---

## 10. 阶段 1 演进（不展开,MVP 之后再定细节）

- Streamlit 工作台（可视化项目/素材/时间线）
- 角色 Bible + 参考图注入（IP-Adapter / FLUX Redux）,目标"明显改善"非"100%"
- Whisper 本地字幕（用户自带音频场景）
- 迁移 SQLite；PostgreSQL/MinIO 仍不做,除非出现真多人协作
- PR XML / DaVinci XML：**优先级低于剪映**——先服务最大用户群,且 XML 方案需重新验证
- 自动导出（剪映限制解除后）+ 反向同步精修结果

---

## 11. 需要你确认的 3 个假设

1. **剪映专业版已装在 Windows 上**,且你接受钉在一个版本做验证（不随意升级）。
2. **SiliconFlow 账号已实名**（2026 年 5 月起强制,仅中国身份证）。
3. **MVP 不做 AI 视频生成**。若坚持要,周期 +2 周、成本 +30 倍量级——我强烈建议按本方案先跑通,再做加法。

---

## 参考资料

- [pyJianYingDraft（剪映草稿生成库,含模板模式）](https://github.com/GuanYixuan/pyJianYingDraft)
- [jy-draftc（剪映草稿解密/回加密）](https://github.com/wenshui330/jy-draftc)
- [jy-draft-port（解密 GUI 版）](https://github.com/zzz1999/jy-draft-port)
- [剪映 6.0+ 草稿加密说明（capcut-cli 决策文档）](https://github.com/renezander030/capcut-cli/blob/master/docs/jianying-encryption.md)
- [SiliconFlow 模型/定价](https://vantaige.io/zh/ai-tool/siliconflow)
