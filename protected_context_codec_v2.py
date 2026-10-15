"""Versioned data-only codec for protected store snapshots, never model input.

Decoding creates only explicitly listed immutable contract values. This is not
approval: the normal authority/completion validators must still run freshly.
"""
import base64
from dataclasses import fields
from datetime import date, datetime
import json
import math

from batch_authority_v2 import RunScope, TargetDefinition, BatchRow, RankingRun
from identity_evidence_v2 import ObservationIdentity, EvidenceApproval, IdentityEvidence
from research_completion_v2 import CompletionContext, FieldEvidence
from drama_identity_v2 import IdentityReviewRequired

TYPES = {c.__name__: c for c in (RunScope, TargetDefinition, BatchRow, RankingRun,
    ObservationIdentity, EvidenceApproval, IdentityEvidence, CompletionContext, FieldEvidence)}
MAX_BYTES = 8 * 1024 * 1024


def _convert(value, depth=0):
    if depth > 40: raise ValueError('CONTEXT_NESTING_LIMIT')
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float and math.isfinite(value): return value
    if type(value) is datetime:
        if value.utcoffset() is None: raise ValueError('AWARE_CONTEXT_TIME_REQUIRED')
        return {'type': 'datetime', 'value': value.isoformat()}
    if type(value) is date: return {'type': 'date', 'value': value.isoformat()}
    if type(value) is bytes: return {'type': 'bytes', 'value': base64.b64encode(value).decode('ascii')}
    if type(value) in TYPES.values():
        return {'type': type(value).__name__, 'fields': {
            f.name: _convert(getattr(value, f.name), depth+1) for f in fields(value)}}
    if type(value) in (list, tuple, set, frozenset):
        values = [_convert(x, depth+1) for x in value]
        if type(value) in (set, frozenset):
            values.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        return {'type': type(value).__name__, 'items': values}
    if type(value) is dict:
        pairs = [[_convert(k, depth+1), _convert(v, depth+1)] for k, v in value.items()]
        pairs.sort(key=lambda p: json.dumps(p[0], sort_keys=True, ensure_ascii=False))
        return {'type': 'mapping', 'items': pairs}
    raise ValueError('CONTEXT_VALUE_TYPE_REFUSED')


def _restore(value, depth=0):
    if depth > 40: raise ValueError('CONTEXT_NESTING_LIMIT')
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float and math.isfinite(value): return value
    if type(value) is not dict or type(value.get('type')) is not str:
        raise ValueError('CONTEXT_TAG_REQUIRED')
    tag = value['type']
    if tag in {'datetime', 'date', 'bytes'} and set(value) == {'type', 'value'}:
        if type(value['value']) is not str: raise ValueError('CONTEXT_SCALAR_INVALID')
        if tag == 'bytes': return base64.b64decode(value['value'], validate=True)
        restored = datetime.fromisoformat(value['value']) if tag == 'datetime' else date.fromisoformat(value['value'])
        if tag == 'datetime' and restored.utcoffset() is None: raise ValueError('AWARE_CONTEXT_TIME_REQUIRED')
        return restored
    if tag in TYPES and set(value) == {'type', 'fields'}:
        cls, data = TYPES[tag], value['fields']
        if type(data) is not dict or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('CONTEXT_FIELDS_INVALID')
        return cls(**{k: _restore(v, depth+1) for k, v in data.items()})
    if tag in {'list', 'tuple', 'set', 'frozenset', 'mapping'} and set(value) == {'type', 'items'}:
        if type(value['items']) is not list: raise ValueError('CONTEXT_ITEMS_INVALID')
        if tag == 'mapping':
            restored = {}
            for pair in value['items']:
                if type(pair) is not list or len(pair) != 2: raise ValueError('CONTEXT_MAPPING_INVALID')
                key, item = (_restore(x, depth+1) for x in pair)
                if key in restored: raise ValueError('CONTEXT_DUPLICATE_MAPPING_KEY')
                restored[key] = item
            return restored
        restored = [_restore(x, depth+1) for x in value['items']]
        if tag in {'set', 'frozenset'} and len(set(restored)) != len(restored):
            raise ValueError('CONTEXT_DUPLICATE_SET_ITEM')
        return {'list': list, 'tuple': tuple, 'set': set, 'frozenset': frozenset}[tag](restored)
    raise ValueError('CONTEXT_TAG_REFUSED')


def encode_context(context):
    if type(context) is not CompletionContext: raise IdentityReviewRequired('TYPED_COMPLETION_CONTEXT_REQUIRED')
    result = json.dumps({'version': 1, 'context': _convert(context)}, sort_keys=True,
        ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(result.encode('utf-8')) > MAX_BYTES: raise IdentityReviewRequired('CONTEXT_SIZE_LIMIT')
    return result


def decode_context(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('CONTEXT_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        if type(text) is not str or len(text.encode('utf-8')) > MAX_BYTES: raise ValueError()
        raw = json.loads(text, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(raw) is not dict or set(raw) != {'version', 'context'} or type(raw['version']) is not int or raw['version'] != 1:
            raise ValueError()
        result = _restore(raw['context'])
        if type(result) is not CompletionContext: raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise IdentityReviewRequired('PROTECTED_CONTEXT_SNAPSHOT_INVALID') from None
