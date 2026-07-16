"""共用字串正規化模組。

提供兩個純函式:

- ``normalize_display(s)``:實際存入資料庫的顯示形式。
- ``normalize_key(s)``:唯一性比較用的鍵(display 再英文 casefold)。

無任何相依,僅使用標準庫 ``re`` 與 ``unicodedata``。
"""

import re
import unicodedata

# 內部連續空白(半形空白、tab、全形空白 U+3000 等)壓成一個半形空白用。
# NFKC 已將 U+3000 轉為半形空白,仍明確列入以防萬一。
_WHITESPACE_RE = re.compile(r"[\s　]+")

# 數字(可含小數點)後緊接空白再接英文字母時,移除該空白。
# 例:200 cm→200cm、20 W→20W、1.5 m→1.5m。
# 僅限「數字後接英文字母」;英文後接數字(iPhone 15)或數字後接中文(5 個)不動。
_DIGIT_UNIT_SPACE_RE = re.compile(r"(?<=\d)[\s　]+(?=[A-Za-z])")


def normalize_display(s: str) -> str:
    """回傳存入資料庫的正規化顯示字串。

    步驟:
    1. Unicode NFKC:全形英數符號轉半形、相容字統一。
    2. 去除頭尾空白(含全形空白 U+3000、tab)。
    3. 內部連續空白(含 tab、全形空白)壓成一個半形空白;單一空白保留
       (中文分詞有意義)。
    4. 移除「數字與緊接其後的英文字母單位」之間的空白
       (200 cm→200cm、20 W→20W、1.5 m→1.5m);
       英文後接數字或數字後接中文的情形不動。
    5. 保留原大小寫。
    """
    # 步驟 1:NFKC
    text = unicodedata.normalize("NFKC", s)
    # 步驟 2:去頭尾空白(str.strip 涵蓋 tab 與 U+3000)
    text = text.strip()
    # 步驟 3:內部連續空白壓成一個半形空白
    text = _WHITESPACE_RE.sub(" ", text)
    # 步驟 4:移除數字與其後英文字母單位之間的空白
    text = _DIGIT_UNIT_SPACE_RE.sub("", text)
    # 步驟 5:大小寫保持不變
    return text


def normalize_key(s: str) -> str:
    """回傳唯一性比較鍵。

    等同 ``normalize_display`` 後再對英文做 ``str.casefold``,
    使 type-c 與 Type-C 產生相同的比較鍵(中文不受影響)。
    """
    return normalize_display(s).casefold()
