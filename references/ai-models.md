# AI 模型选型 + API 对比

## 1. AI 音乐生成

### 方案对比

| 方案 | 类型 | 音质 | 速度 | 成本 | API | 商业版权 | 推荐？ |
|---|---|---|---|---|---|---|---|
| **Suno API** | 第三方 API | v5.5 最佳 | ~30s/首 | $8~24/月 | 非官方（社区逆向） | Pro+ 有商业权 | ⭐⭐⭐⭐ |
| **Udio API** | 第三方 API | 优秀，擅爵士/蓝调 | ~30s/首 | $10~30/月 | 无公开 API（WebUI 调用） | 付费有商业权 | ⭐⭐⭐ |
| **MusicGen (Meta)** | 开源模型 | 中等，纯器乐 | GPU 需 10~30s | 免费（需 GPU） | 自建 API | MIT 开源 | ⭐⭐ |
| **Stable Audio** | 商业 API | 良好 | ~10s | $12~30/月 | 官方 API | 付费有商业权 | ⭐⭐⭐ |

### 推荐：Suno API（主力）+ MusicGen（开源后备）

**Suno 核心信息**（截至 2026-07）：
- 免费：10 首/天，v4.5-all 模型，无商业版权
- Pro：$8/月（年付），500 首/月，v5.5 模型，**有商业版权**，可上传音频（Cover/Extend），Stem 分离，人格化声音
- Premier：$24/月（年付），2000 首/月，Suno Studio（DAW 级工作台），MIDI 导出
- API 方式：目前无官方公开 API，需通过社区逆向方案调用或 Safari 浏览器协议
- 注意：API 调用可能违反 ToS，商业使用前需确认法律合规

**Udio 核心信息**：
- 音色偏向自然/蓝调/爵士，与 Suno 互为补充
- 无官方公开发 API
- 价格近似 $10~30/月

**MusicGen (Meta)** 开源方案：
- 支持文本/旋律条件生成
- 纯器乐，无歌声
- 适合 BGM/素材生成
- GitHub: `facebookresearch/audiocraft`
- 部署方式：云 GPU（AutoDL/Colab），或 HuggingFace Inference API

**实施建议**：
1. 主路线：对接 Suno（社区 API 封装）生成带人声完整歌曲
2. 备选：自建 MusicGen 服务生成 BGM/纯器乐素材
3. 后续关注：Suno/Udio 官方 API 开放后直接迁移

---

## 2. AI 声音克隆

### 方案对比

| 方案 | 类型 | 最低数据 | 训练速度 | 推理速度 | 音质 | 部署难度 | 许可 | 推荐？ |
|---|---|---|---|---|---|---|---|---|
| **RVC v2** | 开源 VC | 10min+ | ~30min (单卡) | 实时 | 优秀 | 中（需 GPU 6GB+） | MIT | ⭐⭐⭐⭐ |
| **GPT-SoVITS v4** | 开源 TTS | 1min+ | ~1h (单卡) | 快速 (RTF 0.028) | 优秀 | 中（需 GPU 8GB+） | MIT | ⭐⭐⭐⭐⭐ |
| **ElevenLabs** | 商业 API | 1min 样本 | 无需训练 | 快 | 优秀 | 简单（API调用） | 商业许可 | ⭐⭐⭐ |
| **OpenVoice** | 开源 VC | 5s+ | 无需训练 | 快 | 中等 | 低（可 CPU） | MIT | ⭐⭐ |

### 推荐：GPT-SoVITS v4（主力）+ RVC v2（实时变声后备）

**GPT-SoVITS**（59.4k stars，同作者为 RVC 作者）：
- **零样本**：5 秒样本即可 TTS，无需训练
- **少样本**：1 分钟微调即可达高相似度
- 支持中/英/日/韩/粤语，跨语言推理
- v4 版本原生 48k 输出，无金属音缺陷
- RTF 推理速度：4060Ti 上 0.028（1400 字 ≈ 4 分钟，推理仅 3.36 秒）
- 自带 WebUI + API（`api_v2.py` 提供 FastAPI 接口）
- 预训练模型 5000+ 小时数据集
- 部署：云 GPU（推荐 4060/4090 级），Docker 支持
- GitHub: `RVC-Boss/GPT-SoVITS`

**RVC v2**（36.3k stars）：
- 基于 VITS 的歌声转换框架
- 端到端延迟 ~90ms（ASIO）或 ~170ms（标准），可用于**实时变声**
- 支持 A 卡/I 卡加速
- 内含 UVR5 人声分离
- 10 分钟以上干净人声数据
- GitHub: `RVC-Project/Retrieval-based-Voice-Conversion-WebUI`

**ElevenLabs**（商业 API 备选）：
- 无需 GPU，直接 API 调用
- $5/月起，语音克隆需订阅
- 英语为主，中文效果一般

**实施建议**：
1. 声音克隆主力 = GPT-SoVITS，部署于云 GPU
2. 实时变声（如 K歌/直播） = RVC v2
3. 商业化考虑提供有限免费次数 + 付费解锁
4. 声音模型存 R2，30 天未使用自动清理

---

## 3. AI MV 生成

### 方案对比

| 方案 | 类型 | 时长 | 最大分辨率 | 成本 | API | 推荐？ |
|---|---|---|---|---|---|---|
| **Runway Gen-4.5** | 商业 API | 5-10s/次 | 1080p / 4K | $12~76/月 | 官方 API (`dev.runwayml.com`) | ⭐⭐⭐⭐⭐ |
| **Pika 2.5** | 商业 API | 5-10s/次 | 1080p | $8~76/月 | 官方 API (`pika.art/api`) | ⭐⭐⭐⭐ |
| **Stable Video Diffusion** | 开源模型 | 2-4s/次 | 576x1024 | 免费（需GPU） | 自建 | ⭐⭐⭐ |

### 推荐：Runway Gen-4.5（主力）+ Pika 2.5（备选）

**Runway**（截至 2026-07）：
- Free：125 credits 一次性，有水印
- Standard：$12/月（年付），625 credits/月，无水印
- Pro：$28/月（年付），2250 credits/月，自定义声音（TTS/LipSync）
- Max：$76/月（年付），9500 credits/月，credit 可滚存 1 月
- **有官方 API**：`dev.runwayml.com`
- 支持 Image-to-Video、Text-to-Image
- 包含 TTS/Audio 生成

**Pika 2.5**（截至 2026-07）：
- Free：80 credits/月，480p，无水印
- Standard：$8/月（年付），700 credits/月，Fast 速度
- Pro：$28/月（年付），2300 credits/月，Faster 速度
- Fancy：$76/月（年付），6000 credits/月，Fastest
- Pikaffects / Pikascenes / Pikadditions / Pikaswaps / Pikatwists 多种特效
- **有官方 API**：`pika.art/api`

**实施策略**：
1. MV 分段生成：一首歌 3min → 拆成 18 段 10s，逐个生成 → FFmpeg 拼接
2. 画面风格由前端 AI 创作面板选择 + LLM 写提示词
3. 成本控制：免费用户不提供 MV（或限 1 次/月 480p），付费用户 Runway Pro 级配额
4. 多人同时使用需自购多个 Runway/Pika API key

---

## 4. AI 二创 / Remix

### 方案对比

| 方案 | 类型 | 用途 | 部署难度 | 许可 |
|---|---|---|---|---|
| **Demucs (Meta)** | 开源模型 | 音频音轨分离（人声/伴奏/鼓/贝斯/其他） | 低（可 CPU，推荐 GPU） | MIT |
| **Ultimate Vocal Remover v5** | 开源 UI | 人声分离 + 去混响 | 低 | MIT |
| **MusicGen Style Transfer** | 开源模型 | 风格迁移/重新编曲 | 高 | MIT |
| **Suno 'Remix' 功能** | 商业 | 上传音频 → AI 重新编排 | 无需部署（Suno Pro+ 内置） | Pro 订阅含 |

### 推荐管道

```
原始歌曲 ──Demucs──▶ 人声轨 + 伴奏轨 + 鼓轨 + 贝斯轨
                             │
                             ▼
               MusicGen / Suno Cover ──▶ 新版伴奏（风格迁移）
                             │
                    新版人声（RVC 克隆 / GPT-SoVITS 合成）
                             │
                             ▼
                       FFmpeg 混音 ──▶ 完成二创曲目
```

**Demucs + RVC/Suno 组合**是最可行的二创路径。核心流程：
1. Demucs 分离原始音轨
2. 保留人声轨，用 MusicGen 生成新版伴奏（更改风格/节奏/调性）
3. 或用人声轨做 Cover（Suno Cover 功能）
4. 用 RVC 替换人声音色
5. FFmpeg 将多轨混音为成品

---

## 5. AI 歌词生成

### 方案对比

| 方案 | 成本 | 中文歌词质量 |
|---|---|---|
| **GPT-4o** | $2.5/1M input, $10/1M output | ⭐⭐⭐⭐ |
| **Claude (Anthropic)** | $3/1M input, $15/1M output | ⭐⭐⭐ |
| **DeepSeek** | ¥1/1M input, ¥2/1M output | ⭐⭐⭐⭐⭐ |
| **自建 LLM (Qwen/Llama)** | 免费（需 GPU） | ⭐⭐ |

### 推荐：DeepSeek API（成本最优）+ GPT-4o（高难度备用）

- DeepSeek 中文最强，成本最低（约 Proverbs Pro 的 1/5）
- 使用 System Prompt 引导：`你是一个专业作词人，擅长<风格>。请根据主题"<主题>"创作一首完整的<歌曲结构>歌词`
- 输出格式：**LRC 格式**，直接可存入 `tracks.lyrics_lrc`
- 歌词 Token 消耗极小（<1000 tokens/task），成本可忽略

---

## 6. 统一推荐方案

| 功能 | 主力方案 | 备选方案 | 部署难度 | 月成本估计 |
|---|---|---|---|---|
| AI 音乐生成 | Suno API（社区逆向） | MusicGen | 中 | ~$50（含 Suno 订阅+逆向维护） |
| 声音克隆 | GPT-SoVITS v4（GPU） | RVC v2 | 中 | ~$30/月 GPU 租用（AutoDL） |
| MV 生成 | Runway Gen-4.5 API | Pika 2.5 | 低 | ~$30/月（Pro 订阅额度） |
| AI 二创/Remix | Demucs + Suno Cover | MusicGen | 低中 | ~$10/月（GPU 偶尔使用） |
| AI 歌词 | DeepSeek API | GPT-4o | 低 | ~$5/月（极低 token 消耗） |

**总月度 AI 成本估算**：~$125/月（初创期），随用户量线性增长

---

## 7. 实施步骤建议

### Phase 1（MVP，1-2 周）
- [x] Suno 社区 API 封装 → Web UI 文本输入 → 生成歌曲
- [x] DeepSeek GPT 歌词 API 封装
- [x] R2 上传生成结果
- [x] 前端简易 AI 创作面板（文本 prompt + 风格选择）

### Phase 2（声音克隆，2-4 周）
- [ ] 租用 AutoDL GPU（4060/4090 级）
- [ ] 部署 GPT-SoVITS API（基于 `api_v2.py`）
- [ ] Workers 鉴权 + 异步任务队列
- [ ] 前端上传样本/输入歌词 → 轮询进度 → 下载成果
- [ ] 声音模型管理（D1 `voice_models` 表）

### Phase 3（MV 生成，1-2 周）
- [ ] Runway API 封装（`dev.runwayml.com`）
- [ ] MV 片段分段生成 + FFmpeg 拼接
- [ ] 前端 MV 预览播放器（HLS）

### Phase 4（二创/Remix，1 周）
- [ ] Demucs 部署（GPU 可选，CPU 也可）
- [ ] 音轨分离 → 风格迁移 → 重混音
- [ ] 集成到 AI 创作面板

### Phase 5（规模化，持续）
- [ ] 监控 AI API 成本，按用户等级分配配额
- [ ] 缓存热门生成结果（KV）
- [ ] 优化任务队列（Workers Queue）
- [ ] 等待 Suno/Udio 官方 API → 迁移至正规渠道

---

## 8. 注意事项

1. **法律合规**：Suno API 逆向调用不可用于商业产品，尽快关注 Suno 官方 API 开放。过渡期可用 MusicGen 开源自建。
2. **声音克隆伦理**：必须要求用户验证身份（上传本人录音验证），限制恶意克隆。防止被用于深度伪造欺诈。
3. **版权声明**：AI 生成作品标注来源（模型名称），付费用户获得使用权（非版权）。参考 Suno 付费版：订阅期间生成作品享商业使用权。
4. **GPU 成本优化**：可考虑 HuggingFace Inference API 免租 GPU（按调用量付费），或使用 UCloud/阿里云竞价 GPU。
5. **API Key 安全**：所有第三方 API Key 存为 Workers Secrets 或 Python 服务环境变量，前端绝不暴露。