"""校验模型摘要，只恢复已知缺失的 JSON 结构字符。"""
import json
import re

PAGE_TAG = r'\[(P\d+(?:\s*,\s*P\d+)*)\]'
COLLECTION_BATCH_PAGES = 10


def collection_pages(text):
    return set(re.findall(r'P\d+', ','.join(re.findall(PAGE_TAG, text))))


def collection_point_target(text):
    return max(5, min(COLLECTION_BATCH_PAGES, len(collection_pages(text))))


def read_unquoted_collection(clean):
    # 有些输出把数组写成逐行的 [P1] 正文。只接受完整对象和逐行的明确引用。
    match = re.fullmatch(r'\{\s*"title"\s*:\s*("(?:[^"\\]|\\.)*")\s*,\s*'
                         r'"points"\s*:\s*\[ *\r?\n(.+)\n\s*\]\s*\}', clean, re.S)
    if match is None:
        return None
    points = [line.strip().rstrip(',').rstrip() for line in match[2].splitlines()]
    if not all(re.fullmatch(PAGE_TAG+r'\s+[^"\r\n]+', point) for point in points):
        return None
    return dict(title=json.loads(match[1]), points=points)


def parse_summary(raw, *, collection=False, min_points=3):
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    original = clean
    repaired = False
    if collection:
        # Qwen3 偶尔在课堂要点开头的 [P1] 前漏写引号。只在解析器
        # 指出这个位置的语法错误时补引号，避免修改合法字符串里的引用。
        for _ in range(COLLECTION_BATCH_PAGES):
            try:
                json.loads(clean)
                break
            except json.JSONDecodeError as exc:
                start = exc.pos-1
                if (exc.msg != 'Expecting value' or start < 0
                        or not clean[:start].rstrip().endswith(('[', ','))
                        or not re.match(r'\[P\d+(?:\s*,\s*P\d+)*\]\s+', clean[start:])):
                    break
                clean = clean[:start]+'"'+clean[start:]
                repaired = True
    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        result = read_unquoted_collection(original) if collection else None
        if result is not None:
            repaired = True
        # 实测Qwen3偶尔漏掉points数组的最终]；只试插入这个结构字符。
        # 完整原始输出由调用者保存。
        else:
            if not re.search(r'"\s*}\s*}?\s*$', clean):
                raise ValueError('模型摘要JSON不完整，请重试')
            candidate = re.sub(r'(?<=")\s*}\s*}?\s*$', ']}', clean)
            try:
                result = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ValueError('模型摘要JSON格式错误，请重试') from exc
            repaired = True
    if (not isinstance(result, dict) or not isinstance(result.get('title'), str)
            or not result['title'].strip() or not isinstance(result.get('points'), list)
            or not all(isinstance(p, str) and p.strip() for p in result['points'])):
        raise ValueError('模型摘要缺少标题或包含无效要点')
    count = len(result['points'])
    if count < min_points:
        raise ValueError('至少应生成{}条要点，模型返回了{}条'.format(min_points, count))
    if not collection and count > 5:
        raise ValueError('单页摘要最多5条要点，模型返回了{}条'.format(count))
    result['output_format_repaired'] = repaired
    return result
