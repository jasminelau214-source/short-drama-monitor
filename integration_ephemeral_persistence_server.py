from __future__ import annotations

import base64
import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


TOKEN = os.environ.get('MONITOR_PERSISTENCE_TOKEN', 'integration-phase-b-token').strip()
HOST = os.environ.get('INTEGRATION_PERSISTENCE_HOST', '127.0.0.1')
PORT = int(os.environ.get('INTEGRATION_PERSISTENCE_PORT', '9009'))


def _sql_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    encoded = base64.b64encode(raw).decode('ascii')
    return f"convert_from(decode('{encoded}','base64'),'UTF8')::jsonb"


def _sql_text(value: object) -> str:
    return "'" + str(value or '').replace("'", "''") + "'"


def _psql(sql: str) -> str:
    proc = subprocess.run(
        ['psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', sql],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or 'psql failed').strip()[:4000])
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ''


def _json_result(sql: str, fallback: object) -> object:
    raw = _psql(sql)
    return json.loads(raw) if raw else fallback


def _rows(table: str, limit: int, order_by: str) -> list[dict]:
    limit = max(1, min(int(limit), 1000))
    return _json_result(
        f"""
        select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text
        from (
          select * from public.{table}
          order by {order_by}
          limit {limit}
        ) t;
        """,
        [],
    )


def handle_action(body: dict) -> dict:
    action = str(body.get('action') or '')

    if action == 'monitoring_status':
        return {'ok': True, 'rows': [], 'integrationStub': True}

    if action == 'list_uploads':
        return {'rows': _rows('collection_uploads', body.get('limit') or 500, 'created_at desc')}

    if action == 'list_analysis_runs':
        return {'rows': _rows('analysis_runs', body.get('limit') or 200, 'updated_at desc, id')}

    if action == 'list_overrides':
        return {'rows': _rows('drama_overrides', 1000, 'updated_at desc, drama_id')}

    if action == 'list_research_tasks':
        limit = max(1, min(int(body.get('limit') or 100), 1000))
        statuses = [str(x) for x in (body.get('statuses') or []) if str(x)]
        where = ''
        if statuses:
            quoted = ','.join(_sql_text(x) for x in statuses)
            where = f'where status in ({quoted})'
        rows = _json_result(
            f"""
            select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text
            from (
              select * from public.research_tasks
              {where}
              order by priority desc, updated_at asc, id
              limit {limit}
            ) t;
            """,
            [],
        )
        return {'rows': rows}

    if action == 'save_analysis_run':
        run = body.get('run') if isinstance(body.get('run'), dict) else {}
        payload = _sql_json(run)
        _psql(
            f"""
            with p as (select {payload} as j)
            insert into public.analysis_runs(
              id, collection_date, platform, upload_ids, status, result_json,
              error, model, provider, created_at, updated_at
            )
            select
              j->>'id',
              (j->>'collectionDate')::date,
              j->>'platform',
              coalesce(j->'uploadIds','[]'::jsonb),
              coalesce(j->>'status',''),
              coalesce(j->'result','{{}}'::jsonb),
              coalesce(j->>'error',''),
              coalesce(j->>'model',''),
              coalesce(j->>'provider',''),
              coalesce(nullif(j->>'createdAt','')::timestamptz, now()),
              coalesce(nullif(j->>'updatedAt','')::timestamptz, now())
            from p
            on conflict (id) do update set
              collection_date=excluded.collection_date,
              platform=excluded.platform,
              upload_ids=excluded.upload_ids,
              status=excluded.status,
              result_json=excluded.result_json,
              error=excluded.error,
              model=excluded.model,
              provider=excluded.provider,
              updated_at=excluded.updated_at;
            """
        )
        return {'ok': True}

    if action == 'claim_research_tasks':
        limit = max(1, min(int(body.get('limit') or 1), 10))
        rows = _json_result(
            f"""
            with picked as (
              select id
              from public.research_tasks
              where status='PENDING'
              order by priority desc, updated_at asc, id
              limit {limit}
              for update skip locked
            ),
            changed as (
              update public.research_tasks t
              set status='RESEARCHING', updated_at=now()
              from picked
              where t.id=picked.id
              returning t.*
            )
            select coalesce(jsonb_agg(to_jsonb(changed)), '[]'::jsonb)::text
            from changed;
            """,
            [],
        )
        return {'rows': rows}

    if action == 'update_research_task':
        payload = _sql_json(body)
        _psql(
            f"""
            with p as (select {payload} as j)
            update public.research_tasks t
            set
              status=coalesce(p.j->>'status', t.status),
              research_json=coalesce(p.j->'research','{{}}'::jsonb),
              sources=coalesce(p.j->'sources','[]'::jsonb),
              confidence=coalesce(p.j->>'confidence',''),
              missing_fields=coalesce(
                array(select jsonb_array_elements_text(coalesce(p.j->'missingFields','[]'::jsonb))),
                array[]::text[]
              ),
              error=coalesce(p.j->>'error',''),
              updated_at=now()
            from p
            where t.id=(p.j->>'id')::bigint;
            """
        )
        return {'ok': True}

    if action == 'save_override':
        drama_id = str(body.get('dramaId') or '')
        fields = body.get('fields') if isinstance(body.get('fields'), dict) else {}
        _psql(
            f"""
            insert into public.drama_overrides(drama_id,fields_json,updated_at)
            values({_sql_text(drama_id)},{_sql_json(fields)},now())
            on conflict (drama_id) do update set
              fields_json=excluded.fields_json,
              updated_at=excluded.updated_at;
            """
        )
        return {'ok': True}

    if action == 'get_batch':
        return {'rows': []}

    if action == 'update_upload_status':
        return {'ok': True}

    raise RuntimeError(f'UNSUPPORTED_ACTION:{action}')


class Handler(BaseHTTPRequestHandler):
    server_version = 'JSMIntegrationPersistence/1.0'

    def log_message(self, format: str, *args) -> None:
        print('[integration-persistence] ' + (format % args), flush=True)

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == '/health':
            try:
                value = _psql('select 1;')
                self._send(200, {'ok': value == '1', 'integrationStub': True})
            except Exception as exc:
                self._send(503, {'ok': False, 'error': str(exc)[:500]})
            return
        self._send(404, {'error': 'not found'})

    def do_POST(self) -> None:
        supplied = self.headers.get('x-monitor-token', '')
        if not TOKEN or not hmac.compare_digest(supplied, TOKEN):
            self._send(401, {'error': 'unauthorized'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 5_000_000:
                raise ValueError('invalid body length')
            body = json.loads(self.rfile.read(length).decode('utf-8'))
            result = handle_action(body)
            self._send(200, result)
        except Exception as exc:
            self._send(500, {'error': str(exc)[:4000]})


if __name__ == '__main__':
    print(f'[integration-persistence] listening on {HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
