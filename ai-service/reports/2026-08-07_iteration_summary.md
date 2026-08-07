# 迭代总结 — 2026-08-07

**报告日期**: 2026-08-07
**报告范围**: 本轮迭代（Mode-A 本地网关云端化 + 强制本地网关业务回归 + 性能优化）
**工作树状态**: 干净，已推送 `origin/main`（最新 `f689296`）

---

## 一、本轮已完成

| 事项 | 结果 |
|---|---|
| 容器内 Mode-A 本地网关（opencode serve + OpenAI 兼容翻译网关） | ✅ 上线，`[web] local Mode-A gateway is UP` |
| 网关 400/500 修复（model 对象化 + sentinel 模型不传 model） | ✅ 容器内探针 chat 200 |
| `startup_check.py` 文本 key 降级为可选（keyless 可启动） | ✅ `01bb3e4` |
| 强制本地网关业务回归（清 key + `FORCE_LOCAL_GATEWAY=true`） | ✅ lyrics `provider=local_gateway`, 纯中文结构化, 9.8s |
| health / gzip / 缓存 / 30s 超时兜底复测 | ✅ 全部通过 |
| 生产密钥/主配置还原 | ✅ openrouter 路径正常（15.9s LRC） |
| FLUX 冷加载加速（模块级 pipeline 缓存） | ✅ 端到端 593s → 440s（-26%） |

---

## 二、当前已知缺陷清单（按影响排序）

### P0 — 服务商密钥 / 业务真实能力
1. **SiliconFlow key 无效**
   - 现象: 线上 `402 balance insufficient`；本地网络握手失败（HTTP 000）
   - 影响: SDXL 生图兜底不可用；若其为 `primary_provider` 每请求白等 45s 超时
   - 现状: 已用 `PRIMARY_PROVIDER=openrouter` 绕开；MV 生图走本地 FLUX
   - 待办: 需用户在 siliconflow.cn 核实 key（实名/充值）后更新 `siliconflow-key` secret
2. **Mureka / HF key 未配置**
   - 现象: 音乐生成无真实音频源
   - 影响: 音乐走 SoundHelix/Mock 兜底，非真实 AI 生成
   - 待办: 用户提供 key 后走真实生成链路
3. **Runway key 未配置（`.env` 占位符）**
   - 现象: MV 无动态镜头，Runway 按钮已置灰
   - 现状: V4.0 用 FLUX 图片序列 + 淡入淡出转场替代
   - 待办: 用户提供真实 key 后恢复动态镜头

### P1 — 已知行为限制
4. **`FORCE_LOCAL_GATEWAY=true` 不足以屏蔽环境内现存第三方 key**
   - 根因: opencode 按 env 中 key 是否存在选择默认 provider；只要 key 在，即使失效也优先第三方
   - 结论: 完全使用本地网关必须清空 key（删/置空 secret）；仅开关无效（已记录，属设计约束）

### P2 — 性能 / 体验
5. **FLUX 冷加载首场景慢（~120s / 219 权重分片）**
   - 现状: 容器存活窗口内已复用（-46% 场景间隔）；跨 job 仍冷启动
   - 候选: 后台预热任务 / 扩大 `scaledown_window`（未实施）
6. **MV 端到端耗时仍偏长（~440s）**
   - 构成: 容器冷启动 + FLUX 首载 + TTS + FFmpeg 合成
   - 前端 `POST /api/v1/ai/mv/generate` 为异步 job，可轮询，但等待较久
7. **歌词真实慢调用触发 30s 超时 → Mock 兜底**
   - 设计如此（`LYRICS_TIMEOUT_SECS` 可调），但长任务可能无真实结果
   - 候选: 对歌词也做异步 job 化（未实施）

### P3 — 边界 / 健壮性
8. **跨容器 SQLite WAL 写不可靠**
   - 已用「共享卷 JSON 文件优先」方案规避（`mv_jobs/*.json`）；SQLite 仅兜底
   - 遗留: 需保持文件优先约定，避免新任务再走 SQLite 写路径
9. **长会话 / 请求超时上限（120s）**
   - 网关超时 120s；极端长文（VISION/LONG）可能截断，需按需调

---

## 三、技术债 / 待跟进

| 项 | 说明 |
|---|---|
| `modal_server.py` 中废弃 GPU 包装（CogVideoX / MusicGen） | 已被 FLUX/Kokoro 取代，仅留未调用包装，可清理 |
| `oc_probe.py` 临时探针 | 已删除；如需复现网关问题可临时重建 |
| 歌词异步 job 化 | 当前同步 + 超时兜底；体验优化候选 |
| MV 模板在 `/create` 的深度对接 | 模板选择器已上，但样式映射仅为前 6 词，可细化 |

---

## 四、结论

本轮把 Mode-A 免费网关完整搬入 Modal 容器，实现**无第三方 key 也可全链路生成**（歌词走容器内 opencode `big-pickle`），并通过强制本地回归 + 生产恢复双重验证。当前主要缺口是**真实密钥未齐**（SiliconFlow/Mureka/HF/Runway），属用户侧配置；代码侧已用本地开源模型（FLUX/Kokoro/SoundHelix）与降级链覆盖。
