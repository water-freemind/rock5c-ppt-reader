"""按文本自身厚度及倾斜方向排阅读顺序，避免长斜框的轴对齐高度合并相邻行。"""
import numpy as np


def sort_text_boxes(boxes, mode='rows'):
    if mode not in ('rows', 'columns'):
        raise ValueError('未知 OCR 阅读顺序')
    if len(boxes) == 0:
        return []
    values = [np.asarray(box, dtype=np.float32) for box in boxes]
    slopes = []
    for box in values:
        top = box[1]-box[0]
        bottom = box[2]-box[3]
        if abs(top[0]) > 2*max(1, abs(top[1])) and abs(bottom[0]) > 2*max(1, abs(bottom[1])):
            slopes.append(float((top[1]/top[0]+bottom[1]/bottom[0])/2))
    slope = float(np.median(slopes)) if slopes else 0
    candidates = []
    for box in values:
        center = box.mean(axis=0)
        height = max(1, float((np.linalg.norm(box[3]-box[0])+np.linalg.norm(box[2]-box[1]))/2))
        candidates.append(dict(box=box, center=float(center[1]-slope*center[0]), height=height))
    rows = []
    for value in sorted(candidates, key=lambda b: b['center']):
        matches = [r for r in rows if abs(value['center']-r['center']) < .45*min(value['height'], r['height'])]
        if matches:
            row = min(matches, key=lambda r: abs(value['center']-r['center']))
            row['items'].append(value)
            row['center'] = float(np.mean([v['center'] for v in row['items']]))
            row['height'] = min(row['height'], value['height'])
        else:
            rows.append(dict(center=value['center'], height=value['height'], items=[value]))
    ordered = [v['box'] for row in sorted(rows, key=lambda r: r['center'])
               for v in sorted(row['items'], key=lambda v: float(v['box'][:, 0].min()))]
    return _by_column(ordered) if mode == 'columns' else ordered


def _by_column(ordered):
    extents = [(float(box[:, 0].min()), float(box[:, 0].max())) for box in ordered]
    low, high = min(x0 for x0, _ in extents), max(x1 for _, x1 in extents)
    width = high-low
    center = (low+high)/2
    # 在页面中部找穿过最少文本框的分界，倾斜照片不要求左右两栏高度一致。
    cuts = np.linspace(low+.42*width, low+.58*width, 25)
    cut = min(cuts, key=lambda x: (sum(a<x<b for a,b in extents), abs(x-center)))
    if sum(b<=cut for _, b in extents)<2 or sum(a>=cut for a, _ in extents)<2:
        return ordered
    result, left, right = [], [], []
    for box, (x0, x1) in zip(ordered, extents):
        # 横跨两栏的宽标题或页脚把页面分段，不挪到某一栏。
        if x0<cut<x1 and x1-x0>.4*width:
            result.extend(left+right+[box])
            left, right = [], []
        else:
            (left if (x0+x1)/2<cut else right).append(box)
    return result+left+right
