from pathlib import Path

path = Path('app.py')
text = path.read_text(encoding='utf-8')

import_old = "from analysis_pipeline import analyze_batch, configured as analysis_configured, model_name as analysis_model_name\nimport persistence\n"
import_new = "from analysis_pipeline import analyze_batch, configured as analysis_configured, model_name as analysis_model_name\nfrom live_observations import merge_analysis_records, build_live_summary\nimport persistence\n"
if import_new not in text:
    if import_old not in text:
        raise SystemExit('IMPORT_ANCHOR_NOT_FOUND')
    text = text.replace(import_old, import_new, 1)

old = '''def public_data():
    with connect() as c:
        rows=c.execute('SELECT drama_id,fields_json FROM drama_overrides').fetchall()
    overrides={}
    for row in rows:
        try: overrides[row['drama_id']]=json.loads(row['fields_json'])
        except json.JSONDecodeError: pass
    records=[]
    for base in BASE_DATA['records']:
        r=dict(base); r.update(overrides.get(r.get('id'),{})); r['laneTerms']=split_lane(r.get('lane')); records.append(r)
    return {'records':records,'summary':build_summary(records)}
'''
new = '''def public_data():
    with connect() as c:
        rows=c.execute('SELECT drama_id,fields_json FROM drama_overrides').fetchall()
    overrides={}
    for row in rows:
        try: overrides[row['drama_id']]=json.loads(row['fields_json'])
        except json.JSONDecodeError: pass
    records=[]
    for base in BASE_DATA['records']:
        r=dict(base); r.update(overrides.get(r.get('id'),{})); r['laneTerms']=split_lane(r.get('lane')); records.append(r)
    # Fact publication is independent from deep research: every latest complete
    # screenshot analysis becomes a live ranking observation immediately.
    records=merge_analysis_records(records, connect, normalize_title, split_lane)
    return {'records':records,'summary':build_live_summary(records, PLATFORM_ORDER, clean, split_lane)}
'''
if new not in text:
    if old not in text:
        raise SystemExit('PUBLIC_DATA_ANCHOR_NOT_FOUND')
    text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
print('patched app.py')
