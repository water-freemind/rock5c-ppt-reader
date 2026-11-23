"""Validate model suggestions against the exact OCR input before showing them."""
import json
import re


def read_list(raw, key):
    value = json.loads(re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', raw.strip()))
    if isinstance(value, dict):
        value = value.get(key)
    if not isinstance(value, list):
        raise ValueError('模型没有返回有效的列表')
    return value


def parse_review(raw, source):
    edits = []
    for item in read_list(raw, 'edits')[:6]:
        if not isinstance(item, dict):
            continue
        original, replacement, reason = (item.get(key) for key in ('original', 'replacement', 'reason'))
        if not all(isinstance(value, str) and value.strip() for value in (original, replacement, reason)):
            continue
        if (len(original) > 80 or len(replacement) > 80 or original == replacement
                or '\n' in original+replacement or re.search(r'\d', original+replacement)):
            continue
        start = source.find(original)
        if start < 0 or source.find(original, start+1) >= 0:
            continue
        edit = dict(original=original, replacement=replacement, reason=reason[:150])
        if edit not in edits:
            edits.append(edit)
    return edits


def parse_knowledge(raw):
    notes = list(dict.fromkeys(note.strip() for note in read_list(raw, 'notes')[:3]
                              if isinstance(note, str) and 0 < len(note.strip()) <= 350))
    if not notes:
        raise ValueError('模型没有返回可用的知识补充')
    return notes
