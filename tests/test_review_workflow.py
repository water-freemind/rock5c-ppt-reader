import json
from pathlib import Path
import tempfile
import unittest

from shared.records import RecordStore
from board_app.summary.review_format import (
    merge_paragraph_lines, paragraph_batches, parse_knowledge, parse_paragraph_merges, parse_review,
)
from board_app.ui.review_workflow import apply_edits


def suggestion(original, replacement):
    return dict(original=original, replacement=replacement, reason='与同页术语一致')


class ReviewWorkflowTests(unittest.TestCase):
    def test_paragraph_layout_preserves_words_list_markers_and_blank_lines(self):
        lines = ['使用 UART', '发送数据。', '数值为 12', 'MHz。', '', '1. 参数', '2. 接线']
        groups = list(paragraph_batches(lines))
        self.assertEqual([offset for offset, _ in groups], [0, 5])
        self.assertEqual(parse_paragraph_merges('{"merge_after":[1,3]}', groups[0][1]), {1, 3})
        self.assertEqual(parse_paragraph_merges('{"merge_after":[true,4,2]}', groups[0][1]), {2})
        self.assertEqual(merge_paragraph_lines(lines, {1, 3, 4, 5, 6}),
                         '使用 UART发送数据。\n数值为 12 MHz。\n\n1. 参数\n2. 接线')
        long_lines = ['甲'*895, '乙', '丙']
        self.assertEqual(list(paragraph_batches(long_lines)), [(1, '1: 乙\n2: 丙')])

    def test_only_unique_non_numeric_source_is_eligible(self):
        raw = '硬件设设计，12V；串行总线，串行总线'
        proposals = [suggestion('设设计', '设计'), suggestion('12V', '21V'),
                     suggestion('串行总线', '并行总线'), suggestion('并口', '串口')]
        valid = parse_review(json.dumps({'edits': proposals}), raw)
        self.assertEqual(valid, proposals[:1])
        self.assertEqual(parse_review('```json\n'+json.dumps(proposals)+'\n```', raw), valid)
        self.assertEqual(apply_edits(raw, valid), '硬件设计，12V；串行总线，串行总线')
        self.assertIn('设设计', raw)

    def test_refuses_overlapping_edits_and_empty_notes(self):
        with self.assertRaises(ValueError):
            apply_edits('硬件设设计', [suggestion('件设设', '件设'), suggestion('设设计', '设计')])
        with self.assertRaises(ValueError):
            parse_knowledge('{"notes":["", null]}')

    def test_markdown_keeps_sources_and_invalidates_stale_notes_on_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            store = RecordStore(path)
            text = '串行总线'
            summary = dict(backend='local-rkllm-npu', model='Qwen3-4B', source_text=text,
                           ocr_raw_text='串行行总线', title='串行扩展', points=['要点一', '要点二', '要点三'],
                           knowledge=dict(model='Qwen3-4B', notes=['用同一线路依次传输各位。']))
            record = store.save(b'image', text, summary)
            destination = path/'note.md'
            store.export_markdown(store.load(record['record_id']), destination)
            made = destination.read_text(encoding='utf-8')
            self.assertIn('## 机器 OCR 原文\n\n```text\n串行行总线', made)
            self.assertIn('#### 对应校订原文\n\n```text\n串行总线', made)
            self.assertIn('## 知识补充\n\nAI 生成', made)
            changed = store.revise(record['record_id'], '串行通信总线', summary['title'], summary['points'], True)
            self.assertNotIn('knowledge', changed['summary'])
            self.assertEqual(changed['summary']['ocr_raw_text'], '串行行总线')


if __name__ == '__main__':
    unittest.main()
