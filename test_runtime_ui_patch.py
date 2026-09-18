import unittest
from pathlib import Path

import runtime_ui_patch


class RuntimeUiPatchTests(unittest.TestCase):
    def test_web_copy_matches_evidence_layer_semantics(self):
        runtime_ui_patch.main()
        html = (Path(__file__).resolve().parent / 'index.html').read_text(encoding='utf-8')
        self.assertIn('Web采集证据', html)
        self.assertIn('Official Web采集证据', html)
        self.assertIn('证据采集通过', html)
        self.assertIn('Official Web 不自动进入内容深研', html)
        self.assertIn('处理时间不得替代采集日期', html)
        self.assertNotIn('采集通过的数据进入内容分析队列', html)
        self.assertNotIn('进入分析队列', html)
        self.assertNotIn('本批次应显示 2026-09-16', html)


if __name__ == '__main__':
    unittest.main()
