from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import app
import persistence


def _sync_cache():
    if not persistence.configured():
        print('[persistence] not configured; using local ephemeral cache only')
        return
    try:
        uploads = persistence.list_uploads(500)
        runs = persistence.list_analysis_runs(200)
        overrides = persistence.list_overrides()
        with app.connect() as c:
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


def _materialize_remote_rows(rows: list[dict]) -> list[dict]:
    out=[]
    for row in rows:
        signed=row.get('signed_url') or ''
        if not signed:
            continue
        ext={
            'image/png':'.png', 'image/webp':'.webp', 'image/jpeg':'.jpg'
        }.get(row.get('mime_type'), '.jpg')
        target=app.UPLOAD_DIR / f"remote_{row.get('id')}{ext}"
        with urllib.request.urlopen(signed, timeout=120) as resp:
            target.write_bytes(resp.read())
        d=dict(row)
        d['storage_path']=str(target)
        out.append(d)
    return out


def run_analysis_batch(collection_date, platform):
    now=datetime.now(timezone.utc).isoformat(); run_id=uuid.uuid4().hex
    if persistence.configured():
        remote_rows=persistence.get_batch(collection_date, platform)
        rows=_materialize_remote_rows(remote_rows)
    else:
        with app.connect() as c:
            local=c.execute('SELECT * FROM collection_uploads WHERE collection_date=? AND platform=? ORDER BY created_at ASC',(collection_date,platform)).fetchall()
            rows=[dict(r) for r in local if Path(r['storage_path']).is_file()]
    if not rows:
        print(f'[analysis] no readable uploads for {collection_date} {platform}')
        return
    upload_ids=[r['id'] for r in rows]
    with app.connect() as c:
        c.execute('INSERT OR REPLACE INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?)',(run_id,collection_date,platform,json.dumps(upload_ids),'分析中','{}','',app.analysis_model_name(),now,now))
        c.executemany('UPDATE collection_uploads SET status=? WHERE id=?',[('分析中',x) for x in upload_ids]); c.commit()
    if persistence.configured():
        persistence.update_upload_status(upload_ids,'分析中')
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status='分析中',result={},error='',model=app.analysis_model_name(),created_at=now,updated_at=now)
    try:
        result=app.analyze_batch(collection_date=collection_date,platform=platform,image_rows=rows,known_titles=app.known_titles())
        existing={app.normalize_title(t):t for t in app.known_titles()}
        for item in result.get('rows') or []:
            norm=app.normalize_title(item.get('title'))
            if norm and norm in existing:
                item['newness']='old'; item['matchedExistingTitle']=existing[norm]
        needs_research=any((x.get('newness') in {'new','uncertain'} or x.get('pendingChecks')) for x in (result.get('rows') or []))
        status='已识别-待深研' if needs_research else '已分析'; error=''
    except Exception as exc:
        result={}; status='分析失败'; error=app.clean(exc,4000)
        print(f'[analysis] failed {collection_date} {platform}: {error}')
    updated=datetime.now(timezone.utc).isoformat()
    with app.connect() as c:
        c.execute('UPDATE analysis_runs SET status=?,result_json=?,error=?,updated_at=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),error,updated,run_id))
        c.executemany('UPDATE collection_uploads SET status=? WHERE id=?',[(status,x) for x in upload_ids]); c.commit()
    if persistence.configured():
        persistence.update_upload_status(upload_ids,status)
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status=status,result=result,error=error,model=app.analysis_model_name(),created_at=now,updated_at=updated)
    print(f'[analysis] {status} {collection_date} {platform} run={run_id}')


class PersistentHandler(app.Handler):
    def upload_payload(self,row):
        d=dict(row)
        storage=str(d.get('storage_path') or '')
        if storage.startswith('supabase:'):
            d['available']=True; d['analysisUrl']=''
            d.pop('storage_path',None); d.pop('sha256',None); d.pop('mime_type',None)
            return d
        return super().upload_payload(row)

    def do_POST(self):
        path=urlparse(self.path).path
        if path!='/api/admin/uploads':
            return super().do_POST()
        if not self.authorized(): return
        try:
            p=self.read_json(); platform=app.clean(p.get('platform'),40); date=app.clean(p.get('date'),20); filename=Path(app.clean(p.get('filename'),300)).name; mime=app.clean(p.get('mimeType'),100)
            if platform not in app.PLATFORM_ORDER: raise ValueError('平台不正确')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date): raise ValueError('采集日期不正确')
            if mime not in {'image/jpeg','image/png','image/webp'}: raise ValueError('仅支持JPG、PNG或WebP截图')
            raw=base64.b64decode(app.clean(p.get('dataBase64'),31_000_000),validate=True)
            if not raw or len(raw)>15*1024*1024: raise ValueError('截图为空或超过15MB')
            uid=uuid.uuid4().hex; ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[mime]
            target=app.UPLOAD_DIR/f'{date}_{platform.lower()}_{uid}{ext}'; target.write_bytes(raw)
            now=datetime.now(timezone.utc).isoformat(); sha=hashlib.sha256(raw).hexdigest()
            with app.connect() as c:
                c.execute('INSERT INTO collection_uploads VALUES(?,?,?,?,?,?,?,?,?)',(uid,date,platform,filename,mime,str(target),sha,'待分析',now)); c.commit(); row=c.execute('SELECT * FROM collection_uploads WHERE id=?',(uid,)).fetchone()
            if persistence.configured():
                persistence.persist_upload(upload_id=uid,collection_date=date,platform=platform,filename=filename,mime_type=mime,raw=raw,sha256=sha,status='待分析',created_at=now)
            scheduled=app.schedule_analysis(date,platform); payload=self.upload_payload(row)
            note='截图已保存到持久化存储；系统将在最后一张上传约8秒后自动合并同日同平台截图进行分析。' if scheduled else '截图已保存；自动分析尚未配置 GEMINI_API_KEY。'
            self.send_json({'uploaded':True,'id':uid,'status':'待分析' if scheduled else '待配置分析API','analysisUrl':payload.get('analysisUrl'),'autoAnalysisScheduled':scheduled,'persistent':persistence.configured(),'note':note},201)
        except (ValueError,json.JSONDecodeError,binascii.Error) as e:
            self.send_json({'error':str(e)},400)
        except Exception as e:
            self.send_json({'error':'持久化存储失败：'+app.clean(e,2000)},502)

    def do_PUT(self):
        path=urlparse(self.path).path; prefix='/api/reviews/'
        if not path.startswith(prefix): return super().do_PUT()
        if not self.authorized(): return
        try:
            drama_id=unquote(path[len(prefix):]); payload=self.read_json(200_000); raw=payload.get('fields') or {}; fields={k:app.clean(v) for k,v in raw.items() if k in app.EDITABLE_FIELDS and isinstance(v,str)}
            if not fields: raise ValueError('没有可保存的字段')
            with app.connect() as c:
                old=c.execute('SELECT fields_json FROM drama_overrides WHERE drama_id=?',(drama_id,)).fetchone(); merged=json.loads(old['fields_json']) if old else {}; merged.update(fields); now=datetime.now(timezone.utc).isoformat(); c.execute('INSERT INTO drama_overrides VALUES(?,?,?) ON CONFLICT(drama_id) DO UPDATE SET fields_json=excluded.fields_json,updated_at=excluded.updated_at',(drama_id,json.dumps(merged,ensure_ascii=False),now)); c.commit()
            if persistence.configured(): persistence.save_override(drama_id, merged)
            self.send_json({'saved':True,'dramaId':drama_id,'fields':fields,'updatedAt':now,'persistent':persistence.configured()})
        except (ValueError,json.JSONDecodeError) as e:self.send_json({'error':str(e)},400)
        except Exception as e:self.send_json({'error':'持久化保存失败：'+app.clean(e,2000)},502)


app.run_analysis_batch=run_analysis_batch
app.Handler=PersistentHandler
_sync_cache()

if __name__=='__main__':
    app.main()
