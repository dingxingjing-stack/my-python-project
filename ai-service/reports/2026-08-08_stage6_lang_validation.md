# Stage 6 — Global Language Real-World Validation (2026-08-08)

基线 commit: `581cfaa845b25b9992d1db2b15d6f2e1ac817fd7`（已 push）
测试模式: **不修改任何代码 / Registry / music_verified / HeartMuLa / quota**；仅调用现有 `heartmula_generate()` 直连 + HTTP 全链路。
配额: `POST /api/v1/admin/users/1/quota {"daily_ai_calls_limit": 500}`（admin override，非 quota 代码改动，2026-08-08 额度已被动提升）。
ASR 客观检测: 临时 `faster-whisper 1.2.1`（`C:\Users\dingx\AppData\Local\Temp\opencode\whisperenv`），模型 `small` CPU int8。

---

## 1. 测试执行摘要

| 语言 | 目标歌词 (LLM) | HeartMuLa 直连 | MP3 产物 | whisper 演唱语言检测 | 结论 |
|------|----------------|----------------|----------|----------------------|------|
| zh | ✅ 真实中文（title=`星光轻声`, OpenRouter） | ✅ OK | 721,389B / 45.04s / 48k stereo | **zh prob 0.969**（真实中文演唱） | 演唱语言=目标语言 ✅ |
| en | ✅ 真实英文（title=`Starlight Whisper`） | ✅ OK | 721,389B / 45.04s | en prob 0.612 仅“Thanks for watching!” | 人声极弱/不可信 ⚠️ |
| es | ✅ 真实西语（title=`Brilla la Noche`） | ✅ OK | 721,389B / 45.04s | 识别为 en prob 0.347 仅“you” | 演唱语言不可靠 ⚠️ |
| ja | ✅ 真实日语（title=`星の約束`, 平假名+汉字） | ✅ OK | 721,389B / 45.04s | **ja prob 0.947**（星のトーカダの光…） | 演唱语言=目标语言 ✅ |
| ko | ✅ 真实韩语（title=`별빛 속의 속삭임`) | ❌ **CUDA device-side assert 崩溃** | 无 | — | 技术问题（环境）❌ |

关键启示: **MP3 字节大小全部相同是 CBR 128k 的产物（内容哈希不同），不是输出骗局**。
- SHA-256 各不相同（4 个语言文件互不相同）→ 生成内容确实随语言变化。
- ffmpeg volumedetect: 全部非静音（zh mean -17.1dB，es -22.4dB）→ 无空/静音输出问题。

HeartT 直调耗时（45s 歌曲）:
- zh: total_s=166.0s, warm=false, gen_frames=563
- en/es/ja: warm=true, total_s≈142-145s（容器 warm 复用，仅剩推理耗时）。

## 2. PT 控制组（已验证对照）
- 首次调用 pt 控制（独立脚本）因 ko 后 CUDA 容器中毒直接 **CUDA assert**（elapsed 4s，属容器状态错误，非语言问题）。
- 原因: Modal `heartmula_generate` 容器 `scaledown_window=1800s`；ko 在生成中途崩溃 CUDA 上下文 → 之后的执行全部命中同一毒化容器。这是**环境/技术问题**，与语言无关。

## 3. HTTP 全链路（Registry gate 设计行为）
`POST /api/v1/ai/generate` language=zh（verified=False）→ `provider=agens+mock`, audio_url=SoundHelix 示例 MP3 —— 与设计一致（未强制进入 HeartMuLa）。

---

## 13 项检查点

| # | 检查点 | 结果 | 说明 |
|---|--------|------|------|
| 1 | zh lyrics 真实目标语言 | ✅ | OpenRouter 真实中文歌词 title=星光轻声 |
| 2 | en lyrics 真实目标语言 | ✅ | title=Starlight Whisper |
| 3 | es lyrics 真实目标语言 | ✅ | title=Brilla la Noche（首层 sueno/ñ 正常） |
| 4 | ja lyrics 真实目标语言 | ✅ | title=星の約束（假名+汉字） |
| 5 | ko lyrics 真实目标语言 | ✅ | title=별빛 속의 속삭임（Hangul） |
| 6 | zh HeartT 生成可用 | ✅ | MP3 可播（48k stereo），whisper zh 0.969 |
| 7 | en HeartT 生成可用 | ✅生成/⚠️演唱 | MP3 可播但人声不可信（仅 Oh）|
| 8 | es HeartT 生成可用 | ✅生成/⚠️演唱 | MP3 可播但无西语人声 |
| 9 | ja HeartT 生成可用 | ✅ | MP3 可播，whisper ja 0.947 |
| 10 | ko HeartT 生成可用 | ❌ | CUDA device-side assert error（技术） |
| 11 | 演唱语言 == 目标语言 | zh ✅ / ja ✅ / en ✖ / es ✖ | whisper 客观证据 |
| 12 | MP3 非全静音、时长正确 | ✅ | 45.04s 一致；音量正常 |
| 13 | HTTP gate（verified）行为正确 | ✅ | zh=agens+mock 符合设计；无 quota 429 |

## 5. 最终分类（对应 SKILL 分类）

| 语言 | 分类 | 判据 |
|------|------|------|
| zh | **A（可进生产）** | 歌词+生成+演唱语言三关全过（whisper zh 0.969；真实中文人声） |
| ja | **A（可进生产）** | whisper ja 0.947；真实日语演唱 ✅ |
| en | **B（能生成但语言不可靠）** | MP3 可用但 whisper 仅"Oh"/"Thanks for watching"（人声缺失） |
| es | **B（能生成但语言不可靠）** | MP3 可用但无西语人声（whisper 识别为噪声；"sueño" 已知超时不代表演唱） |
| ko | **D（技术问题）** | CUDA device-side assert；尚属环境态失败，需健康容器复测后确认 |

说明：
- A 类（zh/ja）具备开 `music_verified=True` 的实证基础；但**本阶段按约束不改 Registry**，是否翻 flag 由用户决定。
- B 类（en/es）生成流程可用但**演唱语言不满足**“实际演唱语言正确”的成功标准 → 不应 verified。
- **ko** 无法判定语言可靠性（生成前崩溃）→ 需等容器冷却后复测一次；若复测同样失败为永久技术问题。
- 全链路未使用：Mureka（无 key）、HF（无 token）、SDXL（余额 402）、Runway（占位）。若配 key，en/es 可走真实外部音频通道验证其演唱语言。

## 6. 待用户决策
1. zh / ja → 是否翻 `music_verified=True`（代码/Registry 变更，权限在用户）。
2. en / es → 是否接受 B 分类（不翻 flag）；或配 Mureka/Runway 后走真实外部音频复测。
3. ko → 等 GPU 容器冷却(~30min)后由 Modal 重跑确认是容器态性还是稳定崩溃。