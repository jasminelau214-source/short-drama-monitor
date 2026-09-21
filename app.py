from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from analysis_pipeline import analyze_batch, configured as analysis_configured, model_name as analysis_model_name
from live_observations import merge_analysis_records, build_live_summary
from research_worker import schedule as schedule_research_worker, configured as research_configured, running as research_running
from collector_import import CollectorImportError, validate_and_normalize
from drama_identity import normalize_title
from research_writeback import select_same_platform_record
from research_validation import validate_research_payload
from ranking_lifecycle import known_app_ranked_titles as lifecycle_known_app_ranked_titles
import persistence

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'short_drama.sqlite3'
PLATFORM_ORDER = ['NetShort','DramaWave','MoboReels','ReelShort']
EDITABLE_FIELDS = {'synopsis','genre','lane','audience','storyCore','storySkin','conflict','payoff','openingSummary','openingType','payEpisode','paywallSummary','paywallType','localizationLevel','localizationJudgment','mismatch'}
ANALYSIS_TIMERS: dict[str, threading.Timer] = {}
ANALYSIS_TIMER_LOCK = threading.Lock()


def clean(value, limit=6000):
    return str(value or '').strip()[:limit]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().casefold() in {'1', 'true', 'yes', 'on'}


def readiness_status() -> dict:
    """Return deployment readiness without performing any write-side action."""
    required = {
        'persistence': env_flag('JSM_READINESS_REQUIRE_PERSISTENCE', False),
        'analysis': env_flag('JSM_READINESS_REQUIRE_ANALYSIS', False),
        'research': env_flag('JSM_READINESS_REQUIRE_RESEARCH', False),
    }
    checks = {}

    try:
        with connect() as c:
            c.execute('SELECT 1').fetchone()
        checks['localDb'] = {'ok': True, 'required': True}
    except Exception as exc:
        checks['localDb'] = {
            'ok': False,
            'required': True,
            'errorType': type(exc).__name__,
        }

    persistence_configured = persistence.configured()
    if required['persistence']:
        if not persistence_configured:
            checks['persistence'] = {
                'ok': False,
                'required': True,
                'configured': False,
                'errorType': 'NOT_CONFIGURED',
            }
        else:
            try:
                persistence.healthcheck(timeout=5.0)
                checks['persistence'] = {
                    'ok': True,
                    'required': True,
                    'configured': True,
                }
            except Exception as exc:
                checks['persistence'] = {
                    'ok': False,
                    'required': True,
                    'configured': True,
                    'errorType': type(exc).__name__,
                }
    else:
        checks['persistence'] = {
            'ok': True,
            'required': False,
            'configured': persistence_configured,
            'probed': False,
        }

    analysis_ok = analysis_configured()
    checks['analysis'] = {
        'ok': bool(analysis_ok or not required['analysis']),
        'required': required['analysis'],
        'configured': bool(analysis_ok),
    }

    research_ok = research_configured()
    checks['research'] = {
        'ok': bool(research_ok or not required['research']),
        'required': required['research'],
        'configured': bool(research_ok),
    }

    ready = all(bool(item.get('ok')) for item in checks.values())
    return {
        'ready': ready,
        'checks': checks,
        'required': required,
        'version': '1.4-collector',
        'gitCommit': clean(os.environ.get('RENDER_GIT_COMMIT', ''), 80),
        'serviceName': clean(os.environ.get('RENDER_SERVICE_NAME', ''), 120),
    }


def split_lane(value):
    s = str(value or '')
    for sep in ['/', '、', ',', '，', ';', '；', '|']:
        s = s.replace(sep, '\n')
    return [x.strip() for x in s.splitlines() if x.strip()]


def load_base_data():
    data = json.loads((ROOT / 'data.json').read_text(encoding='utf-8'))
    records = data.setdefault('records', [])
    by_id = {r.get('id'): r for r in records if r.get('id')}
    by_title = {r.get('title'): r for r in records if r.get('title')}
    update_dir = ROOT / 'updates'
    if update_dir.exists():
        for path in sorted(update_dir.glob('*.json')):
            patch = json.loads(path.read_text(encoding='utf-8'))
            for drama_id, fields in (patch.get('record_patches') or {}).items():
                if drama_id in by_id and isinstance(fields, dict):
                    by_id[drama_id].update(fields)
            for new_record in patch.get('new_records') or []:
                if not isinstance(new_record, dict) or not new_record.get('id'):
                    continue
                if new_record['id'] not in by_id:
                    r = dict(new_record)
                    records.append(r)
                    by_id[r['id']] = r
                    by_title[r.get('title')] = r
            for obs in patch.get('observations') or []:
                r = by_id.get(obs.get('id')) or by_title.get(obs.get('title'))
                if not r:
                    continue
                date = clean(obs.get('date'), 20)
                event = {
                    'date': date,
                    'app': clean(obs.get('app') or r.get('app'), 40),
                    'rank': int(obs.get('rank') or 0),
                    'heat': clean(obs.get('heat'), 100),
                    'tags': clean(obs.get('tags'), 1000),
                    'metrics': obs.get('metrics') if isinstance(obs.get('metrics'), dict) else {},
                    'source': clean(obs.get('source'), 500),
                }
                history = [h for h in (r.get('history') or []) if h.get('date') != date]
                history.append(event)
                history.sort(key=lambda h: (h.get('date',''), h.get('app',''), h.get('rank',999)))
                dates = sorted({h.get('date') for h in history if h.get('date')})
                ranks = [int(h.get('rank')) for h in history if h.get('rank') not in (None,'')]
                r.update({
                    'app': event['app'], 'date': date, 'rank': event['rank'], 'heat': event['heat'],
                    'tags': event['tags'], 'platformMetrics': event['metrics'], 'history': history,
                    'recordedDates': dates, 'daysOnChart': len(dates),
                    'firstDate': dates[0] if dates else date, 'lastDate': dates[-1] if dates else date,
                    'highestRank': min(ranks) if ranks else event['rank'],
                })
    return data


BASE_DATA = load_base_data()
INDEX_HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
REVIEW_HTML = (ROOT / 'review.html').read_text(encoding='utf-8')
COLLECT_HTML = (ROOT / 'collect.html').read_text(encoding='utf-8')
RESEARCH_META = json.loads((ROOT / 'research_meta.json').read_text(encoding='utf-8')) if (ROOT/'research_meta.json').exists() else {}


class ClosingSQLiteConnection(sqlite3.Connection):
    """Preserve sqlite transaction context semantics and close on context exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingSQLiteConnection)
    c.row_factory = sqlite3.Row
    c.executescript('''
    CREATE TABLE IF NOT EXISTS drama_overrides(
      drama_id TEXT PRIMARY KEY, fields_json TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS collection_uploads(
      id TEXT PRIMARY KEY, collection_date TEXT NOT NULL, platform TEXT NOT NULL,
      filename TEXT NOT NULL, mime_type TEXT NOT NULL, storage_path TEXT NOT NULL,
      sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS analysis_runs(
      id TEXT PRIMARY KEY, collection_date TEXT NOT NULL, platform TEXT NOT NULL,
      upload_ids_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL,
      error TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    ''')
    c.commit()
    return c


def sync_persistent_cache():
    if not persistence.configured():
        print('[persistence] not configured; local cache only')
        return
    try:
        uploads = persistence.list_uploads(500)
        runs = persistence.list_analysis_runs(200)
        overrides = persistence.list_overrides()
        with connect() as c:
            for row in uploads:
                c.execute(
                    '''INSERT OR REPLACE INTO collection_uploads
                       (id,collection_date,platform,filename,mime_type,storage_path,sha256,status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',
                    (
                        row.get('id',''), row.get('collection_date',''), row.get('platform',''), row.get('filename',''),
                        row.get('mime_type',''), 'supabase:' + str(row.get('storage_path','')), row.get('sha256',''),
                        row.get('status','待分析'), row.get('created_at',''),
                    ),
                )
            for row in runs:
                c.execute(
                    '''INSERT OR REPLACE INTO analysis_runs
                       (id,collection_date,platform,upload_ids_json,status,result_json,error,model,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (
                        row.get('id',''), row.get('collection_date',''), row.get('platform',''),
                        json.dumps(row.get('upload_ids') or []), row.get('status',''),
                        json.dumps(row.get('result_json') or {}, ensure_ascii=False), row.get('error',''),
                        row.get('model',''), row.get('created_at',''), row.get('updated_at',''),
                    ),
                )
            for row in overrides:
                c.execute(
                    'INSERT OR REPLACE INTO drama_overrides(drama_id,fields_json,updated_at) VALUES(?,?,?)',
                    (row.get('drama_id',''), json.dumps(row.get('fields_json') or {}, ensure_ascii=False), row.get('updated_at','')),
                )
            c.commit()
        print(f'[persistence] synced uploads={len(uploads)} runs={len(runs)} overrides={len(overrides)}')
    except Exception as exc:
        print(f'[persistence] sync failed: {exc}')


def materialize_persistent_batch(collection_date, platform):
    rows = persistence.get_batch(collection_date, platform)
    out=[]
    for row in rows:
        signed=row.get('signed_url') or ''
        if not signed:
            continue
        ext={'image/png':'.png','image/webp':'.webp','image/jpeg':'.jpg'}.get(row.get('mime_type'),'.jpg')
        target=UPLOAD_DIR/f"remote_{row.get('id')}{ext}"
        with urllib.request.urlopen(signed, timeout=120) as resp:
            target.write_bytes(resp.read())
        d=dict(row); d['storage_path']=str(target); out.append(d)
    return out


def count_by(records, field):
    counts = Counter(clean(r.get(field)) for r in records)
    counts.pop('', None); counts.pop('未采集', None)
    return [{'name':k,'value':v} for k,v in sorted(counts.items(), key=lambda x:(-x[1],x[0]))]


def build_summary(records):
    dates = sorted({h.get('date') for r in records for h in (r.get('history') or []) if h.get('date')})
    latest = dates[-1] if dates else ''
    current=[]
    for r in records:
        h = next((x for x in (r.get('history') or []) if x.get('date') == latest), None)
        if not h: continue
        x=dict(r); x.update({'date':latest,'app':h.get('app') or r.get('app',''),'rank':h.get('rank',r.get('rank',0)),'heat':h.get('heat') or '','tags':h.get('tags',r.get('tags','')),'platformMetrics':h.get('metrics') or {}}); current.append(x)
    platforms=[{'name':p,'value':sum(1 for r in current if r.get('app')==p)} for p in PLATFORM_ORDER]
    lane_counts=Counter(t for r in current for t in split_lane(r.get('lane')))
    leaders=[]
    for p in PLATFORM_ORDER:
        xs=[r for r in current if r.get('app')==p]
        if xs: leaders.append(min(xs,key=lambda r:r.get('rank',999)))
    return {
      'collectionDate':latest,'dates':dates,'dateCount':len(dates),'totalRows':len(current),
      'uniqueTitles':len({r.get('title') for r in current}),'totalUniqueTitles':len(records),
      'newTitles':sum(1 for r in current if r.get('firstDate')==latest),
      'continuingTitles':sum(1 for r in current if r.get('firstDate')!=latest),
      'platforms':platforms,'genres':count_by(current,'genre'),'audiences':count_by(current,'audience'),
      'lanes':[{'name':k,'value':v} for k,v in sorted(lane_counts.items(),key=lambda x:(-x[1],x[0]))[:12]],
      'topByPlatform':leaders,
      'dateStats':[{'date':d,'rows':sum(1 for r in records if any(h.get('date')==d for h in (r.get('history') or []))),'uniqueTitles':len({r.get('title') for r in records if any(h.get('date')==d for h in (r.get('history') or []))})} for d in dates]
    }


def _research_source_urls(sources):
    urls = set()
    for item in sources if isinstance(sources, list) else []:
        if isinstance(item, dict):
            value = clean(item.get('url') or item.get('sourceUrl'), 1200)
        else:
            value = clean(item, 1200)
        if value:
            urls.add(value)
    return urls


def _research_task_view(task):
    status = clean(task.get('status'), 40).upper() or 'UNKNOWN'
    research = task.get('research_json') if isinstance(task.get('research_json'), dict) else {}
    sources = task.get('sources') if isinstance(task.get('sources'), list) else []
    allowed_urls = _research_source_urls(sources)
    confidence = clean(task.get('confidence') or research.get('confidence'), 40).lower()
    missing_fields = [clean(x, 120) for x in (task.get('missing_fields') or []) if clean(x, 120)]
    error_raw = clean(task.get('error'), 1000)
    error_code = clean(error_raw.split(':', 1)[0], 120) if error_raw else ''
    validation = validate_research_payload(research, allowed_source_urls=allowed_urls) if status == 'COMPLETE' else {'ok': False, 'errors': [], 'blankFields': []}
    blank_core = [clean(x, 120) for x in (validation.get('blankFields') or []) if clean(x, 120)]
    combined_missing = list(dict.fromkeys(missing_fields + blank_core))
    contract_valid = bool(
        status == 'COMPLETE'
        and validation.get('ok')
        and not blank_core
        and confidence in {'medium', 'high'}
        and bool(allowed_urls)
        and not error_code
    )
    upper_error = error_code.upper()
    return {
        'status': status,
        'confidence': confidence or '',
        'missingFields': combined_missing,
        'evidenceCount': len(allowed_urls),
        'contractValid': contract_valid,
        'validationIssueCount': len(validation.get('errors') or []),
        'errorCode': error_code,
        'identityConflict': 'IDENTITY' in upper_error or 'SAME_PLATFORM' in upper_error,
        'sourceConflict': 'SOURCE' in upper_error or 'AUTHORITATIVE' in upper_error or 'SUPERSEDED' in upper_error,
        'updatedAt': clean(task.get('updated_at'), 80),
        'collectionDate': clean(task.get('collection_date'), 20),
        'analysisRunId': clean(task.get('analysis_run_id'), 200),
    }


def _research_run_source_types():
    out = {}
    try:
        with connect() as c:
            rows = c.execute('SELECT id,result_json FROM analysis_runs').fetchall()
    except Exception:
        return out
    for row in rows:
        try:
            raw = row['result_json']
            result = raw if isinstance(raw, dict) else json.loads(raw or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        collector = result.get('collector') if isinstance(result, dict) and isinstance(result.get('collector'), dict) else {}
        explicit = clean(collector.get('sourceType'), 80)
        out[clean(row['id'], 200)] = explicit or 'LEGACY_INFERRED'
    return out


def _select_app_research_tasks(tasks, run_source_types):
    latest = {}
    excluded_non_app = 0
    excluded_unknown_origin = 0
    for task in tasks:
        run_id = clean(task.get('analysis_run_id'), 200)
        source_type = clean(run_source_types.get(run_id), 80) or 'UNKNOWN'
        if source_type == 'UNKNOWN':
            excluded_unknown_origin += 1
            continue
        if source_type not in {'SHORT_DRAMA_APP', 'LEGACY_INFERRED'}:
            excluded_non_app += 1
            continue
        platform = clean(task.get('platform'), 80)
        norm = normalize_title(task.get('normalized_title') or task.get('title'))
        if not platform or not norm:
            continue
        key = (platform, norm)
        stamp = (clean(task.get('updated_at'), 80), clean(task.get('created_at'), 80), clean(task.get('id'), 120))
        previous = latest.get(key)
        if previous is None or stamp > previous[0]:
            latest[key] = (stamp, task, source_type)
    return latest, excluded_non_app, excluded_unknown_origin


def _attach_research_task_projection(records):
    if not persistence.configured():
        return {'state': 'NOT_CONFIGURED', 'taskCount': 0, 'matchedRecords': 0, 'excludedNonAppTasks': 0, 'excludedUnknownOrigin': 0}
    try:
        tasks = persistence.list_research_tasks(limit=500)
    except Exception as exc:
        return {'state': 'UNAVAILABLE', 'taskCount': 0, 'matchedRecords': 0, 'excludedNonAppTasks': 0, 'excludedUnknownOrigin': 0, 'errorType': type(exc).__name__}

    run_source_types = _research_run_source_types()
    latest, excluded_non_app, excluded_unknown_origin = _select_app_research_tasks(tasks, run_source_types)
    matched = 0
    for record in records:
        key = (clean(record.get('app'), 80), normalize_title(record.get('title')))
        selected = latest.get(key)
        if selected:
            view = _research_task_view(selected[1])
            view['sourceType'] = selected[2]
            record['researchTask'] = view
            matched += 1
        else:
            record['researchTask'] = {
                'status': 'UNKNOWN', 'confidence': '', 'missingFields': [],
                'evidenceCount': 0, 'contractValid': False, 'validationIssueCount': 0,
                'errorCode': '', 'identityConflict': False, 'sourceConflict': False,
                'updatedAt': '', 'collectionDate': '', 'analysisRunId': '', 'sourceType': 'UNKNOWN',
            }
    return {
        'state': 'AVAILABLE',
        'taskCount': len(tasks),
        'matchedRecords': matched,
        'excludedNonAppTasks': excluded_non_app,
        'excludedUnknownOrigin': excluded_unknown_origin,
    }

def public_data():
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
    # Dynamic records are created after the first override pass, so apply overrides
    # again to make completed research visible without requiring a restart.
    for r in records:
        r.update(overrides.get(r.get('id'),{})); r['laneTerms']=split_lane(r.get('lane'))
    research_projection=_attach_research_task_projection(records)
    summary=build_live_summary(records, PLATFORM_ORDER, clean, split_lane)
    summary['researchProjection']=research_projection
    return {'records':records,'summary':summary}


def known_titles(before_date=''):
    """Backward-compatible name for authoritative prior App-ranking titles.

    Newness for SHORT_DRAMA_APP must never be consumed by Official Web or other
    secondary observations. Legacy V1/V1.2 histories are retained as reviewed
    App-ranking screenshot facts by ranking_lifecycle.
    """
    return lifecycle_known_app_ranked_titles(public_data()['records'], before_date)


def apply_research_result(task, research):
    title=clean(task.get('title'),500); platform=clean(task.get('platform'),40)
    data=public_data()
    record=select_same_platform_record(data['records'], platform=platform, title=title)
    if not record or not record.get('id'):
        raise RuntimeError(f'RESEARCH_RECORD_NOT_FOUND_SAME_PLATFORM: {platform} {title}')
    fields={k:clean(research.get(k),6000) for k in EDITABLE_FIELDS if clean(research.get(k),6000)}
    fields['researchStatus']='已研究'
    fields['researchConfidence']=clean(research.get('confidence'),30)
    fields['researchSources']=[clean(x,1000) for x in (research.get('sourceUrls') or []) if clean(x,1000)]
    fields['researchMissingFields']=[clean(x,120) for x in (research.get('missingFields') or []) if clean(x,120)]
    fact_fields={'synopsis','openingSummary','payEpisode','paywallSummary'}
    fields['researchFieldProvenance']={
        key:('source_fact' if key in fact_fields else 'analysis_judgment')
        for key in fields
        if key in EDITABLE_FIELDS
    }
    drama_id=record['id']
    with connect() as c:
        old=c.execute('SELECT fields_json FROM drama_overrides WHERE drama_id=?',(drama_id,)).fetchone()
        merged=json.loads(old['fields_json']) if old else {}; merged.update(fields); now=datetime.now(timezone.utc).isoformat()
        c.execute('INSERT INTO drama_overrides VALUES(?,?,?) ON CONFLICT(drama_id) DO UPDATE SET fields_json=excluded.fields_json,updated_at=excluded.updated_at',(drama_id,json.dumps(merged,ensure_ascii=False),now)); c.commit()
    if persistence.configured(): persistence.save_override(drama_id, merged)


def run_analysis_batch(collection_date, platform):
    now=datetime.now(timezone.utc).isoformat(); run_id=uuid.uuid4().hex
    if persistence.configured():
        rows=materialize_persistent_batch(collection_date, platform)
    else:
        with connect() as c:
            local=c.execute('SELECT * FROM collection_uploads WHERE collection_date=? AND platform=? ORDER BY created_at ASC',(collection_date,platform)).fetchall()
            rows=[dict(r) for r in local if Path(r['storage_path']).is_file()]
    if not rows:
        print(f'[analysis] no readable uploads for {collection_date} {platform}')
        return
    upload_ids=[r['id'] for r in rows]
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?)',(run_id,collection_date,platform,json.dumps(upload_ids),'分析中','{}','',analysis_model_name(),now,now))
        c.executemany('UPDATE collection_uploads SET status=? WHERE id=?',[('分析中',x) for x in upload_ids]); c.commit()
    if persistence.configured():
        persistence.update_upload_status(upload_ids,'分析中')
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status='分析中',result={},error='',model=analysis_model_name(),created_at=now,updated_at=now)
    try:
        result=analyze_batch(collection_date=collection_date,platform=platform,image_rows=rows,known_titles=known_titles(collection_date))
        existing={normalize_title(t):t for t in known_titles(collection_date)}
        for item in result.get('rows') or []:
            norm=normalize_title(item.get('title'))
            if norm and norm in existing:
                item['newness']='old'; item['matchedExistingTitle']=existing[norm]
        needs_research=any((x.get('newness') in {'new','uncertain'} or x.get('pendingChecks')) for x in (result.get('rows') or []))
        status='已识别-待深研' if needs_research else '已分析'; error=''
    except Exception as exc:
        result={}; status='分析失败'; error=clean(exc,4000)
        print(f'[analysis] failed {collection_date} {platform}: {error}')
    updated=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        c.execute('UPDATE analysis_runs SET status=?,result_json=?,error=?,updated_at=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),error,updated,run_id))
        c.executemany('UPDATE collection_uploads SET status=? WHERE id=?',[(status,x) for x in upload_ids]); c.commit()
    if persistence.configured():
        persistence.update_upload_status(upload_ids,status)
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status=status,result=result,error=error,model=analysis_model_name(),created_at=now,updated_at=updated)
    if status=='已识别-待深研':
        schedule_research_worker(apply_research=apply_research_result, delay=0.5)
    print(f'[analysis] {status} {collection_date} {platform} run={run_id}')


def schedule_analysis(collection_date, platform, delay=8):
    key=f'{collection_date}|{platform}'
    if not analysis_configured():
        with connect() as c:
            c.execute("UPDATE collection_uploads SET status='待配置分析API' WHERE collection_date=? AND platform=? AND status='待分析'",(collection_date,platform)); c.commit()
        return False
    def fire():
        with ANALYSIS_TIMER_LOCK: ANALYSIS_TIMERS.pop(key,None)
        run_analysis_batch(collection_date,platform)
    with ANALYSIS_TIMER_LOCK:
        old=ANALYSIS_TIMERS.pop(key,None)
        if old: old.cancel()
        timer=threading.Timer(delay,fire); timer.daemon=True; ANALYSIS_TIMERS[key]=timer; timer.start()
    return True


def render_index_html():
    data=public_data()
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</script>','<\\/script>')
    html=re.sub(r'const DATA\s*=\s*\{.*?\};\s*\n\s*const records', f'const DATA = {payload};\n    const records', INDEX_HTML, count=1, flags=re.S)
    if '榜单采集中心' not in html:
        html=html.replace('<button data-view-button="lifecycle">历史生命周期</button>', '<button data-view-button="lifecycle">历史生命周期</button>\n        <button type="button" onclick="location.href=\'/collect\'">榜单采集中心</button>',1)
    dates=data['summary'].get('dates') or []
    if dates:
        html=re.sub(r'已纳入真实采集日：[^。]+。趋势和生命周期只使用截图采集记录。', f"已纳入真实采集日：{'、'.join(dates)}。趋势和生命周期只使用截图采集记录。", html, count=1)
        html=re.sub(r'<div class="date-pill">\d+个真实采集日</div>', f'<div class="date-pill">{len(dates)}个真实采集日</div>', html, count=1)
    return html


class Handler(BaseHTTPRequestHandler):
    server_version='ShortDramaMonitor/1.4-collector'
    def send_bytes(self,body,content_type,status=200,headers=None):
        self.send_response(status); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def send_json(self,value,status=200): self.send_bytes(json.dumps(value,ensure_ascii=False).encode(),'application/json; charset=utf-8',status)
    def authorized(self):
        pw=os.environ.get('ADMIN_PASSWORD','').strip()
        if not pw: self.send_json({'error':'管理员入口尚未配置密码'},503); return False
        user=os.environ.get('ADMIN_USER','admin'); auth=self.headers.get('Authorization','')
        if auth.startswith('Basic '):
            try:
                u,p=base64.b64decode(auth[6:]).decode().split(':',1)
                if hmac.compare_digest(u,user) and hmac.compare_digest(p,pw): return True
            except (ValueError,UnicodeDecodeError,binascii.Error): pass
        self.send_bytes('需要管理员登录。'.encode(),'text/plain; charset=utf-8',401,{'WWW-Authenticate':'Basic realm="Short Drama Admin"'}); return False
    def read_json(self,limit=32_000_000):
        n=int(self.headers.get('Content-Length','0'))
        if n<=0 or n>limit: raise ValueError('提交内容大小不正确')
        return json.loads(self.rfile.read(n).decode())
    def analysis_link(self,row,ttl=86400):
        storage=str(row['storage_path'] or '')
        if storage.startswith('supabase:'): return ''
        secret=os.environ.get('ADMIN_PASSWORD','').strip()
        if not secret:return ''
        exp=int(time.time())+ttl; msg=f"{row['id']}|{row['sha256']}|{exp}".encode(); sig=hmac.new(secret.encode(),msg,hashlib.sha256).hexdigest()
        return f"/analysis/uploads/{row['id']}?exp={exp}&sig={sig}"
    def upload_payload(self,row):
        d=dict(row); storage=str(d.get('storage_path') or '')
        d['available']=storage.startswith('supabase:') or Path(storage).is_file()
        d['analysisUrl']=self.analysis_link(d) if d['available'] else ''
        d['persistent']=storage.startswith('supabase:') or persistence.configured()
        d.pop('storage_path',None); d.pop('sha256',None); d.pop('mime_type',None); return d
    def analysis_run_payload(self,row):
        d=dict(row)
        try:d['result']=json.loads(d.pop('result_json') or '{}')
        except json.JSONDecodeError:d['result']={}; d.pop('result_json',None)
        try:d['uploadIds']=json.loads(d.pop('upload_ids_json') or '[]')
        except json.JSONDecodeError:d['uploadIds']=[]; d.pop('upload_ids_json',None)
        return d
    def serve_analysis_upload(self,upload_id,query):
        if not re.fullmatch(r'[0-9a-f]{32}',upload_id): self.send_json({'error':'无效的分析链接'},400); return
        try: exp=int((query.get('exp') or ['0'])[0]); sig=(query.get('sig') or [''])[0]
        except (TypeError,ValueError): self.send_json({'error':'无效的分析链接'},400); return
        if exp<int(time.time()): self.send_json({'error':'分析链接已过期，请在采集中心重新复制'},410); return
        with connect() as c: row=c.execute('SELECT * FROM collection_uploads WHERE id=?',(upload_id,)).fetchone()
        if not row: self.send_json({'error':'截图记录不存在'},404); return
        if str(row['storage_path'] or '').startswith('supabase:'):
            self.send_json({'error':'持久化截图请由后台分析引擎读取','status':'persistent-storage'},409); return
        secret=os.environ.get('ADMIN_PASSWORD','').strip()
        if not secret: self.send_json({'error':'管理员入口尚未配置密码'},503); return
        expected=hmac.new(secret.encode(),f"{row['id']}|{row['sha256']}|{exp}".encode(),hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(sig,expected): self.send_json({'error':'分析链接签名无效'},403); return
        target=Path(row['storage_path'])
        if not target.is_file(): self.send_json({'error':'截图原文件已失效，请重新上传','status':'expired-storage'},410); return
        self.send_bytes(target.read_bytes(),row['mime_type'])
    def do_GET(self):
        parsed=urlparse(self.path); path=parsed.path
        if path=='/': self.send_bytes(render_index_html().encode(),'text/html; charset=utf-8')
        elif path in ('/collect','/collect.html'):
            if self.authorized(): self.send_bytes(COLLECT_HTML.encode(),'text/html; charset=utf-8')
        elif path=='/review':
            if self.authorized(): self.send_bytes(REVIEW_HTML.encode(),'text/html; charset=utf-8')
        elif path=='/api/data': self.send_json(public_data())
        elif path=='/api/review-data':
            if self.authorized(): self.send_json({**public_data(),'researchMeta':RESEARCH_META,'accountEmail':'管理员'})
        elif path=='/api/admin/uploads':
            if self.authorized():
                with connect() as c: rows=c.execute('SELECT * FROM collection_uploads ORDER BY created_at DESC LIMIT 100').fetchall()
                self.send_json({'uploads':[self.upload_payload(r) for r in rows],'analysisConfigured':analysis_configured(),'persistenceConfigured':persistence.configured()})
        elif path=='/api/admin/analysis-runs':
            if self.authorized():
                with connect() as c: rows=c.execute('SELECT * FROM analysis_runs ORDER BY created_at DESC LIMIT 30').fetchall()
                self.send_json({'runs':[self.analysis_run_payload(r) for r in rows],'analysisConfigured':analysis_configured(),'model':analysis_model_name(),'persistenceConfigured':persistence.configured()})
        elif path=='/api/admin/research-tasks':
            if self.authorized():
                try:
                    tasks=persistence.list_research_tasks(limit=100) if persistence.configured() else []
                    self.send_json({'tasks':tasks,'researchConfigured':research_configured(),'researchRunning':research_running()})
                except Exception as e:self.send_json({'error':'研究队列读取失败：'+clean(e,2000)},502)
        elif path.startswith('/analysis/uploads/'): self.serve_analysis_upload(path.rsplit('/',1)[-1],parse_qs(parsed.query))
        elif path=='/health':
            d=public_data()
            with connect() as c:
                upload_rows=c.execute('SELECT storage_path FROM collection_uploads').fetchall(); upload_count=len(upload_rows); available_count=sum(1 for r in upload_rows if str(r['storage_path']).startswith('supabase:') or Path(r['storage_path']).is_file()); run_count=c.execute('SELECT COUNT(*) FROM analysis_runs').fetchone()[0]
            self.send_json({'ok':True,'liveness':True,'records':len(d['records']),'collectionDate':d['summary'].get('collectionDate'),'latestRows':d['summary'].get('totalRows'),'newTitles':d['summary'].get('newTitles'),'uploads':upload_count,'availableUploads':available_count,'analysisRuns':run_count,'analysisConfigured':analysis_configured(),'analysisModel':analysis_model_name(),'persistenceConfigured':persistence.configured(),'version':'1.4-collector','gitCommit':clean(os.environ.get('RENDER_GIT_COMMIT',''),80)})
        elif path=='/ready':
            status=readiness_status()
            self.send_json(status,200 if status.get('ready') else 503)
        else: self.send_json({'error':'页面不存在'},404)
    def do_POST(self):
        path=urlparse(self.path).path
        if path=='/api/admin/collector-import':
            if not self.authorized(): return
            try:
                p=self.read_json(3_000_000)
                date=clean(p.get('collection_date'),20) if isinstance(p,dict) else ''
                normalized=validate_and_normalize(p,known_titles(date) if date else known_titles())
                now=datetime.now(timezone.utc).isoformat(); run_id=normalized['runId']
                with connect() as c:
                    prior=c.execute('SELECT created_at FROM analysis_runs WHERE id=?',(run_id,)).fetchone()
                    created=prior['created_at'] if prior and prior['created_at'] else now
                    c.execute(
                        '''INSERT OR REPLACE INTO analysis_runs
                           (id,collection_date,platform,upload_ids_json,status,result_json,error,model,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (run_id,normalized['collectionDate'],normalized['platform'],'[]',normalized['status'],json.dumps(normalized['result'],ensure_ascii=False),'',normalized['model'],created,now),
                    ); c.commit()
                persistent=persistence.configured()
                if persistent:
                    persistence.save_analysis_run(run_id=run_id,collection_date=normalized['collectionDate'],platform=normalized['platform'],upload_ids=[],status=normalized['status'],result=normalized['result'],error='',model=normalized['model'],provider=normalized['provider'],created_at=created,updated_at=now)
                worker=False
                if normalized['newTitleCount'] and persistent and research_configured():
                    worker=schedule_research_worker(apply_research=apply_research_result,delay=0.1)
                self.send_json({'imported':True,'idempotentRunId':run_id,'date':normalized['collectionDate'],'platform':normalized['platform'],'rows':normalized['rowCount'],'newTitleCount':normalized['newTitleCount'],'newTitles':normalized['newTitles'],'status':normalized['status'],'factPublished':True,'persistent':persistent,'researchConfigured':research_configured(),'researchWorkerScheduled':worker,'note':'结构化榜单事实已直接入库并发布；新剧已进入深研队列。' if normalized['newTitleCount'] else '结构化榜单事实已直接入库并发布；本批无新增剧目。'},201)
            except (CollectorImportError,ValueError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400)
            except Exception as e:self.send_json({'error':'collector import failed: '+clean(e,2000)},502)
            return
        if path=='/api/admin/research':
            if not self.authorized(): return
            if not research_configured(): self.send_json({'error':'TAVILY_API_KEY_NOT_CONFIGURED'},503); return
            scheduled=schedule_research_worker(apply_research=apply_research_result,delay=0.1)
            self.send_json({'scheduled':scheduled,'researchConfigured':research_configured()},202); return
        if path=='/api/admin/analyze':
            if not self.authorized(): return
            try:
                p=self.read_json(50_000); platform=clean(p.get('platform'),40); date=clean(p.get('date'),20)
                if platform not in PLATFORM_ORDER: raise ValueError('平台不正确')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): raise ValueError('采集日期不正确')
                if not analysis_configured(): self.send_json({'error':'GEMINI_API_KEY_NOT_CONFIGURED'},503); return
                schedule_analysis(date,platform,delay=0.2); self.send_json({'scheduled':True,'date':date,'platform':platform,'model':analysis_model_name(),'persistent':persistence.configured()},202)
            except (ValueError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400)
            return
        if path!='/api/admin/uploads': self.send_json({'error':'页面不存在'},404); return
        if not self.authorized(): return
        try:
            p=self.read_json(); platform=clean(p.get('platform'),40); date=clean(p.get('date'),20); filename=Path(clean(p.get('filename'),300)).name; mime=clean(p.get('mimeType'),100)
            if platform not in PLATFORM_ORDER: raise ValueError('平台不正确')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): raise ValueError('采集日期不正确')
            if mime not in {'image/jpeg','image/png','image/webp'}: raise ValueError('仅支持JPG、PNG或WebP截图')
            raw=base64.b64decode(clean(p.get('dataBase64'),31_000_000),validate=True)
            if not raw or len(raw)>15*1024*1024: raise ValueError('截图为空或超过15MB')
            uid=uuid.uuid4().hex; ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[mime]; target=UPLOAD_DIR/f'{date}_{platform.lower()}_{uid}{ext}'; target.write_bytes(raw); now=datetime.now(timezone.utc).isoformat(); sha=hashlib.sha256(raw).hexdigest()
            with connect() as c:
                c.execute('INSERT INTO collection_uploads VALUES(?,?,?,?,?,?,?,?,?)',(uid,date,platform,filename,mime,str(target),sha,'待分析',now)); c.commit(); row=c.execute('SELECT * FROM collection_uploads WHERE id=?',(uid,)).fetchone()
            if persistence.configured():
                persistence.persist_upload(upload_id=uid,collection_date=date,platform=platform,filename=filename,mime_type=mime,raw=raw,sha256=sha,status='待分析',created_at=now)
            scheduled=schedule_analysis(date,platform); payload=self.upload_payload(row)
            note='截图已保存到持久化存储；系统将在最后一张上传约8秒后自动合并同日同平台截图进行分析。' if scheduled and persistence.configured() else ('截图已保存；系统将在最后一张上传约8秒后自动分析。' if scheduled else '截图已保存；自动分析尚未配置 GEMINI_API_KEY。')
            self.send_json({'uploaded':True,'id':uid,'status':'待分析' if scheduled else '待配置分析API','analysisUrl':payload.get('analysisUrl'),'autoAnalysisScheduled':scheduled,'persistent':persistence.configured(),'note':note},201)
        except (ValueError,json.JSONDecodeError,binascii.Error) as e: self.send_json({'error':str(e)},400)
        except Exception as e: self.send_json({'error':'持久化存储失败：'+clean(e,2000)},502)
    def do_PUT(self):
        path=urlparse(self.path).path; prefix='/api/reviews/'
        if not path.startswith(prefix): self.send_json({'error':'页面不存在'},404); return
        if not self.authorized(): return
        try:
            drama_id=unquote(path[len(prefix):]); payload=self.read_json(200_000); raw=payload.get('fields') or {}; fields={k:clean(v) for k,v in raw.items() if k in EDITABLE_FIELDS and isinstance(v,str)}
            if not fields: raise ValueError('没有可保存的字段')
            with connect() as c:
                old=c.execute('SELECT fields_json FROM drama_overrides WHERE drama_id=?',(drama_id,)).fetchone(); merged=json.loads(old['fields_json']) if old else {}; merged.update(fields); now=datetime.now(timezone.utc).isoformat(); c.execute('INSERT INTO drama_overrides VALUES(?,?,?) ON CONFLICT(drama_id) DO UPDATE SET fields_json=excluded.fields_json,updated_at=excluded.updated_at',(drama_id,json.dumps(merged,ensure_ascii=False),now)); c.commit()
            if persistence.configured(): persistence.save_override(drama_id, merged)
            self.send_json({'saved':True,'dramaId':drama_id,'fields':fields,'updatedAt':now,'persistent':persistence.configured()})
        except (ValueError,json.JSONDecodeError) as e: self.send_json({'error':str(e)},400)
        except Exception as e:self.send_json({'error':'持久化保存失败：'+clean(e,2000)},502)
    def log_message(self,fmt,*args): print('[%s] %s'%(self.log_date_time_string(),fmt%args), flush=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','4173'))); parser.add_argument('--no-open',action='store_true'); args=parser.parse_args(); connect().close(); sync_persistent_cache(); server=ThreadingHTTPServer((args.host,args.port),Handler); url=f'http://127.0.0.1:{args.port}/'; print('短剧研究工具 V1.4 Collector：'+url, flush=True); print(f'自动分析配置：{analysis_configured()} model={analysis_model_name()} persistence={persistence.configured()}', flush=True)
    print(f'自动深研配置：{research_configured()} running={research_running()}', flush=True)
    schedule_research_worker(apply_research=apply_research_result,delay=1.0)
    if not args.no_open: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
