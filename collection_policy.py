from __future__ import annotations

from collections.abc import Mapping


POLICY_VERSION = 'core-contract-v1-2026-09-23'

ACTIVE = 'ACTIVE'
PAUSED = 'PAUSED'

COLLECTION_PROFILE = {
    'language': 'English',
    'locale': 'en-US',
    'region': 'US',
    'regionRule': 'US_WHEN_FILTER_AVAILABLE',
}

# This is the only place that decides which official-web platforms belong to
# the default collection plan. Collector implementations may remain available
# while their platform is paused.
PLATFORM_POLICY = {
    'DramaBox': {
        'state': ACTIVE,
        'targetName': 'dramabox_trending',
        'targetKey': 'web_trending_top10',
        'rankingType': 'Trending',
        'topN': 10,
    },
    'FlexTV': {
        'state': ACTIVE,
        'targetName': 'flextv_top',
        'targetKey': 'web_top_in_flextv_all',
        'rankingType': 'Top in FlexTV',
        'topN': 10,
    },
    'GoodShort': {
        'state': ACTIVE,
        'targetName': 'goodshort_top',
        'targetKey': 'web_top_goodshort_pilot',
        'rankingType': 'Top in GoodShort',
        'topN': 10,
    },
    'MoboReels': {
        'state': ACTIVE,
        'targetName': 'moboreels_popular',
        'targetKey': 'web_popular_series_all',
        'rankingType': 'Popular Series',
        'topN': 10,
    },
    'NetShort': {
        'state': ACTIVE,
        'targetName': 'netshort_trending',
        'targetKey': 'web_trending_now_all',
        'rankingType': 'Trending Now',
        'topN': 10,
    },
    'ReelShort': {
        'state': ACTIVE,
        'targetName': 'reelshort_top',
        'targetKey': 'web_top_shelf_all',
        'rankingType': 'TOP',
        'topN': 10,
    },
    'DramaWave': {
        'state': PAUSED,
        'reasonCode': 'PRIMARY_RANKING_NOT_STABLY_VERIFIED',
        'resumeGate': (
            'Verify one official English/US primary ranking, its exact capacity, '
            'and stable evidence before Integration Gate and user approval.'
        ),
    },
    'ShortMax': {
        'state': PAUSED,
        'reasonCode': 'PRIMARY_RANKING_CONTRACT_UNRESOLVED',
        'resumeGate': (
            'Lock Most Popular versus Homepage Hero semantics and capacity before '
            'Integration Gate and user approval.'
        ),
    },
}


def active_platforms() -> tuple[str, ...]:
    return tuple(
        platform
        for platform, policy in PLATFORM_POLICY.items()
        if policy['state'] == ACTIVE
    )


def paused_platforms() -> tuple[str, ...]:
    return tuple(
        platform
        for platform, policy in PLATFORM_POLICY.items()
        if policy['state'] == PAUSED
    )


def platform_policy(platform: str) -> Mapping[str, object]:
    try:
        return PLATFORM_POLICY[platform]
    except KeyError as exc:
        raise ValueError(f'UNDECLARED_PLATFORM_POLICY:{platform}') from exc


def validate_active_target_catalog(targets: Mapping[str, Mapping[str, object]]) -> None:
    expected_names = {
        str(policy['targetName'])
        for policy in PLATFORM_POLICY.values()
        if policy['state'] == ACTIVE
    }
    actual_names = set(targets)
    if actual_names != expected_names:
        raise ValueError(
            'ACTIVE_TARGET_SCOPE_MISMATCH:'
            f'expected={sorted(expected_names)} actual={sorted(actual_names)}'
        )

    for platform in active_platforms():
        policy = PLATFORM_POLICY[platform]
        name = str(policy['targetName'])
        target = targets[name]
        expected = {
            'platform': platform,
            'target_key': policy['targetKey'],
            'ranking_type': policy['rankingType'],
            'top_n': policy['topN'],
        }
        actual = {key: target.get(key) for key in expected}
        if actual != expected:
            raise ValueError(
                f'ACTIVE_TARGET_CONTRACT_MISMATCH:{name}:'
                f'expected={expected} actual={actual}'
            )


def collection_scope_manifest() -> dict:
    return {
        'policyVersion': POLICY_VERSION,
        'profile': dict(COLLECTION_PROFILE),
        'activePlatforms': list(active_platforms()),
        'pausedPlatforms': [
            {
                'platform': platform,
                'status': PAUSED,
                'reasonCode': PLATFORM_POLICY[platform]['reasonCode'],
                'resumeGate': PLATFORM_POLICY[platform]['resumeGate'],
            }
            for platform in paused_platforms()
        ],
    }
