"""真实失败框坐标与倾斜分片回归，不运行NPU。"""
import unittest
import numpy as np
from reading_order import sort_text_boxes


class ReadingOrderTests(unittest.TestCase):
    def test_actual_adjacent_long_slanted_lines(self):
        # 来自已有摄像头PPT校正后的两行；旧算法将它们并入同一行再按x排序。
        first = np.array([[115, 573], [1314, 505], [1320, 542], [121, 610]])
        second = np.array([[109, 619], [1316, 545], [1323, 585], [116, 659]])
        ordered = sort_text_boxes([second, first])
        np.testing.assert_array_equal(ordered[0], first)
        np.testing.assert_array_equal(ordered[1], second)

    def test_tilted_same_row_fragments_then_next_row(self):
        def box(x, y, width=400):
            return np.array([[x, y], [x+width, y-width*.08],
                             [x+width, y+20-width*.08], [x, y+20]])
        left, right, next_line = box(0, 100), box(600, 52), box(0, 145, 1000)
        ordered = sort_text_boxes([next_line, right, left])
        for actual, expected in zip(ordered, [left, right, next_line]):
            np.testing.assert_array_equal(actual, expected)
        self.assertEqual(sort_text_boxes([]), [])

    def test_separate_two_columns_before_spanning_footer(self):
        def box(x0, x1, y):
            return np.array([[x0, y], [x1, y], [x1, y+26], [x0, y+26]])
        left_title, left_body = box(144, 476, 520), box(140, 688, 566)
        right_title, right_body = box(1084, 1280, 554), box(1084, 1500, 597)
        footer = box(150, 1500, 900)
        actual = sort_text_boxes([right_body, left_body, footer, right_title, left_title], mode='columns')
        for found, expected in zip(actual, [left_title, left_body, right_title, right_body, footer]):
            np.testing.assert_array_equal(found, expected)


if __name__ == '__main__':
    unittest.main()
