from __future__ import annotations

AUDIENCE_VALUES = ('女频', '男频', '泛受众')
GENRE_VALUES = (
    '现代都市', '校园青春', '犯罪黑帮', '科幻', '奇幻超自然', '西幻',
    '悬疑惊悚', '历史古装', '动作冒险', '家庭伦理', '其他',
)

_AUDIENCE_MAP = {
    'female': '女频', 'women': '女频', 'woman': '女频', '女频': '女频',
    'male': '男频', 'men': '男频', 'man': '男频', '男频': '男频',
    'general': '泛受众', 'general audience': '泛受众', 'mixed': '泛受众',
    'both': '泛受众', '泛受众': '泛受众',
}

_GENRE_MAP = {
    'modern': '现代都市', 'modern urban': '现代都市', 'urban romance': '现代都市',
    'campus': '校园青春', 'young adult': '校园青春', 'school': '校园青春',
    'mafia': '犯罪黑帮', 'crime': '犯罪黑帮', 'crime/mafia': '犯罪黑帮',
    'sci-fi': '科幻', 'science fiction': '科幻',
    'fantasy': '奇幻超自然', 'urban fantasy': '奇幻超自然', 'superhero': '奇幻超自然',
    'high fantasy': '西幻', 'western fantasy': '西幻', 'werewolf': '西幻',
    'thriller': '悬疑惊悚', 'mystery': '悬疑惊悚', 'tragedy': '悬疑惊悚',
    'historical': '历史古装', 'historical fantasy / romance': '历史古装',
    'action': '动作冒险', 'martial arts': '动作冒险',
    'family': '家庭伦理', 'family drama': '家庭伦理',
}


def normalize_audience(value: object) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    if raw in AUDIENCE_VALUES:
        return raw
    return _AUDIENCE_MAP.get(raw.casefold(), '')


def normalize_genre(value: object) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    if raw in GENRE_VALUES:
        return raw
    return _GENRE_MAP.get(raw.casefold(), '')
