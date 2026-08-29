"""运镜 → 关键帧计划（M7-8.2）：scene.motion 解析为剪映可消费的 MotionPlan。

设计（M7 动态化）：
- 运镜参数集中在本模块一处解析 —— animate 节点（run_animate）把计划写入
  scene.motion_plan 落盘，剪映导出（jianying.py）对旧项目（没跑 animate）用
  同一个函数兜底。两处同源，参数可审计可调。
- 四种运镜全部用关键帧实现（M2 曾把 pan_* 降级为无动画，M7 解除该限制）：
  - zoom_in_slow：uniform_scale 1.0 → 1.15（画面缓慢推近）
  - zoom_out：uniform_scale 1.15 → 1.0（画面缓慢拉远）
  - pan_left：position_x -0.12 → +0.12，恒 1.15 缩放（镜头左摇，内容向右滑）
  - pan_right：position_x +0.12 → -0.12，恒 1.15 缩放（镜头右摇，内容向左滑）
  - none：全默认（不写任何关键帧）
- 语义约定（position_x 右移为正，单位半个画布宽，pyJianYingDraft 0.3.0）：
  镜头左摇 = 视窗从画面右侧滑向左侧 = 内容在画布上向右移动（x 增）。
  pan 恒配 1.15 缩放：±0.12×半画布宽 ≈ ±115px < 1.15 缩放的 288px 余量，不露边。
"""

from app.core.schema import MotionPlan, Scene

ZOOM_SCALE = 1.15          # 推近/拉远的关键帧缩放（pan 防露边的恒缩放）
PAN_EXTENT = 0.12          # 摇镜起/终点的 position_x（单位：半个画布宽）
END_FRAME_MS = 400         # 首尾帧尾拍时长（配音后追加的静态尾拍，硬切；切点转场见 M9）


def resolve_motion_plan(scene: Scene) -> MotionPlan:
    """把 scene.motion（+ 是否设了结束帧）解析为关键帧运镜计划。

    确定性纯函数：同样输入恒同样输出（animate 节点幂等，export 兜底可用）。
    """
    end_frame_ms = END_FRAME_MS if scene.end_image_asset_id else 0
    motion = scene.motion
    if motion == "zoom_in_slow":
        return MotionPlan(scale_from=1.0, scale_to=ZOOM_SCALE, end_frame_ms=end_frame_ms)
    if motion == "zoom_out":
        return MotionPlan(scale_from=ZOOM_SCALE, scale_to=1.0, end_frame_ms=end_frame_ms)
    if motion == "pan_left":
        return MotionPlan(
            scale_from=ZOOM_SCALE, scale_to=ZOOM_SCALE, pan_from=-PAN_EXTENT, pan_to=PAN_EXTENT,
            end_frame_ms=end_frame_ms,
        )
    if motion == "pan_right":
        return MotionPlan(
            scale_from=ZOOM_SCALE, scale_to=ZOOM_SCALE, pan_from=PAN_EXTENT, pan_to=-PAN_EXTENT,
            end_frame_ms=end_frame_ms,
        )
    return MotionPlan(end_frame_ms=end_frame_ms)   # none / 未知运镜：不动，只保留尾拍时长
