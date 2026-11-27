// Built-in church words, the same as the app (riah_translate/church_words.py): short church phrases
// with the words churches use, and find -> replace fixes for names and terms the translator spells
// its own way. The operator's own fixes are applied after these, so they always win.

const zh = (s, t) => ({ zh: s, "zh-TW": t });

export const EXACT = {
  "amen": { th: "อาเมน", ...zh("阿们", "阿們") },
  "church": { th: "คริสตจักร", ...zh("教会", "教會") },   // "Good morning. Church." came out as "church building"
  "amen amen": { th: "อาเมน อาเมน", ...zh("阿们，阿们", "阿們，阿們") },
  "hallelujah": { th: "ฮาเลลูยา", ...zh("哈利路亚", "哈利路亞") },
  "alleluia": { th: "ฮาเลลูยา", ...zh("哈利路亚", "哈利路亞") },
  "praise the lord": { th: "สรรเสริญพระเจ้า", ...zh("赞美主", "讚美主") },
  "praise god": { th: "สรรเสริญพระเจ้า", ...zh("赞美神", "讚美神") },
  "thank you jesus": { th: "ขอบคุณพระเยซู", ...zh("感谢耶稣", "感謝耶穌") },
  "thank you lord": { th: "ขอบคุณพระเจ้า", ...zh("感谢主", "感謝主") },
  "let us pray": { th: "ให้เราอธิษฐาน", ...zh("让我们祷告", "讓我們禱告") },
  "lets pray": { th: "ให้เราอธิษฐาน", ...zh("让我们祷告", "讓我們禱告") },
  "god bless you": { th: "ขอพระเจ้าอวยพรท่าน", ...zh("愿神祝福你", "願神祝福你") },
  "in jesus name amen": { th: "ในพระนามพระเยซู อาเมน", ...zh("奉耶稣的名，阿们", "奉耶穌的名，阿們") },
  "in jesus name we pray amen": { th: "ในพระนามพระเยซูเราอธิษฐาน อาเมน", ...zh("奉耶稣的名祷告，阿们", "奉耶穌的名禱告，阿們") },
};

export const FIX = {
  th: [
    // Bible books and names (Thai Bible spelling)
    ["หนังสือประกอบพรรษา", "หนังสือปฐมกาล"],
    ["ยะซายา", "อิสยาห์"],
    ["เอเฟโซ", "เอเฟซัส"],
    ["ฟิลิปเปีย", "ฟีลิปปี"],
    ["มัดธาย", "มัทธิว"],
    ["ลูคา", "ลูกา"],
    ["โยฮัน", "ยอห์น"],
    ["อับราฮาม", "อับราฮัม"],
    ["นิโกรดมัส", "นิโคเดมัส"],          // Nicodemus came out starting with an offensive word
    ["นิโกรเดมัส", "นิโคเดมัส"],
    ["นิโกร", "นิโคร"],                  // ...so that word never reaches the screen at all
    ["โกเลียต", "โกลิอัท"],
    // church terms
    ["ชอร์ช", "คริสตจักร"],              // "Good morning, church" came out as English in Thai letters
    ["เชิร์ช", "คริสตจักร"],
    ["เชอร์ช", "คริสตจักร"],
    ["อเมน", "อาเมน"],
    ["แกะหมา", "ลูกแกะ"],               // Lamb of God came out as "sheepdog"
    ["เกียรติของพระเจ้า", "พระสิริของพระเจ้า"],   // glory of God
    ["เคารพพระเจ้า", "นมัสการพระเจ้า"],         // worship God
    ["ปฎิเสธ", "ปฏิเสธ"],               // common misspelling
    // Thai places the translator leaves in English letters
    ["Chiang Mai", "เชียงใหม่"],
    ["Chiang Rai", "เชียงราย"],
    ["Bangkok", "กรุงเทพฯ"],
  ],
  zh: [
    ["约翰书", "约翰福音"],
    ["哥利亚特", "歌利亚"],
    ["这话成为肉身", "道成了肉身"],
  ],
};

// "In Jesus' name, amen!" -> "in jesus name amen" (how EXACT is looked up). Python's \w is Unicode.
export function phraseKey(text) {
  const t = String(text).toLowerCase().replace(/['’]/g, "");
  return t.replace(/[^\p{L}\p{N}\p{M}_\s]/gu, " ").replace(/\s+/g, " ").trim();
}

// The built-in words plus the operator's own (theirs applied last, so they win).
export function churchGlossary(mine = {}) {
  const fix = {};
  for (const [k, v] of Object.entries(FIX)) fix[k] = v.map((p) => [...p]);
  for (const [k, pairs] of Object.entries(mine.fix || {})) (fix[k] ||= []).push(...(pairs || []));
  return { hints: mine.hints || "", fix, exact: { ...EXACT, ...(mine.exact || {}) } };
}
