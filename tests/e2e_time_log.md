# 端到端耗时记录（IMPLEMENTATION_PLAN 3.4）

> M2 退出条件：`avpo run` 一条 60s 口播全自动 ≤5 分钟。

## 真实链路（dashscope 千问渠道，2026-08-21）

| 项目 | 场景数 | 口播时长 | 字幕 | 耗时（direct 分次跑） | gen_assets | timeline | export | 合计 |
|---|---|---|---|---|---|---|---|---|
| proj_m2live | 6 | 56.6s | 20 条 | ~30s | 120.6s（续跑，含 2 张缓存命中） | 0.9s | 1.6s | **~153s ≈ 2.5min** |

- **结论**：✅ **达标**（≤5 分钟）。gen_assets 为主体耗时：6 张 wanx2.1-t2i-turbo 串行生成（每张 ~15-20s）。
- **成本**：6 张图 × ¥0.16 = ¥0.96；edge-tts 免费；qwen-plus 分镜 ~¥0.01。单条视频总成本 < ¥1。
- **优化空间（3.5）**：并发生图 3~4 张 → gen_assets 预计降到 ~40-60s。
- 首次尝试曾因 edge-tts NoAudioReceived 失败（6 场景连续请求触发瞬态错误）→ 已在
  EdgeTTS provider 内加重试 ×3（0.5s/1s 退避），续跑一次通过。

## 离线 e2e（tests/test_e2e.py）

固定输入 + 确定性假实现（director/生图/配音）→ 两个项目跑同一文案，project.json
逐字段一致；全 done 重跑秒级返回。真实耗时以本表真实链路为准。
