from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request


def configured() -> bool:
    return bool(os.environ.get('SUPABASE_PERSISTENCE_URL', '').strip() and os.environ.get('MONITOR_PERSISTENCE_TOKEN', '').strip())


def _call(action: str, **payload):
    url = os.environ.get('SUPABASE_PERSISTENCE_URL', '').strip()
    token = os.environ.get('MONITOR_PERSISTENCE_TOKEN', '').strip()
    if not url or not token:
        raise RuntimeError('SUPABASE_PERSISTENCE_NOT_CONFIGURED')
    body = {'action': action, **payload}
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-monitor-token': token},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:4000]
        raise RuntimeError(f'SUPABASE_HTTP_{exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'SUPABASE_NETWORK_ERROR: {exc}') from exc
    if isinstance(data, dict) and data.get('error'):
        raise RuntimeError(f"SUPABASE_ERROR: {data['error']}")
    return data


def persist_upload(*, upload_id: str, collection_date: str, platform: str, filename: str, mime_type: str, raw: bytes, sha256: str, status: str, created_at: str):
    return _call(
        'upload',
        id=upload_id,
        collectionDate=collection_date,
        platform=platform,
        filename=filename,
        mimeType=mime_type,
        dataBase64=base64.b64encode(raw).decode('ascii'),
        sha256=sha256,
        status=status,
        createdAt=created_at,
    )


def get_batch(collection_date: str, platform: str):
    return (_call('get_batch', collectionDate=collection_date, platform=platform).get('rows') or [])


def list_uploads(limit: int = 500):
    return (_call('list_uploads', limit=limit).get('rows') or [])


def update_upload_status(ids: list[str], status: str):
    if not ids:
        return {'ok': True}
    return _call('update_upload_status', ids=ids, status=status)


def save_analysis_run(*, run_id: str, collection_date: str, platform: str, upload_ids: list[str], status: str, result: dict, error: str, model: str, provider: str = 'gemini', created_at: str, updated_at: str):
    return _call('save_analysis_run', run={
        'id': run_id,
        'collectionDate': collection_date,
        'platform': platform,
        'uploadIds': upload_ids,
        'status': status,
        'result': result,
        'error': error,
        'model': model,
        'provider': provider,
        'createdAt': created_at,
        'updatedAt': updated_at,
    })


def list_analysis_runs(limit: int = 200):
    return (_call('list_analysis_runs', limit=limit).get('rows') or [])


def save_override(drama_id: str, fields: dict):
    return _call('save_override', dramaId=drama_id, fields=fields)


def list_overrides():
    return (_call('list_overrides').get('rows') or [])
