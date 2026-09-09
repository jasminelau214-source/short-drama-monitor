import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research_pipeline as rp


def fake_search(query, **kwargs):
    return {
        'results': [{
            'title': 'Example Show - Official',
            'url': 'https://www.netshort.com/example-show',
            'score': 0.91,
            'raw_content': 'Example Show follows a hidden hero who returns to protect his family and defeat a corrupt rival.'
        }]
    }


def fake_analyze_sources(*, task, sources):
    return {
        'canonicalTitle': task['title'],
        'newnessResolution': 'new',
        'synopsis': '有来源支持的剧情简介',
        'genre': '都市逆袭',
        'lane': '隐藏强者 / 复仇',
        'audience': '男频',
        'storyCore': '低位主角回归反杀。',
        'storySkin': '现代都市。',
        'conflict': '主角与腐败对手冲突。',
        'payoff': '身份揭晓 / 反杀',
        'openingSummary': '', 'openingType': '', 'payEpisode': '', 'paywallSummary': '', 'paywallType': '',
        'localizationLevel': 'B+', 'localizationJudgment': '基础英语市场语境可理解。', 'mismatch': '',
        'confidence': 'medium', 'missingFields': [], 'auditNotes': [],
        'sourceUrls': [sources[0]['url']], 'needsGPT': False,
    }

rp._search_once = fake_search
rp.analyze_sources = fake_analyze_sources
out = rp.research_task({
    'id': 'x', 'platform': 'NetShort', 'rank': 1, 'title': 'Example Show',
    'context_json': {}, 'missing_fields': [],
})
assert out['status'] == 'COMPLETE', out
assert out['confidence'] == 'medium', out
assert out['sources'][0]['official'] is True, out
print('research pipeline smoke test passed')
