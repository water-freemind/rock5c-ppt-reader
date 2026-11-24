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


def paragraph_batches(lines, limit=900):
    """Number lines within each bounded batch, retaining the original offset."""
    batch = []
    size = 0
    start = 0
    for index, line in enumerate(lines):
        numbered = '{}: {}'.format(len(batch)+1, line)
        if not line.strip() or size+len(numbered)+1 > limit:
            if len(batch) > 1:
                yield start, '\n'.join(batch)
            batch, size = [], 0
        if not line.strip() or len(line)+4 > limit:
            continue
        if not batch:
            start = index
        numbered = '{}: {}'.format(len(batch)+1, line)
        batch.append(numbered)
        size += len(numbered)+1
    if len(batch) > 1:
        yield start, '\n'.join(batch)


def parse_paragraph_merges(raw, source):
    boundaries = read_list(raw, 'merge_after')
    limit = len(source.split('\n'))-1
    return {index for index in boundaries if type(index) is int and 1 <= index <= limit}


_LIST_START = re.compile(r'^\s*(?:[•●▪◆■]|[-–]\s|[（(]?\d{1,3}[）)、.．](?!\d)|[一二三四五六七八九十]+[、.．])')


def merge_paragraph_lines(lines, boundaries):
    parts = []
    for index, line in enumerate(lines):
        if index:
            previous = lines[index-1]
            if index in boundaries and previous.strip() and line.strip() and not _LIST_START.match(line):
                if re.search(r'[A-Za-z0-9]$', previous) and re.match(r'[A-Za-z0-9]', line):
                    parts.append(' ')
            else:
                parts.append('\n')
        parts.append(line)
    return ''.join(parts)
