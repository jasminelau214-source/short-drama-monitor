from pathlib import Path

path = Path('app.py')
text = path.read_text(encoding='utf-8')

old_import = "from live_observations import merge_analysis_records, build_live_summary\nimport persistence\n"
new_import = "from live_observations import merge_analysis_records, build_live_summary\nfrom research_worker import schedule as schedule_research_worker, configured as research_configured, running as research_running\nimport persistence\n"
if new_import not in text:
    if old_import not in text:
        raise SystemExit('IMPORT_ANCHOR_NOT_FOUND')
    text = text.replace(old_import, new_import, 1)

old_public = '''    # Fact publication is independent from deep research: every latest complete
    # screenshot analysis becomes a live ranking observation immediately.
    records=merge_analysis_records(records, connect, normalize_title, split_lane)
    return {'records':records,'summary':build_live_summary(records, PLATFORM_ORDER, clean, split_lane)}


def known_titles():
    return sorted({clean(r.get('title'), 500) for r in public_data()['records'] if clean(r.get('title'), 500)})
'''
new_public = '''    # Fact publication is independent from deep research: every latest complete
    # screenshot analysis becomes a live ranking observation immediately.
    records=merge_analysis_records(records, connect, normalize_title, split_lane)
    # Dynamic records are created after the first override pass, so apply overrides
    # again to make completed research visible without requiring a restart.
    for r in records:
        r.update(overrides.get(r.get('id'),{})); r['laneTerms']=split_lane(r.get('lane'))
    return {'records':records,'summary':build_live_summary(records, PLATFORM_ORDER, clean, split_lane)}


def known_titles(before_date=''):
    titles=set()
    for r in public_data()['records']:
        title=clean(r.get('title'),500)
        if not title: continue
        if before_date:
            prior=any(clean(h.get('date'),20) < before_date for h in (r.get('history') or []) if clean(h.get('date'),20))
            if not prior: continue
        titles.add(title)
    return sorted(titles)


def apply_research_result(task, research):
    title=clean(task.get('title'),500); platform=clean(task.get('platform'),40); norm=normalize_title(title)
    data=public_data(); candidates=[r for r in data['records'] if normalize_title(r.get('title'))==norm]
    record=next((r for r in candidates if clean(r.get('app'),40)==platform), None) or (candidates[0] if candidates else None)
    if not record or not record.get('id'):
        raise RuntimeError(f'RESEARCH_RECORD_NOT_FOUND: {platform} {title}')
    fields={k:clean(research.get(k),6000) for k in EDITABLE_FIELDS if clean(research.get(k),6000)}
    fields['researchStatus']='已研究'
    fields['researchConfidence']=clean(research.get('confidence'),30)
    fields['researchSources']=[clean(x,1000) for x in (research.get('sourceUrls') or []) if clean(x,1000)]
    drama_id=record['id']
    with connect() as c:
        old=c.execute('SELECT fields_json FROM drama_overrides WHERE drama_id=?',(drama_id,)).fetchone()
        merged=json.loads(old['fields_json']) if old else {}; merged.update(fields); now=datetime.now(timezone.utc).isoformat()
        c.execute('INSERT INTO drama_overrides VALUES(?,?,?) ON CONFLICT(drama_id) DO UPDATE SET fields_json=excluded.fields_json,updated_at=excluded.updated_at',(drama_id,json.dumps(merged,ensure_ascii=False),now)); c.commit()
    if persistence.configured(): persistence.save_override(drama_id, merged)
'''
if new_public not in text:
    if old_public not in text:
        raise SystemExit('PUBLIC_ANCHOR_NOT_FOUND')
    text = text.replace(old_public, new_public, 1)

text = text.replace("result=analyze_batch(collection_date=collection_date,platform=platform,image_rows=rows,known_titles=known_titles())", "result=analyze_batch(collection_date=collection_date,platform=platform,image_rows=rows,known_titles=known_titles(collection_date))")
text = text.replace("existing={normalize_title(t):t for t in known_titles()}", "existing={normalize_title(t):t for t in known_titles(collection_date)}")

old_finish = """    if persistence.configured():
        persistence.update_upload_status(upload_ids,status)
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status=status,result=result,error=error,model=analysis_model_name(),created_at=now,updated_at=updated)
    print(f'[analysis] {status} {collection_date} {platform} run={run_id}')
"""
new_finish = """    if persistence.configured():
        persistence.update_upload_status(upload_ids,status)
        persistence.save_analysis_run(run_id=run_id,collection_date=collection_date,platform=platform,upload_ids=upload_ids,status=status,result=result,error=error,model=analysis_model_name(),created_at=now,updated_at=updated)
    if status=='已识别-待深研':
        schedule_research_worker(apply_research=apply_research_result, delay=0.5)
    print(f'[analysis] {status} {collection_date} {platform} run={run_id}')
"""
if new_finish not in text:
    if old_finish not in text:
        raise SystemExit('ANALYSIS_FINISH_ANCHOR_NOT_FOUND')
    text = text.replace(old_finish, new_finish, 1)

old_analysis_get = """        elif path=='/api/admin/analysis-runs':
            if self.authorized():
                with connect() as c: rows=c.execute('SELECT * FROM analysis_runs ORDER BY created_at DESC LIMIT 30').fetchall()
                self.send_json({'runs':[self.analysis_run_payload(r) for r in rows],'analysisConfigured':analysis_configured(),'model':analysis_model_name(),'persistenceConfigured':persistence.configured()})
"""
new_analysis_get = old_analysis_get + """        elif path=='/api/admin/research-tasks':
            if self.authorized():
                try:
                    tasks=persistence.list_research_tasks(limit=100) if persistence.configured() else []
                    self.send_json({'tasks':tasks,'researchConfigured':research_configured(),'researchRunning':research_running()})
                except Exception as e:self.send_json({'error':'研究队列读取失败：'+clean(e,2000)},502)
"""
if "path=='/api/admin/research-tasks'" not in text:
    if old_analysis_get not in text:
        raise SystemExit('ANALYSIS_GET_ANCHOR_NOT_FOUND')
    text = text.replace(old_analysis_get, new_analysis_get, 1)

old_post = """        if path=='/api/admin/analyze':
            if not self.authorized(): return
"""
new_post = """        if path=='/api/admin/research':
            if not self.authorized(): return
            if not research_configured(): self.send_json({'error':'TAVILY_API_KEY_NOT_CONFIGURED'},503); return
            scheduled=schedule_research_worker(apply_research=apply_research_result,delay=0.1)
            self.send_json({'scheduled':scheduled,'researchConfigured':research_configured()},202); return
        if path=='/api/admin/analyze':
            if not self.authorized(): return
"""
if "path=='/api/admin/research'" not in text:
    if old_post not in text:
        raise SystemExit('POST_ANCHOR_NOT_FOUND')
    text = text.replace(old_post, new_post, 1)

old_main = """    if not args.no_open: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
"""
new_main = """    print(f'自动深研配置：{research_configured()} running={research_running()}', flush=True)
    schedule_research_worker(apply_research=apply_research_result,delay=1.0)
    if not args.no_open: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
"""
if new_main not in text:
    if old_main not in text:
        raise SystemExit('MAIN_ANCHOR_NOT_FOUND')
    text = text.replace(old_main, new_main, 1)

path.write_text(text, encoding='utf-8')
print('patched research worker integration')
