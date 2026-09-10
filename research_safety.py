from __future__ import annotations

import ipaddress
import os
import re
import threading
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ALLOWED_PLATFORMS = {'ReelShort','MoboReels','NetShort','DramaWave'}
SENSITIVE_QUERY_KEYS = {
    'token','access_token','auth','authorization','api_key','apikey','key','secret','signature','sig',
    'password','passwd','session','sessionid','jwt','credential','credentials','x-amz-signature',
    'x-amz-credential','x-goog-signature','x-goog-credential',
}
SENSITIVE_TEXT_PATTERNS = [
    re.compile(r'(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|sbp_[0-9A-Za-z_-]{20,})\b'),
    re.compile(r'(?i)\b(?:api[_ -]?key|access[_ -]?token|authorization|bearer|password|secret|private[_ -]?key)\s*[:=]\s*\S+'),
    re.compile(r'(?i)https?://\S+[?&](?:token|access_token|sig|signature|key|api_key|apikey|secret|jwt)=\S+'),
]
INJECTION_PATTERNS = [
    re.compile(p, re.I) for p in [
        r'ignore\s+(?:all\s+)?previous\s+instructions',
        r'ignore\s+(?:the\s+)?system\s+prompt',
        r'disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions',
        r'follow\s+these\s+instructions\s+instead',
        r'reveal\s+(?:the\s+)?(?:system|developer)\s+prompt',
        r'print\s+(?:your\s+)?(?:api\s*key|secret|token|password)',
        r'exfiltrat(?:e|ion)',
        r'execute\s+(?:this\s+)?(?:code|command|script)',
        r'call\s+(?:this\s+)?(?:url|endpoint|api)',
        r'you\s+are\s+now\s+(?:a|an)\s+',
    ]
]

_COUNTER_LOCK = threading.Lock()
_COUNTER_DAY = date.today().isoformat()
_COUNTER = 0


def _daily_limit() -> int:
    try:
        return max(1, min(int(os.environ.get('TAVILY_DAILY_SEARCH_LIMIT', '120')), 1000))
    except ValueError:
        return 120


def reserve_search_credit() -> None:
    global _COUNTER_DAY, _COUNTER
    today = date.today().isoformat()
    with _COUNTER_LOCK:
        if today != _COUNTER_DAY:
            _COUNTER_DAY, _COUNTER = today, 0
        if _COUNTER >= _daily_limit():
            raise RuntimeError('TAVILY_LOCAL_DAILY_LIMIT_REACHED')
        _COUNTER += 1


def _contains_sensitive(value: str) -> bool:
    return any(p.search(value or '') for p in SENSITIVE_TEXT_PATTERNS)


def validate_search_identity(title: str, platform: str) -> tuple[str, str]:
    title = str(title or '').strip()
    platform = str(platform or '').strip()
    if platform not in ALLOWED_PLATFORMS:
        raise RuntimeError('RESEARCH_PLATFORM_NOT_ALLOWED')
    if not title or len(title) > 240:
        raise RuntimeError('RESEARCH_TITLE_INVALID')
    if '\n' in title or '\r' in title or '\x00' in title:
        raise RuntimeError('RESEARCH_TITLE_INVALID')
    if re.search(r'(?i)https?://|www\.|localhost|127\.0\.0\.1', title):
        raise RuntimeError('RESEARCH_TITLE_CONTAINS_URL')
    if _contains_sensitive(title):
        raise RuntimeError('RESEARCH_TITLE_CONTAINS_SENSITIVE_DATA')
    return title, platform


def safe_public_url(value: str) -> str:
    raw = str(value or '').strip()
    if not raw or len(raw) > 2048 or _contains_sensitive(raw):
        return ''
    try:
        parsed = urlparse(raw)
    except Exception:
        return ''
    if parsed.scheme not in {'http','https'} or not parsed.hostname:
        return ''
    host = parsed.hostname.strip('.').casefold()
    if host in {'localhost','localhost.localdomain'} or host.endswith('.local'):
        return ''
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return ''
    except ValueError:
        pass
    clean_pairs = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in SENSITIVE_QUERY_KEYS:
            continue
        if _contains_sensitive(val):
            continue
        clean_pairs.append((key, val))
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(clean_pairs, doseq=True), ''))


def sanitize_untrusted_evidence(text: str, *, max_chars: int = 12000) -> tuple[str, list[str]]:
    value = str(text or '').replace('\x00', ' ').strip()
    notes: list[str] = []
    if _contains_sensitive(value):
        for pattern in SENSITIVE_TEXT_PATTERNS:
            value, n = pattern.subn('[REDACTED_SENSITIVE_VALUE]', value)
            if n:
                notes.append(f'redacted_sensitive:{n}')
    lines = []
    removed = 0
    for line in value.splitlines():
        if any(p.search(line) for p in INJECTION_PATTERNS):
            removed += 1
            continue
        lines.append(line)
    if removed:
        notes.append(f'removed_prompt_injection_lines:{removed}')
    value = '\n'.join(lines)
    if len(value) > max_chars:
        value = value[:max_chars]
        notes.append('truncated_evidence')
    return value, notes


def build_search_query(title: str, platform: str, *, broad: bool = False) -> str:
    title, platform = validate_search_identity(title, platform)
    if broad:
        return f'"{title}" short drama'
    return f'"{title}" {platform} short drama synopsis episodes'
