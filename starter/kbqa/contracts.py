"""发布前的契约硬限额。超限拒答而不是切掉证据后保留原结论。"""
import json
import re
import unicodedata
from .schemas import Answer
from .docfacts import _normalize_quote

# 日期、时刻、业务编号不属于经营数字。计数策略略保守，避免超限输出。
_MASK = re.compile(r'KB-\d+|\b[A-Za-z]{1,3}\d{2,}\b|\d{4}[-/]\d{1,2}[-/]\d{1,2}|'
                   r'\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*[日号])?|'
                   r'(?<![\d个半])\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?|'
                   r'(?<![\d个半])\d{1,2}\s*[日号](?!\d)|\d{1,2}:\d{2}(?::\d{2})?')
_NUM = re.compile(r'-?\d+(?:\.\d+)?')

def numbers(text):
    text = _MASK.sub(' ', unicodedata.normalize('NFKC', text))
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    return [float(n) for n in _NUM.findall(text)]

def enforce_limits(answer, trace):
    errors = []
    if len(answer.answer) > 1200:
        errors.append('answer exceeds 1200 characters')
    if len(set(numbers(answer.answer))) > 20:
        errors.append('answer exceeds 20 different numbers')
    if len({c['doc_id'] for c in answer.citations}) > 4:
        errors.append('too many cited documents')
    if any(len(_normalize_quote(c['quote'])) > 400 for c in answer.citations):
        errors.append('quote exceeds 400 normalized characters')
    count = 0
    for item in answer.data_evidence:
        blob = json.dumps(item['result'], ensure_ascii=False)
        if len(blob.encode('utf-8')) > 4096:
            errors.append('evidence result exceeds 4096 bytes')
        count += len(numbers(blob))
    if count > 60:
        errors.append('evidence exceeds 60 numbers')
    if not errors:
        return answer
    trace.step('response_limit', {'errors': errors})
    return Answer(answer='本次结果过多，无法在证据上限内完整呈现。请缩短日期范围或限定门店、商品后再查询。',
                  answer_type='refusal', notes=errors)
