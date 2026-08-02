"""i18n 国际化支持 — 服务端 gettext + 客户端无刷新切换。

支持语言: zh_CN / en / ja_JP / ko_KR / es_ES
ja/ko/es 使用内置翻译字典（无需编译 MO），zh_CN/en 使用 gettext MO 文件。
"""
from __future__ import annotations

import gettext
import json
import os
from pathlib import Path

from fastapi import APIRouter, Response, Request, Body
from fastapi.responses import JSONResponse

LOCALES_DIR = Path(__file__).parent / "locales"

_translations: dict[str, gettext.GNUTranslations] = {}

SUPPORTED_LOCALES = {"zh_CN", "en", "ja_JP", "ko_KR", "es_ES"}
DEFAULT_LOCALE = "en"

# locale 显示名（供语言切换器显示）
LOCALE_NAMES = {
    "en": "English",
    "zh_CN": "简体中文",
    "ja_JP": "日本語",
    "ko_KR": "한국어",
    "es_ES": "Español",
}

# ── 内置翻译字典（ja/ko/es，免编译 MO） ──
_BUILTIN_JA = {
    "Products": "製品",
    "Solutions": "ソリューション",
    "Pricing": "料金",
    "Developer Docs": "開発者ドキュメント",
    "Console": "コンソール",
    "Sign In": "ログイン",
    "Get Started": "はじめる",
    "Create": "作成",
    "Explore": "探索",
    "Library": "ライブラリ",
    "Templates": "テンプレート",
    "Settings": "設定",
    "AI Music Generation": "AI音楽生成",
    "Lyrics Creation": "歌詞作成",
    "Voice Synthesis": "音声合成",
    "MV Generation": "MV生成",
    "AI-Powered One-Stop Music Creation Platform": "AIワンストップ音楽制作プラットフォーム",
    "Try Music Generation Free": "無料で音楽を生成する",
    "View Enterprise Plan": "企業プランを見る",
    "No credit card required": "クレジットカード不要",
    "Daily free quota included": "毎日無料枠あり",
    "Core Capabilities": "主要機能",
    "What do you want to create?": "何を作成しますか？",
    "Song Language": "歌の言語",
    "Auto Detect": "自動検出",
    "中文": "中文",
    "English": "英語",
    "日本語": "日本語",
    "한국어": "한국어",
    "Genre": "ジャンル",
    "Pop": "ポップ",
    "Rock": "ロック",
    "Electronic": "エレクトロニック",
    "Hip Hop": "ヒップホップ",
    "R&B": "R&B",
    "Jazz": "ジャズ",
    "Classical": "クラシック",
    "Folk": "フォーク",
    "Mood": "ムード",
    "Any": "任意",
    "Happy": "明るい",
    "Sad": "切ない",
    "Energetic": "情熱的",
    "Calm": "落ち着いた",
    "Dark": "ダーク",
    "Romantic": "ロマンチック",
    "Vocal": "ボーカル",
    "Auto": "自動",
    "Male": "男性",
    "Female": "女性",
    "Generate Lyrics": "歌詞を生成",
    "Generating...": "生成中...",
    "Lyrics": "歌詞",
    "Title": "タイトル",
    "Regenerate": "再生成",
    "Continue": "続ける",
    "Music Settings": "音楽設定",
    "Versions": "バージョン",
    "1 Version": "1バージョン",
    "2 Versions": "2バージョン",
    "Analyzing lyrics": "歌詞を分析中",
    "Creating composition": "作曲中",
    "Generating vocals": "ボーカル生成中",
    "Finalizing audio": "音声仕上げ中",
    "Generated Versions": "生成バージョン",
    "Selected": "選択中",
    "Continue to Visuals": "ビジュアルへ",
    "Visuals": "ビジュアル",
    "Cover Art": "カバーアート",
    "Music Video": "ミュージックビデオ",
    "Describe your song to get started": "曲を説明して始めましょう",
    "The preview will appear here": "プレビューがここに表示されます",
    "Generate visuals to preview": "プレビュー用にビジュアルを生成",
    "Ready to publish your creation": "作品を公開する準備ができました",
    "Turn your idea": "あなたのアイデアを",
    "into music": "音楽に",
    "How It Works": "仕組み",
    "Describe": "説明",
    "Tell us what song you want in one sentence": "一言で作りたい曲を教えてください",
    "AI generates lyrics, you can edit them": "AIが歌詞を生成、編集も可能",
    "Music": "音楽",
    "AI creates the song with vocals": "AIがボーカル付きの曲を作成",
    "Video": "ビデオ",
    "Generate a music video automatically": "ミュージックビデオを自動生成",
    "Discover Music": "音楽を探す",
    "Search tracks...": "曲を検索...",
    "Searching...": "検索中...",
    "Featured Creations": "注目作品",
    "View All": "すべて表示",
    "Template Library": "テンプレートライブラリ",
    "One-click remix any template": "テンプレートをワンクリックでリミックス",
    "No templates yet": "まだテンプレートがありません",
    "Create New": "新規作成",
    "Remix": "リミックス",
    "Language": "言語",
    "Español": "スペイン語",
}

_BUILTIN_KO = {
    "Products": "제품",
    "Solutions": "솔루션",
    "Pricing": "요금",
    "Developer Docs": "개발자 문서",
    "Console": "콘솔",
    "Sign In": "로그인",
    "Get Started": "시작하기",
    "Create": "만들기",
    "Explore": "탐색",
    "Library": "라이브러리",
    "Templates": "템플릿",
    "Settings": "설정",
    "AI Music Generation": "AI 음악 생성",
    "Lyrics Creation": "가사 작성",
    "Voice Synthesis": "음성 합성",
    "MV Generation": "MV 생성",
    "AI-Powered One-Stop Music Creation Platform": "AI 원스톱 음악 제작 플랫폼",
    "Try Music Generation Free": "무료로 음악 만들기",
    "View Enterprise Plan": "기업 플랜 보기",
    "No credit card required": "신용카드 불필요",
    "Daily free quota included": "매일 무료 할당량 포함",
    "Core Capabilities": "핵심 기능",
    "What do you want to create?": "무엇을 만들고 싶으신가요?",
    "Song Language": "노래 언어",
    "Auto Detect": "자동 감지",
    "中文": "중국어",
    "English": "영어",
    "日本語": "일본어",
    "한국어": "한국어",
    "Genre": "장르",
    "Pop": "팝",
    "Rock": "록",
    "Electronic": "일렉트로닉",
    "Hip Hop": "힙합",
    "R&B": "R&B",
    "Jazz": "재즈",
    "Classical": "클래식",
    "Folk": "포크",
    "Mood": "분위기",
    "Any": "모두",
    "Happy": "행복한",
    "Sad": "슬픈",
    "Energetic": "에너지 넘치는",
    "Calm": "차분한",
    "Dark": "다크",
    "Romantic": "로맨틱",
    "Vocal": "보컬",
    "Auto": "자동",
    "Male": "남성",
    "Female": "여성",
    "Generate Lyrics": "가사 생성",
    "Generating...": "생성 중...",
    "Lyrics": "가사",
    "Title": "제목",
    "Regenerate": "다시 생성",
    "Continue": "계속",
    "Music Settings": "음악 설정",
    "Versions": "버전",
    "1 Version": "버전 1개",
    "2 Versions": "버전 2개",
    "Analyzing lyrics": "가사 분석 중",
    "Creating composition": "작곡 중",
    "Generating vocals": "보컬 생성 중",
    "Finalizing audio": "오디오 마무리 중",
    "Generated Versions": "생성된 버전",
    "Selected": "선택됨",
    "Continue to Visuals": "비주얼로 계속",
    "Visuals": "비주얼",
    "Cover Art": "커버 아트",
    "Music Video": "뮤직 비디오",
    "Describe your song to get started": "노래를 설명하여 시작하세요",
    "The preview will appear here": "미리보기가 여기에 표시됩니다",
    "Generate visuals to preview": "미리보기용 비주얼 생성",
    "Ready to publish your creation": "작품을 게시할 준비가 되었습니다",
    "Turn your idea": "당신의 아이디어를",
    "into music": "음악으로",
    "How It Works": "작동 방식",
    "Describe": "설명",
    "Tell us what song you want in one sentence": "한 문장으로 원하는 노래를 알려주세요",
    "AI generates lyrics, you can edit them": "AI가 가사를 생성하고 편집할 수 있습니다",
    "Music": "음악",
    "AI creates the song with vocals": "AI가 보컬이 있는 노래를 만듭니다",
    "Video": "비디오",
    "Generate a music video automatically": "뮤직 비디오 자동 생성",
    "Discover Music": "음악 탐색",
    "Search tracks...": "곡 검색...",
    "Searching...": "검색 중...",
    "Featured Creations": "추천 작품",
    "View All": "전체 보기",
    "Template Library": "템플릿 라이브러리",
    "One-click remix any template": "템플릿을 원클릭으로 리믹스",
    "No templates yet": "아직 템플릿이 없습니다",
    "Create New": "새로 만들기",
    "Remix": "리믹스",
    "Language": "언어",
    "Español": "스페인어",
}

_BUILTIN_ES = {
    "Products": "Productos",
    "Solutions": "Soluciones",
    "Pricing": "Precios",
    "Developer Docs": "Documentación",
    "Console": "Consola",
    "Sign In": "Iniciar sesión",
    "Get Started": "Comenzar",
    "Create": "Crear",
    "Explore": "Explorar",
    "Library": "Biblioteca",
    "Templates": "Plantillas",
    "Settings": "Ajustes",
    "AI Music Generation": "Generación de música con IA",
    "Lyrics Creation": "Creación de letras",
    "Voice Synthesis": "Síntesis de voz",
    "MV Generation": "Generación de videoclip",
    "AI-Powered One-Stop Music Creation Platform": "Plataforma de creación musical integral con IA",
    "Try Music Generation Free": "Prueba la generación de música gratis",
    "View Enterprise Plan": "Ver plan empresarial",
    "No credit card required": "Sin tarjeta de crédito",
    "Daily free quota included": "Cuota gratuita diaria incluida",
    "Core Capabilities": "Capacidades principales",
    "What do you want to create?": "¿Qué quieres crear?",
    "Song Language": "Idioma de la canción",
    "Auto Detect": "Detección automática",
    "中文": "Chino",
    "English": "Inglés",
    "日本語": "Japonés",
    "한국어": "Coreano",
    "Genre": "Género",
    "Pop": "Pop",
    "Rock": "Rock",
    "Electronic": "Electrónica",
    "Hip Hop": "Hip Hop",
    "R&B": "R&B",
    "Jazz": "Jazz",
    "Classical": "Clásica",
    "Folk": "Folk",
    "Mood": "Estado de ánimo",
    "Any": "Cualquiera",
    "Happy": "Feliz",
    "Sad": "Triste",
    "Energetic": "Energético",
    "Calm": "Tranquilo",
    "Dark": "Oscuro",
    "Romantic": "Romántico",
    "Vocal": "Voz",
    "Auto": "Automático",
    "Male": "Masculina",
    "Female": "Femenina",
    "Generate Lyrics": "Generar letras",
    "Generating...": "Generando...",
    "Lyrics": "Letras",
    "Title": "Título",
    "Regenerate": "Regenerar",
    "Continue": "Continuar",
    "Music Settings": "Ajustes de música",
    "Versions": "Versiones",
    "1 Version": "1 versión",
    "2 Versions": "2 versiones",
    "Analyzing lyrics": "Analizando letras",
    "Creating composition": "Creando composición",
    "Generating vocals": "Generando voz",
    "Finalizing audio": "Finalizando audio",
    "Generated Versions": "Versiones generadas",
    "Selected": "Seleccionado",
    "Continue to Visuals": "Continuar a visuales",
    "Visuals": "Visuales",
    "Cover Art": "Portada",
    "Music Video": "Videoclip musical",
    "Describe your song to get started": "Describe tu canción para comenzar",
    "The preview will appear here": "La vista previa aparecerá aquí",
    "Generate visuals to preview": "Generar visuales para previsualizar",
    "Ready to publish your creation": "Listo para publicar tu creación",
    "Turn your idea": "Convierte tu idea",
    "into music": "en música",
    "How It Works": "Cómo funciona",
    "Describe": "Describe",
    "Tell us what song you want in one sentence": "Dinos en una frase qué canción quieres",
    "AI generates lyrics, you can edit them": "La IA genera letras, puedes editarlas",
    "Music": "Música",
    "AI creates the song with vocals": "La IA crea la canción con voz",
    "Video": "Vídeo",
    "Generate a music video automatically": "Genera un videoclip automáticamente",
    "Discover Music": "Descubre música",
    "Search tracks...": "Buscar canciones...",
    "Searching...": "Buscando...",
    "Featured Creations": "Creaciones destacadas",
    "View All": "Ver todo",
    "Template Library": "Biblioteca de plantillas",
    "One-click remix any template": "Remezcla cualquier plantilla con un clic",
    "No templates yet": "Aún no hay plantillas",
    "Create New": "Crear nuevo",
    "Remix": "Remezclar",
    "Language": "Idioma",
    "Español": "Español",
}

_BUILTIN = {
    "ja_JP": _BUILTIN_JA,
    "ko_KR": _BUILTIN_KO,
    "es_ES": _BUILTIN_ES,
}

router = APIRouter(tags=["i18n"])


def _load(locale: str) -> gettext.GNUTranslations:
    if locale not in _translations:
        try:
            _translations[locale] = gettext.translation(
                "messages", LOCALES_DIR, languages=[locale],
            )
        except FileNotFoundError:
            _translations[locale] = gettext.NullTranslations()
    return _translations[locale]


def detect_locale(request) -> str:
    cookie = request.cookies.get("lang")
    if cookie in SUPPORTED_LOCALES:
        return cookie
    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        lang = part.split(";")[0].strip().replace("-", "_")
        if lang.startswith("zh"):
            return "zh_CN"
        if lang.startswith("en"):
            return "en"
        if lang.startswith("ja"):
            return "ja_JP"
        if lang.startswith("ko"):
            return "ko_KR"
        if lang.startswith("es"):
            return "es_ES"
    return DEFAULT_LOCALE


def make_gettext(locale: str):
    t = _load(locale)
    return t.gettext


def _merged_translations(locale: str) -> dict[str, str]:
    """合并 gettext 目录翻译 + 内置字典，内置字典优先。"""
    result: dict[str, str] = {}
    t = _load(locale)
    if hasattr(t, "_catalog"):
        for msgid, msgstr in t._catalog.items():
            if isinstance(msgid, str) and msgstr:
                result[msgid] = msgstr
    if locale in _BUILTIN:
        result.update(_BUILTIN[locale])
    return result


def i18n_context(request) -> dict:
    locale = detect_locale(request)
    return {
        "_": make_gettext(locale),
        "current_locale": locale,
        "translations_json": build_translations_json(),
        "supported_locales": SUPPORTED_LOCALES,
        "locale_names": LOCALE_NAMES,
    }


def build_translations_json() -> str:
    """构建所有语言的翻译 JSON 字符串，供前端 Alpine.js 使用。"""
    result = {}
    for locale in SUPPORTED_LOCALES:
        result[locale] = _merged_translations(locale)
    return json.dumps(result, ensure_ascii=False)


@router.get("/api/v1/lang/current")
async def get_current_lang(request: Request):
    locale = detect_locale(request)
    return {"locale": locale}


@router.post("/api/v1/lang/set")
async def set_lang(locale: str = Body(..., embed=True), response: Response = Response()):
    if locale not in SUPPORTED_LOCALES:
        return JSONResponse({"error": "unsupported locale"}, status_code=400)
    response.set_cookie(key="lang", value=locale, path="/", max_age=365 * 86400)
    return {"locale": locale, "message": "ok"}


@router.get("/api/v1/lang/translations")
async def get_translations():
    return JSONResponse(content=json.loads(build_translations_json()))