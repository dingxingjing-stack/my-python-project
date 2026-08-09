"""Global Language Registry — data-driven capability map.

单一事实来源：把"语言能力"从业务 if-else 收敛为纯数据。
严格区分三个维度（产品可选语言 ≠ 歌词模型支持 ≠ 音乐模型支持），
其中 music_verified 必须由真实端到端测试确认后才为 True，禁止人为设置。

设计约定：
- 未知语言不 crash，按现有约定 fallback 到 zh。
- 加一种语言 = 在本文件 _REGISTRY 中加一行，核心生成流程零改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class LanguageCapability:
    """一种生成语言的完整能力描述。"""

    code: str
    display_name: str
    # 歌词通道
    lyrics_supported: bool
    lyrics_provider: str
    lyrics_prompt_name: str
    # 音乐通道
    music_supported: bool
    music_provider: str
    music_verified: bool  # True = 本项目已完成真实端到端测试
    heartmula_vocal_word: str
    # Fallback
    fallback_provider: Tuple[str, ...]
    mock_template: str = ""


# ── mock 歌词兜底模板（各语言预置；缺语言时回退英文/中文） ──
_MOCK_ZH = """Verse 1:
星光落在窗前
夜色温柔如水
心中那首歌谣
随风轻轻飘远

Chorus:
追梦的人永不疲倦
穿越风雨也从容
我们都在路上
奔向心中的光

LRC:
[00:00.00]星光落在窗前
[00:05.00]月色温柔如水
[00:10.00]心中那首歌谣
[00:15.00]随风轻轻飘远"""

_MOCK_EN = """Verse 1:
Starlight falls upon the garden
Night is gentle as the water
The song inside my soul
Drifts away with the wind

Chorus:
Dreamers never tire
Walking through the storm with grace
We are all on the road
Running toward the light within

LRC:
[00:00.00]Starlight falls upon the garden
[00:05.00]Night is gentle as the water
[00:10.00]The song inside my soul
[00:15.00]Drifts away with the wind"""

_MOCK_JA = """Verse 1:
星が窓辺に降り注ぐ
夜は水のように優しく
心の中の歌が
風に乗って遠くへ

Chorus:
夢を追う人は決して疲れない
嵐を越えて優雅に
僕たちはみんな旅の途中
心の光へ走り続ける

LRC:
[00:00.00]星が窓辺に降り注ぐ
[00:05.00]夜は水のように優しく
[00:10.00]心の中の歌が
[00:15.00]風に乗って遠くへ"""

_MOCK_KO = """Verse 1:
별빛이 창가에 내려와
밤은 물처럼 부드럽게
마음속의 그 노래가
바람을 타고 멀리

Chorus:
꿈을 찾는 우리는 멈추지 않아
폭풍을 가로질러
우린 모두 여정 중이야
마음속의 빛을 향해 달려가

LRC:
[00:00.00]별빛이 창가에 내려와
[00:05.00]밤은 물처럼 부드럽게
[00:10.00]마음속의 그 노래가
[00:15.00]바람을 타고 멀리 멀리"""

_MOCK_ES = """Verse 1:
La luz de las estrellas sobre la ventana
La noche es suave como el agua
La canción de mi corazón
Flota lejos con el viento

Chorus:
Los soñadores nunca se cansan
Atravesando la tormenta con gracia
Todos estamos en el camino
Corriendo hacia la luz interior

LRC:
[00:00.00]La luz de las estrellas
[00:05.00]La noche es suave como el agua
[00:10.00]La canción de mi corazón
[00:15.00]Flota lejos con el viento"""

_MOCK_PT = """Verse 1:
A luz das estrelas sobre a janela
A noite é suave como a água
A canção dentro do meu peito
Leva-me para longe com o vento

Chorus:
Os sonhadores nunca se cansam
Atravessando a tempestade com graça
Estamos todos na estrada
Correndo em direção à luz

LRC:
[00:00.00]A luz das estrelas
[00:05.00]A noite é suave como a água
[00:10.00]A canção dentro do meu peito
[00:15.00]Leva-me para longe com o vento"""

# 共用降级链（HeartMuLa 失败/未验证 → Mureka → HF → Mock）由 music.py 内部链路顺序保证，
# 这里只登记"该语言自身可用的兜底音频 provider 顺序"，仅供报告/调试参考。
_COMMON_FALLBACK: Tuple[str, ...] = ("mureka", "huggingface", "mock")


_REGISTRY: dict = {
    "zh": LanguageCapability(
        code="zh",
        display_name="中文",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="Chinese (简体中文)",
        music_supported=True,
        music_provider="heartmula",
        music_verified=True,  # Stage 6 端到端实测通过（Round1 zh 0.969 + 独立 cold zh 0.959）
        heartmula_vocal_word="Chinese",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_ZH,
    ),
    "en": LanguageCapability(
        code="en",
        display_name="English",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="English",
        music_supported=True,
        music_provider="heartmula",
        music_verified=False,
        heartmula_vocal_word="English",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_EN,
    ),
    "pt": LanguageCapability(
        code="pt",
        display_name="Português",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="Portuguese (Português)",
        music_supported=True,
        music_provider="heartmula",
        music_verified=True,  # 已由本项目端到端实测通过
        heartmula_vocal_word="Portuguese",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_PT,
    ),
    "es": LanguageCapability(
        code="es",
        display_name="Español",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="Spanish (Español)",
        music_supported=True,
        music_provider="heartmula",
        music_verified=False,
        heartmula_vocal_word="Spanish",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_ES,
    ),
    "ja": LanguageCapability(
        code="ja",
        display_name="日本語",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="Japanese (日本語)",
        music_supported=True,
        music_provider="heartmula",
        music_verified=False,
        heartmula_vocal_word="Japanese",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_JA,
    ),
    "ko": LanguageCapability(
        code="ko",
        display_name="한국어",
        lyrics_supported=True,
        lyrics_provider="openrouter",
        lyrics_prompt_name="Korean (한국어)",
        music_supported=True,
        music_provider="heartmula",
        music_verified=False,
        heartmula_vocal_word="Korean",
        fallback_provider=_COMMON_FALLBACK,
        mock_template=_MOCK_KO,
    ),
}

_DEFAULT_CODE = "zh"


def get(code: str) -> LanguageCapability:
    """按语言代码取能力；未知语言 fallback 到 zen-（不 crash）。"""
    key = (code or "").lower()
    return _REGISTRY.get(key, _REGISTRY[_DEFAULT_CODE])


def resolve_music_provider(code: str) -> str:
    """根据能力判断音乐通道：仅 music_verified=True 才允许走 HeartMuLa；
    未实测语言一律返回 external（维持现有降级链，不强制切 HeartMuLa）。"""
    cap = get(code)
    if cap.music_verified and cap.music_provider == "heartmula":
        return "heartmula"
    return "external"


def lyrics_prompt_name(code: str) -> str:
    """歌词生成时传给 LLM 的完整语言名；未知语言 fallback 到默认。"""
    return get(code).lyrics_prompt_name


def supported_codes() -> list:
    return sorted(_REGISTRY.keys())