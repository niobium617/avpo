# pyJianYingDraft 调研笔记（M0-1.4）

> 调研日期：2026-08-19　|　版本：PyPI **0.3.0**（最新，无更新版本）
> 结论摘要：**API 与 IMPLEMENTATION_PLAN §1 的 sketch 差异较大**（主类改名 + 轨道 API 重设计），但能力完整覆盖 MVP 三轨需求，demo 已跑通。本笔记以实测为准，1.5 导出适配器按此实现。

---

## 1. 版本与兼容性

- PyPI 最新版 = 0.3.0（已安装），README 称 0.3.0 经历"大规模更新"。
- 生成草稿的 `draft_content.json` 模板：`version: 360000`、`new_version: 110.0.0`（即剪映 11.0.0 格式）。
- 官方 README 兼容性：草稿生成/模板在剪映 **5.9 与 10.8** 均有测试；批量导出（uiautomation 控件）仅剪映 6 及以下——**剪映 7+ 隐藏了导出控件，不可自动导出**，与计划 §6 的结论一致（人工导入）。
- **加密**：剪映新版 `draft_content.json` 往往不是明文，加载模板需 `DraftFolder(fallback_loader=...)`；**生成路径是明文草稿，剪映 6+ 可直接打开**（打开后剪映自行加密）。符合计划 §6。

## 2. API 结构（0.3.0 实测，与计划 sketch 的关键差异）

| 计划 sketch（旧） | 0.3.0 实际（新） |
|---|---|
| `JianYingDraft(w, h, fps)` | `ScriptFile(width, height, fps, maintrack_adsorb)`；**推荐 `DraftFolder(folder).create_draft(name, w, h, fps)` 创建**，自动写 `draft_meta_info.json` |
| `draft.add_video(...)` 隐式轨 | 先 `append_track(TrackSpec(TrackType.video, 'v1'))` 建轨，再 `add_segment(segment, track_name)` |
| `v.add_animation(name)` | `VideoSegment.add_animation(IntroType/OutroType/GroupAnimationType[...], duration)` 枚举成员为**中文名**（如 `IntroType['放大']`） |
| `draft.add_text(...)` | `TextSegment(text, trange(...))`，或 `import_srt(srt_path, track_name)` 一键字幕轨（自动建文本轨、带默认字幕样式） |
| `draft.export(out_dir)` | `sf.save()`（DraftFolder 模式已定位路径）；`sf.dump(path)` / `dumps()` |

**核心事实：**
- **时间单位是微秒**：`SEC = 1_000_000`，`trange(start, duration)` 收微秒（也支持 `'1.5s'` 字符串），`tim()` 任意格式转微秒。
- 轨道类型枚举：`video / audio / text / effect / filter / sticker`；同类型轨道多时 `add_segment` 必须传轨道名。
- `VideoSegment(material_or_path, target_timerange, *, source_timerange, speed, volume, ...)` 直接传**素材路径字符串**即可，自动构造 `VideoMaterial`（pymediainfo 读时长/宽高；图片素材 duration=3h 长帧）。
- 素材/动画/特效由 `add_segment` **自动注册**进 `materials`，无需手工维护。
- 素材 `path` 记录为**绝对路径** —— 导出适配器必须先把素材拷入草稿目录再建素材（相对引用 + zip 可迁移）。
- 音频真实时长以实际文件为准（demo 实测：预估 8.32s，mutagen 实测 6.528s）→ 时间线必须以 mutagen 读出的时长计算。

## 3. Demo 实测（三轨，全部成功）

- 2 张 64×64 PNG（图片素材，类型 `photo`）+ edge-tts MP3 + 4 条字幕 + 2 个动画（入场"放大"、"缩放"组合动画）。
- 产出 `demo_draft/draft_content.json`：明文 JSON，`new_version: 110.0.0`，3 轨（video×2 段 / audio×1 段 / text×4 段），素材自动注册（videos=2, audios=1, texts=4, material_animations=2），`duration` 自动取最晚片段终点。
- 素材引用为绝对路径 → 1.5 需先拷贝。

## 4. 与 aoguai fork 对比

- `aoguai/pyJianYingDraft`（自维护 fork，"支持高版本剪映"）：功能对比表显示剪映 **10.8** 上绝大多数功能 ✅（本地素材/关键帧/动画/特效/滤镜/转场/字幕 srt 导入等），仅视频蒙版 🟡 待本机验证、轨道顺序控制、字体缓存有注意项。
- fork 额外提供 `content_codec`（解码本机草稿格式的**私有扩展，不进上游 PR**）—— 仅模板解密场景需要，MVP 主线用不到。
- **决策：用 PyPI 0.3.0 主线版**。demo 已跑通、能力覆盖 MVP；fork 只有模板解密增值，将来真需要时再按需引入。若剪映打开验证失败再切 fork。

## 5. 1.5 导出适配器设计要点（实测依据）

1. 导出目录结构：`<draft>/draft_content.json` + `draft_meta_info.json` + `materials/`（拷贝的素材）。
2. 流程：建 `DraftFolder(out_root)` → `create_draft(name, 1920, 1080, 30)` → 拷素材 → `append_track` ×3 → 按 timeline 加段 → 字幕用 `TextSegment` 逐条或 `import_srt` → `save()`。
3. 时长换算：project.json 毫秒 → 微秒（×1000）。
4. 运镜映射：`zoom_in_slow → IntroType['放大']`（入场动画），`pan_left` 等后续 M2 再补，MVP 先覆盖 zoom。
5. 素材路径：先拷进草稿目录再建 `VideoMaterial/AudioMaterial`，保证草稿自包含、zip 可迁移。
6. zip 打包：`shutil.make_archive`，产物 `exports/<pid>.zip`。
7. 错误提示：素材缺失/格式不支持 → FatalError（不重试），给修复动作。

## 6. 注意与风险

- 剪映打开草稿时会尝试**自动下载未缓存的动画/特效/转场**，网络差可能显示"xx 加载失败"（5.9 上较多）——打开验证时观察。
- 动画枚举是中文名且随剪映版本增长（IntroType 155 项），**钉版本 + 打开验证**守则不变。
- 字幕 SRT 导入带默认样式（`TextStyle(size=5, align=1)` + `clip_settings(transform_y=-0.8)`），若剪映里样式不理想，1.5 用 `TextStyle`/`TextBorder` 定制。
