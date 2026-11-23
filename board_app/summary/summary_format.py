"""校验模型摘要，只恢复已知缺失的 JSON 结构字符。"""
import json
import re


def parse_summary(raw, *, collection=False, min_points=3):
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    repaired = False
    if collection:
        # Qwen3 偶尔在课堂要点开头的 [P1] 前漏写引号。只在解析器
        # 指出这个位置的语法错误时补引号，避免修改合法字符串里的引用。
        for _ in range(5):
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
        # 实测Qwen3偶尔漏掉points数组的最终]；只试插入这个结构字符。
        # 完整原始输出由调用者保存。
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
            or not min_points <= len(result['points']) <= 5
            or not all(isinstance(p, str) and p.strip() for p in result['points'])):
        raise ValueError('模型未返回标题和{}～5条有效要点，请重试'.format(min_points))
    result['output_format_repaired'] = repaired
    return result
