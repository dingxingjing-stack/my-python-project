"""i18n 国际化支持 — gettext + babel."""
from __future__ import annotations

import gettext
import os
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"

# 缓存已加载的翻译对象
_translations: dict[str, gettext.GNUTranslations] = {}

SUPPORTED_LOCALES = {"zh_CN", "en"}
DEFAULT_LOCALE = "en"


def _load(locale: str) -> gettext.GNUTranslations:
    """加载指定语言包，缓存。"""
    if locale not in _translations:
        try:
            _translations[locale] = gettext.translation(
                "messages", LOCALES_DIR, languages=[locale],
            )
        except FileNotFoundError:
            _translations[locale] = gettext.NullTranslations()
    return _translations[locale]


def detect_locale(request) -> str:
    """从 Cookie 或 Accept-Language 头检测用户语言。"""
    cookie = request.cookies.get("lang")
    if cookie in SUPPORTED_LOCALES:
        return cookie
    # Accept-Language 头
    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        lang = part.split(";")[0].strip().replace("-", "_")
        if lang.startswith("zh"):
            return "zh_CN"
        if lang.startswith("en"):
            return "en"
    return DEFAULT_LOCALE


def make_gettext(locale: str):
    """生成当前语言环境的 _() 函数。"""
    t = _load(locale)
    return t.gettext


def i18n_context(request) -> dict:
    """注入模板的 i18n 上下文变量。"""
    locale = detect_locale(request)
    return {
        "_": make_gettext(locale),
        "current_locale": locale,
    }
