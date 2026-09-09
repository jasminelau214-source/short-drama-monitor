import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_observations import merge_analysis_records, build_live_summary


def normalize_title(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def split_lane(value):
    return [x.strip() for x in str(value or '').replace('/', '\n').splitlines() if x.strip()]


def clean(value):
    return str(value or '').strip()


tmp = tempfile.TemporaryDirectory()
db = Path(tmp.name) / 'x.sqlite3'


def connect():
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute('create table if not exists analysis_runs(id text, collection_date text, platform text, status text, result_json text, updated_at text)')
    return c

rows = []
for rank in range(1, 11):
    rows.append({
        'rank': rank,
        'title': 'Known' if rank == 1 else f'New {rank}',
        'heat': f'{rank}K',
        'tags': ['Test'],
        'metrics': {'collect': '', 'like': '', 'followers': ''},
        'newness': 'old' if rank == 1 else 'new',
        'matchedExistingTitle': 'Known' if rank == 1 else '',
        'confidence': 'high',
        'pendingChecks': [] if rank == 1 else ['待深度研究'],
    })
result = {'batchComplete': True, 'rows': rows}
with connect() as c:
    c.execute('insert into analysis_runs values(?,?,?,?,?,?)', ('run1','2026-09-09','ReelShort','已识别-待深研',json.dumps(result, ensure_ascii=False),'2026-09-09T12:00:00Z'))
    c.commit()

base = [{
    'id':'reelshort-known','title':'Known','app':'ReelShort','date':'2026-09-07','rank':5,'heat':'5K',
    'tags':'Old','synopsis':'done','genre':'x','lane':'y','audience':'女频','storyCore':'c','storySkin':'s',
    'conflict':'c','payoff':'p','openingSummary':'','openingType':'','payEpisode':'','paywallSummary':'',
    'paywallType':'','localizationLevel':'A','localizationJudgment':'ok','mismatch':'','history':[
        {'date':'2026-09-07','app':'ReelShort','rank':5,'heat':'5K','tags':'Old','metrics':{}}
    ],'platformMetrics':{},'recordedDates':['2026-09-07'],'daysOnChart':1,'firstDate':'2026-09-07','lastDate':'2026-09-07','highestRank':5
}]
merged = merge_analysis_records(base, connect, normalize_title, split_lane)
summary = build_live_summary(merged, ['ReelShort'], clean, split_lane)
assert summary['collectionDate'] == '2026-09-09', summary
assert summary['totalRows'] == 10, summary
assert summary['platforms'][0]['value'] == 10, summary
assert any(r['title'] == 'New 10' and r.get('researchStatus') == '待深研' for r in merged)
assert next(r for r in merged if r['title'] == 'Known')['synopsis'] == 'done'
print('live observation smoke test passed')
