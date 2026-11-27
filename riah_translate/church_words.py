"""Built-in church words: fixes for Bible names and church terms the translator gets wrong.

Found by running sermon sentences through the translator (tests/mt_probe.py) and checking the result
against the usual Protestant Bibles (Thai Standard Version / 1971, Chinese Union Version). A Thai and
a Chinese speaker should still look these over. The operator's own fixes (the Words box) are applied
after these, so they always win.

* `exact`: short church phrases that are said on their own ("Amen.", "Hallelujah!"), with the words
  churches use. The translator gets several of them badly wrong (Amen came out as "may we go together").
* `fix`: find -> replace on the translation, for names and terms the translator spells its own way.
  Only words that can't mean anything else go here: "Mark" could be a person, "favor" and
  "mercy" aren't "grace", and "thank goodness" isn't "Hallelujah", so those stay as translated.
"""
from __future__ import annotations

import re

EXACT = {
    "amen": dict(th="อาเมน", zh="阿们", **{"zh-TW": "阿們"}),
    "church": dict(th="คริสตจักร", zh="教会", **{"zh-TW": "教會"}),   # "Good morning. Church." came out as "church building"
    "amen amen": dict(th="อาเมน อาเมน", zh="阿们，阿们", **{"zh-TW": "阿們，阿們"}),
    "hallelujah": dict(th="ฮาเลลูยา", zh="哈利路亚", **{"zh-TW": "哈利路亞"}),
    "alleluia": dict(th="ฮาเลลูยา", zh="哈利路亚", **{"zh-TW": "哈利路亞"}),
    "praise the lord": dict(th="สรรเสริญพระเจ้า", zh="赞美主", **{"zh-TW": "讚美主"}),
    "praise god": dict(th="สรรเสริญพระเจ้า", zh="赞美神", **{"zh-TW": "讚美神"}),
    "thank you jesus": dict(th="ขอบคุณพระเยซู", zh="感谢耶稣", **{"zh-TW": "感謝耶穌"}),
    "thank you lord": dict(th="ขอบคุณพระเจ้า", zh="感谢主", **{"zh-TW": "感謝主"}),
    "let us pray": dict(th="ให้เราอธิษฐาน", zh="让我们祷告", **{"zh-TW": "讓我們禱告"}),
    "lets pray": dict(th="ให้เราอธิษฐาน", zh="让我们祷告", **{"zh-TW": "讓我們禱告"}),
    "god bless you": dict(th="ขอพระเจ้าอวยพรท่าน", zh="愿神祝福你", **{"zh-TW": "願神祝福你"}),
    "in jesus name amen": dict(th="ในพระนามพระเยซู อาเมน", zh="奉耶稣的名，阿们", **{"zh-TW": "奉耶穌的名，阿們"}),
    "in jesus name we pray amen": dict(th="ในพระนามพระเยซูเราอธิษฐาน อาเมน", zh="奉耶稣的名祷告，阿们",
                                       **{"zh-TW": "奉耶穌的名禱告，阿們"}),
}

FIX = {
    "th": [
        # Bible books and names (Thai Bible spelling)
        ["หนังสือประกอบพรรษา", "หนังสือปฐมกาล"],
        ["ยะซายา", "อิสยาห์"],
        ["เอเฟโซ", "เอเฟซัส"],
        ["ฟิลิปเปีย", "ฟีลิปปี"],
        ["มัดธาย", "มัทธิว"],
        ["ลูคา", "ลูกา"],
        ["โยฮัน", "ยอห์น"],
        ["อับราฮาม", "อับราฮัม"],
        ["นิโกรดมัส", "นิโคเดมัส"],                     # Nicodemus came out starting with an offensive word
        ["นิโกรเดมัส", "นิโคเดมัส"],
        ["นิโกร", "นิโคร"],                             # ...so that word never reaches the screen at all
        ["โกเลียต", "โกลิอัท"],
        # church terms
        ["ชอร์ช", "คริสตจักร"],                         # "Good morning, church" came out as English in Thai letters
        ["เชิร์ช", "คริสตจักร"],
        ["เชอร์ช", "คริสตจักร"],
        ["อเมน", "อาเมน"],
        ["แกะหมา", "ลูกแกะ"],                          # Lamb of God came out as "sheepdog"
        ["เกียรติของพระเจ้า", "พระสิริของพระเจ้า"],        # glory of God
        ["เคารพพระเจ้า", "นมัสการพระเจ้า"],              # worship God
        ["ปฎิเสธ", "ปฏิเสธ"],                          # common misspelling
        # Thai places the translator leaves in English letters
        ["Chiang Mai", "เชียงใหม่"],
        ["Chiang Rai", "เชียงราย"],
        ["Bangkok", "กรุงเทพฯ"],
    ],
    "zh": [
        ["约翰书", "约翰福音"],
        ["哥利亚特", "歌利亚"],
        ["这话成为肉身", "道成了肉身"],
    ],
}


def phrase_key(text: str) -> str:
    """'In Jesus' name, amen!' -> 'in jesus name amen' (how EXACT is looked up)."""
    t = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def church_glossary(mine: dict | None = None) -> dict:
    """The built-in words plus the operator's own (theirs applied last, so they win)."""
    mine = mine or {}
    fix = {k: [list(p) for p in v] for k, v in FIX.items()}
    for k, pairs in (mine.get("fix") or {}).items():
        fix.setdefault(k, []).extend(pairs or [])
    exact = dict(EXACT)
    exact.update(mine.get("exact") or {})
    return dict(hints=mine.get("hints", ""), fix=fix, exact=exact)
