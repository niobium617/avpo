# AVPO 具体实现计划 v1.0

> 从零到 MVP 的落地细节：任务拆分、文件清单、接口设计、验收动作。
> 前置：`EXECUTION_PLAN.md`（总体方案）｜`SUMMARY.md`（速查）

---

## 0. 前置准备（半天,一次性）

```bash
# 0.1 环境
py -3.11 -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install pydantic edge-tts openai typer mutagen pyJianYingDraft pytest

# 0.2 SiliconFlow 开通（需实名）→ 充值 ¥10,拿 SK-xxx
#     验证 chat：curl 一次 deepseek-chat,验证 images：curl 一次 FLUX.1-schnell

# 0.3 验证 edge-tts 本机可用
edge-tts --text "你好,世界" --voice zh-CN-YunxiNeural --write-media /tmp/t.mp3

# 0.4 确认剪映专业版版本号（关于页）,记入 docs/versions.md;确认草稿目录：
#     %LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\

# 0.5 建骨架 + git
git init
echo ".env" > .gitignore   # SILICONFLOW_API_KEY 只放 .env,永不上传
mkdir -p app/{core,director,tts,vision,timeline,export} data/projects templates tests
```

**里程碑开始前的检查点（每次必做）**：剪映版本没变？pyJianYingDraft 能导入？edge-tts 还能发声？SiliconFlow 余额够？—— 外部依赖是唯一不可控变量,先验证再开工。

---

## 1. M0 数据层 + 剪映导出 PoC（第 1 周）

> 唯一目标：**剪映能打开我们程序生成的草稿**。全案最大风险,第一个消灭。

| # | 任务 | 产出文件 | 验收 |
|---|---|---|---|
| 0.1 | 骨架 + 依赖安装 | — | 见 §0 |
| 1.1 | pydantic schema（Project/Scene/Asset/Subtitle/Timeline/ExportConfig）,JSON roundtrip | `app/core/schema.py` | `pytest`：fixture JSON 读→写→读一致 |
| 1.2 | ProjectStore：load/save（原子写 tmp+rename）、git 提交封装 | `app/core/project.py` | 保存后 git log 有提交 |
| 1.3 | CLI 骨架：`new` 创建模板项目、`status` 打印状态树 | `app/cli.py` | `avpo new proj_001` 产出合法 JSON |
| 1.4 | **调研 pyJianYingDraft 实际 API**：读 README + 跑官方 demo。对比主仓 vs `aoguai/pyJianYingDraft`（支持高版本剪映）,以能跑通为准 | 调研笔记 `docs/jyd_notes.md` | demo 生成草稿 |
| 1.5 | 导出适配器：project.json → 草稿目录（视频+音频+字幕三轨）;素材拷贝进草稿、相对路径引用;zip 打包 | `app/export/jianying.py` | 目录结构正确 |
| 1.6 | golden 测试：手工 fixture 项目 → 生成 → 断言文件与 JSON 字段 | `tests/test_m0_export.py` | pytest 绿 |
| 1.7 | **打开验证**：草稿目录拷入剪映草稿路径 → 重启剪映 → 打开 | `tests/opened_log.md` | ✅ 三轨可见 |

**导出适配器核心（sketch,以 jyd_notes 实测为准）**：

```python
def export(project, out_dir) -> Path:
    draft = JianYingDraft(w=1920, h=1080, fps=30)
    for clip in project.timeline.video:          # 视频轨
        v = draft.add_video(asset_path, start=clip.start_s, duration=clip.duration_s)
        v.add_animation(name=ANIMATIONS[clip.motion])   # 运镜:剪映预设动画
    for c in project.timeline.voiceover:          # 音频轨
        draft.add_audio(asset_path, start=c.offset_s)
    for sub in project.subtitles:                 # 字幕轨
        draft.add_text(sub.text, start=sub.start_s, duration=sub.duration_s)
    draft.export(str(out_dir))                    # → 草稿目录(含 draft_content.json)
    return out_dir   # 素材已拷贝入内,相对路径引用;zip 可选
```

**M0 退出条件**：剪映成功打开草稿,看到 1 图 + 1 配音 + 字幕,且修改后能保存。

---

## 2. M1 素材链路（第 2 周）

> 目标：文案 → 配音(含时间戳) + 生图 → 自动归档,JSON 完整。

| # | 任务 | 产出文件 | 验收 |
|---|---|---|---|
| 2.1 | **edge-tts 稳定性实测**：连续 20 条(不同长度/标点),记录失败率与时间戳质量 | `tests/edge_tts_log.md` | 失败率 0 → 定版;否则切豆包 TTS（`app/tts/tts_volc.py`,接口同协议） |
| 2.2 | TTS 协议 + edge 实现：输入 narration → 输出 mp3 + word 时间戳 | `app/tts/base.py` `app/tts/tts_edge.py` | 20 条通过;时间戳毫秒级 |
| 2.3 | 字幕生成：word 时间戳按标点(。！？)与 18 字上限聚合 | `app/tts/subs.py` | 字幕不重叠、覆盖全文、无截断词 |
| 2.4 | FLUX 生图：SiliconFlow images API,16:9,seed 记录,失败重抽(≤3) | `app/vision/flux.py` | 产出 PNG 入 assets/ |
| 2.5 | 生图缓存：prompt_hash(SHA256 of prompt+model+size)命中即复用 | `app/vision/cache.py` | 二次生成 0 次 API 调用 |
| 2.6 | 任务状态机：`run_task(project, node, fn)` — done 跳过 / running→失败重试(指数退避×3) | `app/core/state.py` | 见 §5 规则 |
| 2.7 | 分镜生成（DeepSeek）：文案 → 场景列表 JSON(强制 JSON output,校验后落盘) | `app/director/director.py` | 产出 scenes 可被 schema 校验 |
| 2.8 | 素材链路测试：mock 外部 API 回归 + 1 条真实全链路 | `tests/test_m1_assets.py` | pytest 绿;真实链路 JSON 完整 |

**M1 退出条件**：一条真实文案自动产出 配音+字幕+3 张图,全部归档,`avpo status` 状态树正确。

---

## 3. M2 端到端管线（第 3 周）

| # | 任务 | 产出文件 | 验收 |
|---|---|---|---|
| 3.1 | 时间线组装：scene 时长 = 对应配音段时长(mutagen 读实际 mp3);运镜轮转分配(zoom_in_slow / zoom_out / pan_left / pan_right / none) | `app/timeline/builder.py` | 总时长 = 配音总长,无缝隙 |
| 3.2 | 导出扩展：字幕样式（字号/居中/描边）、音频淡入淡出、封面首帧 | `app/export/jianying.py` | 剪映打开验证 ✅ |
| 3.3 | 一键流水线 CLI：`avpo run <pid>` = direct→confirm→gen→timeline→export;confirm 输出分镜 JSON 等 y/n（n 则交给用户改文案重跑） | `app/cli.py` | 一次命令全链路 |
| 3.4 | e2e 测试：固定输入+固定 seed → 固定输出比对;记录端到端耗时 | `tests/test_e2e.py` | 耗时写入 `tests/e2e_time_log.md` |
| 3.5 | 优化到 ≤5 分钟：并发生图(3~4 张并行)、LLM 输出流式不等待、跳过已 done 节点 | `app/cli.py` `app/vision/flux.py` | 60s 口播 ≤5min 达标 |

**M2 退出条件**：`avpo run` 一条 60s 口播全自动,≤5 分钟,剪映打开成功。

---

## 4. M3 健壮性（第 4 周）

| # | 任务 | 产出文件 | 验收 |
|---|---|---|---|
| 4.1 | 断点续跑：启动扫描 failed/pending 重跑;缓存命中跳过;kill -9 后重跑 **0 次重复 API 调用**（日志断言） | `app/core/state.py` | 中断恢复测试通过 |
| 4.2 | 成本统计：每任务 cost 汇总,`avpo cost` 输出明细;超预算(默认 ¥5/项目)告警 | `app/cli.py` `app/core/cost.py` | 输出正确 |
| 4.3 | 错误分类与可读化：API 错误(余额/限流/网络) vs 格式错误 vs 剪映错误,各自提示修复动作 | `app/core/errors.py` | 三类错误均有测试 |
| 4.4 | 3 个风格模板回归：快节奏口播 / 情感向(慢运镜+BGM) / 解说向(字幕样式不同)——各产 3 条 | `templates/` `tests/test_regression.py` | 9/9 剪映打开成功 |
| 4.5 | 汇总 `opened_log.md`,导出成功率统计 | — | **≥90%** |
| 4.6 | 文档收尾：README（安装/使用/成本）、架构图、阶段 1 备忘 | `README.md` | — |

**M3 退出条件**：成功率达标;kill 恢复、成本告警、错误提示全部验证;MVP 可交付使用。

---

## 5. 关键技术设计要点

**状态机（一行规则）**：`run_task` 前检查该节点 status——`done` 跳过,`failed` 或 `pending` 执行;执行中写 `running` 并立即 commit;成功写 `done` 并 commit;失败写 `failed` + 错误摘要并 commit。**状态与产物永远同一次提交落盘**,这是断点续跑可靠的前提。

```python
def run_task(project, node: str, fn):
    if project.pipeline[node] == "done": return True
    project.set(node, "running"); save_and_commit(project)
    for attempt in range(3):
        try:
            fn(project)
            project.set(node, "done"); save_and_commit(project); return True
        except TransientError as e:            # API/网络 → 重试
            time.sleep(2 ** attempt)
        except FatalError as e:                # 格式/剪映 → 不重试,报修复动作
            project.set(node, "failed", error=str(e)); save_and_commit(project); return False
```

**pipeline 节点**：`direct → confirm → gen_assets → timeline → export`,各节点在 project.json 顶层 `pipeline` 对象中。

**原子写**：写 `project.json.tmp` → `os.replace` → `git commit`,三步顺序执行,进程死在任意一步都不损坏。

**字幕聚合**：TTS word 时间戳流 → 按 `。！？，;` 断句,超 18 字再折半,最后一句不足 0.5s 并入前句。断言：字幕并集覆盖全文、无重叠。

**导出兜底**：M2 若 pyJianYingDraft 对某效果（花字/特殊运镜）不支持 → 启用模板路线：jy-draftc 解密手工模板 → 模板模式替换素材与文本 → 回加密。**仅在确实需要时做,不进 MVP 主线**。

**成本账本**：每次 API 调用成功即写 `cost` 到对应 asset/scene,`avpo cost` 只是 SUM——不做二次对账。

---

## 6. 每周节奏（建议 4 周 × 5 天,每天 2~3 小时）

| 周 | 一 | 二 | 三 | 四 | 五 |
|---|---|---|---|---|---|
| W1 | 前置准备 | schema+store | CLI 骨架 | pyJianYingDraft 调研 | 导出适配+打开验证 ✅ |
| W2 | edge-tts 实测 20 条 | tts+字幕 | flux+缓存 | 状态机+director | 链路测试+验证 ✅ |
| W3 | timeline builder | 导出扩展 | run 流水线 | e2e+计时 | 优化 ≤5min ✅ |
| W4 | 断点续跑 | 成本+错误分类 | 3 模板回归 | 成功率统计 | README+复盘 ✅ |

周五下午固定做**打开验证**与**本周退出条件核对**,不达标则下周优先补——宁可砍范围,不可带病前进。

---

## 7. 失败预案（出现即响应）

| 现象 | 处理 |
|---|---|
| 剪映打不开草稿 | 停主线,回 1.4 换 fork/换生成方式;记录错误现象到 opened_log |
| edge-tts 大量失败 | 切 `tts_volc.py`（同协议,半天工作量） |
| SiliconFlow 限流/涨价 | 生图降级到 Z-Image-Turbo($0.005);LLM 降级同渠道小模型 |
| 运镜动画不支持 | 该 scene 降级为 `none`（静态图）,不阻塞主线 |
| 4 周超期 | 保 M0+M1（素材自动化已解决 80% 搬运问题）,M2/M3 顺延 |
