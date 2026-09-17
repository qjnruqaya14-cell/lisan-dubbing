"""
languages.py — قائمة اللغات المدعومة وربطها بأكواد DeepL / LibreTranslate.

كل لغة لها:
  - code: المعرف الداخلي المستخدم بالواجهة والـ API
  - label: الاسم المعروض للمستخدم
  - deepl: كود DeepL المطابق، أو None إذا DeepL ما يدعمها
  - libre: كود ISO المستخدم مع LibreTranslate (بديل احتياطي لِلغات
           ما يدعمها DeepL، مثل التايلندية والفيتنامية)
"""

LANGUAGES = [
    {"code": "ar",    "label": "العربية",              "deepl": "AR",    "libre": "ar"},
    {"code": "en-US", "label": "English (US)",          "deepl": "EN-US", "libre": "en"},
    {"code": "en-GB", "label": "English (UK)",          "deepl": "EN-GB", "libre": "en"},
    {"code": "en-CA", "label": "English (Canada)",      "deepl": "EN-US", "libre": "en"},
    {"code": "es",    "label": "Español",               "deepl": "ES",    "libre": "es"},
    {"code": "fr",    "label": "Français",              "deepl": "FR",    "libre": "fr"},
    {"code": "it",    "label": "Italiano",              "deepl": "IT",    "libre": "it"},
    {"code": "tr",    "label": "Türkçe",                "deepl": "TR",    "libre": "tr"},
    {"code": "ru",    "label": "Русский",               "deepl": "RU",    "libre": "ru"},
    {"code": "sv",    "label": "Svenska",               "deepl": "SV",    "libre": "sv"},
    {"code": "pt-BR", "label": "Português (Brasil)",    "deepl": "PT-BR", "libre": "pt"},
    {"code": "ja",    "label": "日本語",                 "deepl": "JA",    "libre": "ja"},
    {"code": "zh",    "label": "中文",                   "deepl": "ZH",    "libre": "zh"},
    {"code": "ko",    "label": "한국어",                 "deepl": "KO",    "libre": "ko"},
    {"code": "th",    "label": "ไทย",                    "deepl": None,    "libre": "th"},
    {"code": "vi",    "label": "Tiếng Việt",             "deepl": None,    "libre": "vi"},
]

LANGUAGES_BY_CODE = {lang["code"]: lang for lang in LANGUAGES}


def get_language(code: str):
    return LANGUAGES_BY_CODE.get(code)
