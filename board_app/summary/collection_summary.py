"""分块综合摘要：严格页码引用，保留调用计时，不截断来源或伪造关联。"""
import re
import time
from shared.records import OCR_BACKEND
from board_app.summary.summary_format import COLLECTION_BATCH_PAGES, PAGE_TAG, collection_pages


def point_references(points, allowed):
    mappings = []
    for point in points:
        tags = re.findall(PAGE_TAG, point)
        if not tags or not re.sub(PAGE_TAG, '', point).strip():
            raise ValueError('综合摘要缺少有效[P页码]引用，请重试')
        refs = list(dict.fromkeys(re.findall(r'P\d+', ','.join(tags))))
        if not set(refs).issubset(allowed):
            raise ValueError('综合摘要引用了未提供的页面，未采用结果')
        mappings.append(refs)
    return mappings


def batches(blocks, limit=900):
    groups, current = [], ''
    for block in blocks:
        if len(block) > limit:
            raise ValueError('单页摘要过长，请先在历史记录中校订至900字符以内')
        candidate = (current+'\n\n'+block).strip()
        if len(candidate) > limit or len(collection_pages(candidate)) > COLLECTION_BATCH_PAGES:
            groups.append(current)
            current = block
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


class CollectionSummarizer:
    def __init__(self, backend, progress=None):
        self.backend = backend
        self.progress = progress or (lambda event: None)

    def cancel(self):
        self.backend.cancel()

    def summarize(self, sources, *, raw_ocr=False):
        started = time.monotonic()
        allowed = {s['page'] for s in sources}
        if not allowed:
            raise ValueError('请先选择要整理的页面')
        blocks = []
        for source in sources:
            summary = source.get('summary', {})
            if raw_ocr or summary.get('backend') == OCR_BACKEND:
                text = re.sub(PAGE_TAG, r'［\1］', source['text']).strip()
                if not text:
                    raise ValueError(source['page']+' 未识别出文字')
                blocks.extend('[{}]\n{}'.format(source['page'], text[start:start+840])
                              for start in range(0, len(text), 840))
            else:
                text = '\n'.join([summary['title'], *summary['points']])
                blocks.append('[{}] {}'.format(source['page'], re.sub(PAGE_TAG, r'［\1］', text)))
        calls = []
        first_output = None
        for level in range(4):
            groups = batches(blocks)
            results = []
            for index, text in enumerate(groups, 1):
                self.progress(dict(stage='综合摘要 第{}层 {}/{}'.format(level+1, index, len(groups))))
                call_offset = time.monotonic()-started
                value = self.backend.summarize(text, task='collection')
                if first_output is None and value.get('first_token_seconds') is not None:
                    first_output = call_offset+value['first_token_seconds']
                chunk_pages = collection_pages(text)
                references = point_references(value['points'], chunk_pages)
                if set(ref for refs in references for ref in refs) != chunk_pages:
                    raise ValueError('综合摘要遗漏了部分来源页，请重试或减少课堂页数')
                calls.append(dict(elapsed_seconds=value.get('elapsed_seconds'),
                                  first_token_seconds=value.get('first_token_seconds'),
                                  generated_tokens=value.get('generated_tokens'),
                                  tokens_per_second=value.get('tokens_per_second'),
                                  evidence_directory=value.get('evidence_directory')))
                results.append(dict(value, point_sources=references))
            next_blocks = ['\n'.join(result['points']) for result in results]
            if (len(groups) == 1 or len(allowed) > 5 or level == 3
                    or any(len(block) > 900 for block in next_blocks)
                    or sum(map(len, next_blocks)) >= sum(map(len, blocks))):
                break
            blocks = next_blocks
        result = results[-1]
        if len(results) > 1:
            result = dict(result, title='多页综合摘要（分组）', source_text='\n\n'.join(groups))
            for key in ('points', 'point_sources'):
                result[key] = [item for part in results for item in part[key]]
            for key in ('quality_warnings', 'missing_numeric_facts', 'unexpected_numeric_facts'):
                result[key] = list(dict.fromkeys(item for part in results for item in part.get(key, [])))
            result['output_format_repaired'] = any(part.get('output_format_repaired') for part in results)
        result.update(collection_calls=calls, elapsed_seconds=time.monotonic()-started,
                      first_token_seconds=first_output, input_kind='album-ocr' if raw_ocr else
                      'classroom-pages' if any(s.get('summary', {}).get('backend') == OCR_BACKEND for s in sources)
                      else 'saved-page-summaries', source_pages=[source['page'] for source in sources])
        return result
