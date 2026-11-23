import json
import unittest
from board_app.summary.summary_format import parse_summary


class SummaryFormatTests(unittest.TestCase):
    def test_valid_content_unchanged(self):
        expected = dict(title='标题', points=['含数字16GB', '引号"和括号]', '第三条'])
        actual = parse_summary(json.dumps(expected, ensure_ascii=False))
        self.assertFalse(actual.pop('output_format_repaired'))
        self.assertEqual(actual, expected)

    def test_only_missing_final_array_bracket_recovered(self):
        expected = dict(title='标题', points=['第一条', '第二条', '第三条'])
        for ending in ('}', '}}'):
            malformed = json.dumps(expected, ensure_ascii=False)[:-2]+ending
            actual = parse_summary(malformed)
            self.assertTrue(actual.pop('output_format_repaired'))
            self.assertEqual(actual, expected)

    def test_content_and_count_errors_rejected(self):
        for raw in ('{"title":"标题","points":["只有一条"}',
                    '{"title":"标题","points":["未闭合字符串}',
                    '{"title":"标题","points":["a" "b","c"]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_summary(raw)

    def test_collection_recovers_missing_opening_quotes_at_citations(self):
        expected = dict(title='课堂', points=['[P1] 第一条', '[P1, P2] 第二条', '[P2] 第三条'])
        raw = '{"title":"课堂","points":["[P1] 第一条",[P1, P2] 第二条",[P2] 第三条"]}'
        with self.assertRaises(ValueError):
            parse_summary(raw)
        result = parse_summary(raw, collection=True)
        self.assertTrue(result.pop('output_format_repaired'))
        self.assertEqual(result, expected)

    def test_collection_preserves_valid_citations_and_rejects_broken_text(self):
        expected = dict(title='课堂', points=['[P1] 第一条', '[P2] 例子：,[P1] 引用', '[P2] 第三条'])
        valid = json.dumps(expected, ensure_ascii=False)
        result = parse_summary(valid, collection=True)
        self.assertFalse(result.pop('output_format_repaired'))
        self.assertEqual(result, expected)
        with self.assertRaises(ValueError):
            parse_summary(valid.replace('第三条"', '第三条'), collection=True)

    def test_album_can_keep_two_valid_points_without_padding(self):
        raw = '{"title":"相册","points":["[P1] 第一页要点",[P2] 第二页要点"]}'
        with self.assertRaises(ValueError):
            parse_summary(raw, collection=True)
        result = parse_summary(raw, collection=True, min_points=2)
        self.assertEqual(result['points'], ['[P1] 第一页要点', '[P2] 第二页要点'])
        self.assertTrue(result['output_format_repaired'])


if __name__ == '__main__':
    unittest.main()
