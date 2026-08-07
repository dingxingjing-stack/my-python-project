# P0 密钥梳理 — 专项报告

**报告日期**: 2026-08-07
**目标**: 梳理 SiliconFlow / Mureka / Runway 密钥缺口，打通完整外部模型链路
**提交**: `682e1d4`（Modal secret 基建挂载）

---

## 一、密钥全景（线上 doctor 实测 2026-08-07）

| 提供方 | 密钥 | 状态 | 实测现象 | 结论 |
|---|---|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` (73) | ✅ 正常 | lyrics 200, `nemotron-3-nano-30b-a3b:free`, 21s | 文本主力通道 |
| SiliconFlow | `SILICONFLOW_API_KEY` (51) | ⚠️ **402 余额不足** | chat 402 "balance insufficient"; image 403 "model disabled"; models 200 | key 有效但账户无余额 |
| Agnes | `AGNES_API_KEY` (51) | ✅ 正常 | 视频 create 200 / poll 200 | MV 视频通道 |
| Mureka | `MUREKA_API_KEY` | ❌ **未配置** | 无 key → 音乐降级 SoundHelix/Mock | 需用户提供 key |
| Runway | `RUNWAY_API_KEY` | ❌ 占位 `your-` | 无 key | MV V4.0 已用 FLUX+Agnes 替代，可选 |

---

## 二、按目标能力拆解

### 1. 真实音乐音频（当前最弱链路）
- **现状**: 歌词(OR) → 音乐降级链 Agnes→Mureka(无key跳过)→HF(无key跳过)→**SoundHelix/Mock**
- **缺口**: 仅缺 `MUREKA_API_KEY`。`mureka_service.py` 已生产就绪（读 env、429/未配置自动降级）
- **行动**: 用户去 https://www.mureka.ai/ 注册拿 key → 更新 Modal secret `mureka-key`

### 2. 文本/代码 LLM（已打通）
- **现状**: `PRIMARY_PROVIDER=openrouter` 生效，真实生成
- **SiliconFlow 修复路径**: key 有效但 402 → 需在 siliconflow.cn 控制台**充值/实名**后更新
  - 注: 当前已用 OpenRouter 完全绕开，SiliconFlow 修复后可将 `PRIMARY_PROVIDER` 切回或留作 SDXL 生图兜底

### 3. MV 视频镜头（V4.0 已本地化）
- **现状**: FLUX 图片序列 + Kokoro TTS + Agnes 视频均可用
- **Runway 缺口**: 仅影响「动态镜头」增强，非必需；按钮已前端置灰

---

## 三、代码侧已就绪验证

| 提供方 | 客户端 | 未配置降级 | 已挂载 secret |
|---|---|---|---|
| Mureka | `mureka_service.py` | 抛 QuotaExceededError → 降级 | ✅ `mureka-key`（本次新增，空） |
| Runway | `runway_client.py` | `is_configured=False` → 跳过 | ✅ `runway-key`（本次新增，空） |
| SiliconFlow | `ai_scheduler.py` | 超时/错误 → OpenRouter/本地网关 | ✅ `siliconflow-key`（已有） |

> 全部 3 个 Modal 函数（web / run_mv_job / doctor）已挂载 `mureka-key`、`runway-key`，
> 线上部署健康（health 200，gateway UP，降级警告正常）。

---

## 四、用户行动清单（需人工操作）

| # | 动作 | 平台 | 完成后 |
|---|---|---|---|
| 1 | 注册获取 Mureka API Key | https://www.mureka.ai/ | `modal secret create mureka-key MUREKA_API_KEY=<key>` 或控制台更新 → 音乐走真实生成 |
| 2 | SiliconFlow 账户充值/实名 | https://cloud.siliconflow.cn/ | 更新 `siliconflow-key` → SDXL 生图兜底可用；可将 PRIMARY_PROVIDER 切回 |
| 3 | (可选) 获取 Runway key | https://dev.runwayml.com/ | 更新 `runway-key` → MV 动态镜头恢复 |

> **Modal secret 更新注意**: Modal secret 无 update 命令，需 `delete` 后 `create` 同名的，
> 或直接在 Modal Dashboard 控制台编辑；改后必须 `modal deploy modal_server.py` 生效。

---

## 五、结论

- 代码链路（Mureka/Runway/SiliconFlow 三客户端 + 降级 + 限流）**全部就绪**，本轮已补齐 Modal secret 基建与文档
- 剩余 P0 缺口均为**用户侧密钥/账户**问题，无法在代码侧修复：
  - Mureka: 缺 key（音乐真实音频）
  - SiliconFlow: 余额不足（SDXL 生图兜底）
  - Runway: 占位符（MV 动态镜头，可选）
- 一旦用户填入 key 并重新部署，无需任何代码改动即可启用对应真实链路
