from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

import app
from collector_import import CollectorImportError, validate_and_normalize


class Handler(app.Handler):
    server_version = 'ShortDramaMonitor/1.4-collector'

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/api/admin/collector-import':
            return super().do_POST()
        if not self.authorized():
            return
        try:
            payload = self.read_json(3_000_000)
            date_hint = str(payload.get('collection_date') or '').strip() if isinstance(payload, dict) else ''
            known = app.known_titles(date_hint) if date_hint else app.known_titles()
            normalized = validate_and_normalize(payload, known)
            now = datetime.now(timezone.utc).isoformat()
            run_id = normalized['runId']

            with app.connect() as c:
                prior = c.execute('SELECT created_at FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
                created_at = prior['created_at'] if prior and prior['created_at'] else now
                c.execute(
                    '''INSERT OR REPLACE INTO analysis_runs
                       (id,collection_date,platform,upload_ids_json,status,result_json,error,model,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (
                        run_id,
                        normalized['collectionDate'],
                        normalized['platform'],
                        '[]',
                        normalized['status'],
                        json.dumps(normalized['result'], ensure_ascii=False),
                        '',
                        normalized['model'],
                        created_at,
                        now,
                    ),
                )
                c.commit()

            persistent = app.persistence.configured()
            if persistent:
                app.persistence.save_analysis_run(
                    run_id=run_id,
                    collection_date=normalized['collectionDate'],
                    platform=normalized['platform'],
                    upload_ids=[],
                    status=normalized['status'],
                    result=normalized['result'],
                    error='',
                    model=normalized['model'],
                    provider=normalized['provider'],
                    created_at=created_at,
                    updated_at=now,
                )

            worker_scheduled = False
            if normalized['newTitleCount'] and persistent and app.research_configured():
                worker_scheduled = app.schedule_research_worker(apply_research=app.apply_research_result, delay=0.1)

            self.send_json(
                {
                    'imported': True,
                    'idempotentRunId': run_id,
                    'date': normalized['collectionDate'],
                    'platform': normalized['platform'],
                    'rows': normalized['rowCount'],
                    'newTitleCount': normalized['newTitleCount'],
                    'newTitles': normalized['newTitles'],
                    'status': normalized['status'],
                    'factPublished': True,
                    'persistent': persistent,
                    'researchConfigured': app.research_configured(),
                    'researchWorkerScheduled': worker_scheduled,
                    'note': '结构化榜单事实已直接入库并发布；新剧已进入深研队列。' if normalized['newTitleCount'] else '结构化榜单事实已直接入库并发布；本批无新增剧目。',
                },
                201,
            )
        except (CollectorImportError, ValueError, json.JSONDecodeError) as exc:
            self.send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self.send_json({'error': 'collector import failed: ' + app.clean(exc, 2000)}, 502)


def main():
    parser = app.argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=int(app.os.environ.get('PORT', '4173')))
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()

    app.connect().close()
    app.sync_persistent_cache()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f'http://127.0.0.1:{args.port}/'
    print('短剧研究工具 V1.4 Collector：' + url, flush=True)
    print(f'自动分析配置：{app.analysis_configured()} model={app.analysis_model_name()} persistence={app.persistence.configured()}', flush=True)
    print(f'自动深研配置：{app.research_configured()} running={app.research_running()}', flush=True)
    app.schedule_research_worker(apply_research=app.apply_research_result, delay=1.0)
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
