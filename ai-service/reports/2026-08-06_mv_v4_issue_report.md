# MV V4.0 端到端测试 — 问题复盘与测试报告

**报告日期**: 2026-08-06
**涉及提交**: `2b10e3d`（初版，已部署）
**目标**: 验证本地 FLUX.1-schnell（生图）+ Kokoro-82M（TTS）全链路可用性
**范围**: 测试、根因定位、修复方案；复测结果见文末「复测补充」章节

---

## 一、执行过程

### 1.1 复现步骤
1. `POST /api/v1/ai/mv/generate`（提交完整 MV 任务）
   ```json
   {
     "lyrics": "Verse: 夜空中 星光指引 我们向前 / Chorus: fly to the stars now...",
     "title": "星夜启程",
     "style": "cinematic, night sky, stars",
     "num_scenes": 3,
     "user_id": 1
   }
   ```
2. 返回 `job_id`，后台由 Modal 独立容器 `run_mv_job` 执行（CPU 容器经 `Function.from_name` 调用 GPU 函数）
3. 轮询 `GET /api/v1/ai/mv/job/{job_id}`（30s 间隔）

### 1.2 执行结果
| 项目 | 结果 |
|---|---|
| 第 1 轮 `46360a60` | completed（Flux 容器冷启失败 → 文字幻灯片兜底） |
| 第 2 轮 `7ed7c43d` | completed，主线 ~123s，端到端 ~602s |
| 产物 | `videos/20260806_1b1b17c86dbd.mp4` — h264 1280×720 + aac，可播放 |
| 音频通道 | ✅ Kokoro TTS 真实生成 `audio/20260806_6358d366c2e3.wav`（216KB） |
| 画面通道 | ❌ 无 Flux 图片，回退文字幻灯片（4.6s 单页） |

---

## 二、问题 1：Flux meta-tensor 并发崩溃

### 2.1 现象
流水线内 Flux 生图全部失败，日志：
```
[modal-gpu] flux_image_generate 失败: NotImplementedError:
  Cannot copy out of meta tensor; no data!
  Please use torch.nn.Module.to_empty() instead of torch.nn.Module.to()
```
→ 通通熔断（连续失败 3 次，冷却 300s）→ 所有场景回退 → 无画面。

但单独 `modal run flux_image_generate`（单实例）**成功**（219 shard 加载 + 4 步推理 69s）。即「单实例可用，流水线并发不可用」。

### 2.2 根因
- 崩溃点：`modal_server.py` → `pipe.enable_model_cpu_offload(gpu_id=0)`
- 前置链路：
  ```
  quantize(transformer, weights=qfloat8)   # FP8 量化 → 权重变 lazy/meta
  freeze(transformer)
  FluxPipeline(...)                         # 手动逐组件装配
  pipe.enable_model_cpu_offload()           # 内部对 meta 权重调 .to(device) → 崩
  ```
- 核心：`low_cpu_mem_usage=True` 让组件参数停留在 **meta/lazy 张量**；`enable_model_cpu_offload` 内部的 device 迁移 hooks 对 meta 张量调 `.to()`，PyTorch 抛 `NotImplementedError`。
- 单实例可过、并发必现：多容器（`max_containers=2` × 多场景并发）下内存压力与 hook 触发顺序变化，属**非确定并发崩溃**。

### 2.3 影响范围
- 生产 MV 无 AI 画面，全部回退文字幻灯片（体验降级，**不白屏、不报错**）
- 波及任何走 `mv_scheduler.generate_scene_image` 的路径
- 不波及 Kokoro TTS、音频、FFmpeg 合成、文字幻灯片
- 严重度：高（V4.0 核心本地生图失效）；`gpu_quota_exhausted` 分支不触发（该异常非 quota 类型）

### 2.4 修复方案（已实施）
- **移除 `low_cpu_mem_usage=True`**，改为显式加载权重到 CPU（`local_files_only=True`，权重已在 volume，且符合搁置 HF DNS 决策）
- 容器内存 `32GB → 48GB` 保证移除 low-mem 后加载无 OOM
- 保留 `quantize(qfloat8)` + `freeze` + `enable_model_cpu_offload` 的 FP8 显存方案

---

## 三、问题 2：SiliconFlow 双 /v1 接口路径错误

### 3.1 现象
```
[MV] scene2 SiliconFlow 失败: Client error '404 404 Page not found'
  for url 'https://api.siliconflow.cn/v1/v1/image/generations'
```

### 3.2 根因
- `config.py`：`siliconflow_base_url = "https://api.siliconflow.cn/v1"`（已含 `/v1`）
- `ai_scheduler.py`：`f"{self._sf_base}/v1/image/generations"`（又拼一次 `/v1`）
- 结果：`/v1/v1/image/generations` → 404（确定性 bug）

> 注：`llm_client.py:105` 用 `rstrip("/")` 防御过，但 `generate_image_sdxl` 未做，属于漏网。

### 3.3 影响范围
- SiliconFlow SDXL 兜底必然 404（与 key 无关）
- 叠加 SF 账户 **402 余额不足** → SF 通道双重不可用
- 因 Flux 已崩，SDXL 兜底也失效 → 无任何真实画面来源；Flux 修好后影响缩小为「SF 兜底不可用」

### 3.4 修复方案（已实施）
- `ai_scheduler.py:507` → `f"{self._sf_base.rstrip('/')}/image/generations"`（与 llm_client 防御一致）
- 附带说明：SF 无余额，此通道修复后仅在未来充值后生效

---

## 四、决策落实 ✓
1. SF 不充 → ✓ 未调用付费生图
2. Runway 置灰 → ✓ `create.html` 按钮置灰、不接视频生成
3. HF DNS 搁置 → ✓ 未触发在线拉模型（`local_files_only=True` 亦印证）

---

## 五、结论（修复前）
- ✅ 可用：Kokoro TTS 真实音频、FFmpeg 合成可播放 MV、降级不白屏
- ❌ 不可用：Flux 真实画面（并发崩溃）、SF 兜底（双 URL + 402）

---

## 复测补充（修复交付后填写）
> 以下由「GPU 全量 MV 复测」环节补充填写：
- 提交：`2026-08-07`，job_id = `55c6f1df`
- 产物 video_url = `/uploads/videos/20260807_dec8773e536b.mp4`，时长 4.68s，h264 1280x720 + aac 音频双流
- 是否包含 Flux 真实画面？**是** — 3 个场景全部生成真实 Flux 封面（1024x576 JPEG: `covers/20260807_df2add8cad73.jpg` 64KB、`fa96d113f3e9.jpg` 45KB、`e0e7909a07e2.jpg` 40KB），视频由图片序列（淡入淡出转场）+ Kokoro 音频合成，帧亮度 YMIN=22/YAVG=136/YMAX=207 证实为真实图像内容而非文字幻灯片
- 分阶段耗时：Flux 场景串行（scene0 ~09:38 / scene1 ~09:40 / scene2 ~09:42，各约 120s 冷加载+生成）→ TTS + 合成 11:43:29 保存 → 总耗时 ~593s（含 Flux 单容器冷启动）
- 结论：**Flux 链路修复可用** ✅
  - 根因复验：单容器 `modal run` 冷加载 219 权重分片全部成功、无 meta tensor 崩溃；先前并发场景（`max_containers=2` / `_MAX_FLUX_CONCURRENCY=2`）两容器同时冷加载导致其一残留 meta 张量 → `enable_model_cpu_offload` 崩溃
  - 修复：`modal_server.py` `flux_image_generate` `max_containers 2→1`、`max_inputs 2→1`（配合 commit `7f97e6c` 中 `mv_scheduler._MAX_FLUX_CONCURRENCY 2→1`）串行化冷加载
  - SF 双 `/v1` bug 亦确认修复：线上 URL 已为单 `/v1/image/generations`，当前返回 403（key 禁用），非 404
- 备注：单场景 Flux 生成约 120s 主要耗在冷加载权重（219 shards）；后续同一容器存活窗口内复用可显著加速