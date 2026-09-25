"""分词。

中文没有空白可切：整句变成一个 token 的话，问句与文档永远没有公共词，
BM25 与词表覆盖率全部失效。这里把连续汉字切成二元组（bigram），
拉丁字母与数字串保持整词；文档侧与查询侧走同一个函数，天然一致。
"""

from __future__ import annotations

import re
import unicodedata

#: 分词规则变了，索引缓存必须失效。
TOKENIZER_VERSION = "tokenizer-3-bigram"

#: 中文里几乎不携带信息的字。只用在“查询覆盖率”上，索引照常保留全部词。
STOP_CHARS = frozenset("的了吗呢是在有和与及或就都也还把被给对从向于个些这那哪什么怎样如何多少几请帮我你他它可以能要想会一下少吧啊呀们么样过得着为所")
STOP_WORDS = frozenset("the a an of to in is are and or for on at it this that how what".split())

#: 连续的拉丁字母/数字（含小数点）作为整词；连续的汉字切成二元组。
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9.\-]*|[\u4e00-\u9fff]+")


def normalise(text: str) -> str:
    """全角转半角、统一大小写，比较与分词都走这一层。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str) -> list[str]:
    """拉丁/数字整词 + 汉字二元组，喂给 BM25。"""
    tokens: list[str] = []
    for match in _SEGMENT.finditer(normalise(text)):
        segment = match.group()
        if segment[0].isascii():
            tokens.append(segment)
            continue
        if len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return tokens


def content_tokens(text: str) -> list[str]:
    """去掉虚词之后的查询词，用来算“这个问题被文档覆盖了多少”。"""
    kept = []
    for token in tokenize(text):
        if token in STOP_WORDS:
            continue
        if all(char in STOP_CHARS for char in token):
            continue
        kept.append(token)
    return kept
