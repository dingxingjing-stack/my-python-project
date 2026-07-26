"""i18n 国际化支持 — 服务端 gettext + 客户端无刷新切换。"""
from __future__ import annotations

import gettext
import json
import os
from pathlib import Path

from fastapi import APIRouter, Response, Request, Body
from fastapi.responses import JSONResponse

LOCALES_DIR = Path(__file__).parent / "locales"

_translations: dict[str, gettext.GNUTranslations] = {}

SUPPORTED_LOCALES = {"zh_CN", "en"}
DEFAULT_LOCALE = "en"

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
    return DEFAULT_LOCALE


def make_gettext(locale: str):
    t = _load(locale)
    return t.gettext


def i18n_context(request) -> dict:
    locale = detect_locale(request)
    return {
        "_": make_gettext(locale),
        "current_locale": locale,
        "translations_json": build_translations_json(),
    }


def build_translations_json() -> str:
    """构建所有语言的翻译 JSON 字符串，供前端 Alpine.js 使用。"""
    result = {}
    for locale in SUPPORTED_LOCALES:
        t = _load(locale)
        result[locale] = {}
        if hasattr(t, "_catalog"):
            for msgid, msgstr in t._catalog.items():
                if isinstance(msgid, str) and msgstr:
                    result[locale][msgid] = msgstr
    return json.dumps(result, ensure_ascii=False)


@router.get("/api/v1/lang/current")
async def get_current_lang(request: Request):
    locale = detect_locale(request)
    return {"locale": locale}


@router.post("/api/v1/lang/set")
async def set_lang(locale: str = Body(..., embed=True), response: Response = Response()):
    if locale not in SUPPORTED_LOCALES:
        JSONResponse({"error": "unsupported locale"}, status_code=400)
    response.set_cookie(key="lang", value=locale, path="/", max_age=365 * 86400)
    return {"locale": locale, "message": "ok"}


@router.get("/api/v1/lang/translations")
async def get_translations():
    import json
    return JSONResponse(content=json.loads(build_translations_json()))
