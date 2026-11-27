"""Languages the app knows about.

`whisper` is the speech-recognition code (None = can be shown as a translation but not listened to).
`nllb` is the translation model's code. `spaced` says whether words are separated by spaces, which
changes how a long line is measured and cut.
"""

LANGS = {
    "en": dict(name="English", native="English", whisper="en", nllb="eng_Latn", spaced=True),
    "th": dict(name="Thai", native="ไทย", whisper="th", nllb="tha_Thai", spaced=False),
    "zh": dict(name="Chinese (Simplified)", native="中文", whisper="zh", nllb="zho_Hans", spaced=False),
    "zh-TW": dict(name="Chinese (Traditional)", native="繁體中文", whisper="zh", nllb="zho_Hant", spaced=False),
    "my": dict(name="Burmese", native="မြန်မာ", whisper="my", nllb="mya_Mymr", spaced=False),
    "shn": dict(name="Shan", native="တႆး", whisper=None, nllb="shn_Mymr", spaced=False),
    "lo": dict(name="Lao", native="ລາວ", whisper="lo", nllb="lao_Laoo", spaced=False),
    "km": dict(name="Khmer", native="ខ្មែរ", whisper="km", nllb="khm_Khmr", spaced=False),
    "vi": dict(name="Vietnamese", native="Tiếng Việt", whisper="vi", nllb="vie_Latn", spaced=True),
    "ko": dict(name="Korean", native="한국어", whisper="ko", nllb="kor_Hang", spaced=True),
    "ja": dict(name="Japanese", native="日本語", whisper="ja", nllb="jpn_Jpan", spaced=False),
    "id": dict(name="Indonesian", native="Bahasa Indonesia", whisper="id", nllb="ind_Latn", spaced=True),
    "tl": dict(name="Tagalog", native="Tagalog", whisper="tl", nllb="tgl_Latn", spaced=True),
    "hi": dict(name="Hindi", native="हिन्दी", whisper="hi", nllb="hin_Deva", spaced=True),
    "es": dict(name="Spanish", native="Español", whisper="es", nllb="spa_Latn", spaced=True),
    "fr": dict(name="French", native="Français", whisper="fr", nllb="fra_Latn", spaced=True),
    "de": dict(name="German", native="Deutsch", whisper="de", nllb="deu_Latn", spaced=True),
    "pt": dict(name="Portuguese", native="Português", whisper="pt", nllb="por_Latn", spaced=True),
    "ru": dict(name="Russian", native="Русский", whisper="ru", nllb="rus_Cyrl", spaced=True),
}


def lang(code):
    if code not in LANGS:
        raise ValueError(f"Unknown language '{code}'. Known: {', '.join(LANGS)}")
    return LANGS[code]


def default_whisper_model(src):
    """English has a small, fast English-only model. Everything else needs the bigger multilingual
    one to be usable (small multilingual models are weak on Thai and Burmese)."""
    return "small.en" if src == "en" else "large-v3-turbo"


def public_list():
    return [dict(code=k, name=v["name"], native=v["native"], can_listen=bool(v["whisper"]))
            for k, v in LANGS.items()]
