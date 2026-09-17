from pathlib import Path


INDEX_PATH = Path(__file__).resolve().parent / 'index.html'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f'UI patch anchor not found: {label}')
    return text.replace(old, new, 1)


def main() -> None:
    html = INDEX_PATH.read_text(encoding='utf-8')

    html = replace_once(
        html,
        '<button data-view-button="catalog">今日40部榜单</button>',
        '<button data-view-button="catalog">当日榜单</button>',
        'catalog nav label',
    )
    html = replace_once(
        html,
        '<h1 id="catalog-title">今日40部榜单</h1>',
        '<h1 id="catalog-title">当日榜单</h1>',
        'catalog title',
    )
    html = replace_once(
        html,
        '<div class="subtle">NetShort、DramaWave、MoboReels、ReelShort 热播榜 Top10。</div>',
        '<div class="subtle" id="dashboardScope">按实际完成的 App 榜单采集范围展示。</div>',
        'dashboard scope copy',
    )
    html = replace_once(
        html,
        '<div class="metric"><div class="label">今日榜位</div><div class="value" id="metricRows">0</div><div class="subtle">4个平台 Top10</div></div>',
        '<div class="metric"><div class="label">当日榜位</div><div class="value" id="metricRows">0</div><div class="subtle" id="metricScope">按实际采集范围</div></div>',
        'dashboard rows metric',
    )
    html = replace_once(
        html,
        '<div class="metric"><div class="label">监测平台</div><div class="value" id="metricApps">0</div><div class="subtle">本次完整覆盖</div></div>',
        '<div class="metric"><div class="label">App榜单平台</div><div class="value" id="metricApps">0</div><div class="subtle">当日实际有榜单数据</div></div>',
        'dashboard platform metric',
    )

    stats_end = '''          <button class="metric continuing-title" type="button" data-status-filter="continuing"><div class="label">继续在榜</div><div class="value" id="metricContinuing">0</div><div class="subtle">点击查看此前已出现剧目</div></button>\n        </div>\n\n        <div class="grid-2">'''
    overview = '''          <button class="metric continuing-title" type="button" data-status-filter="continuing"><div class="label">继续在榜</div><div class="value" id="metricContinuing">0</div><div class="subtle">点击查看此前已出现剧目</div></button>\n        </div>\n\n        <section class="section" id="collectionOverview" style="margin-top:14px;">\n          <div class="section-head">\n            <div>\n              <h2>全网采集概览</h2>\n              <div class="subtle">独立于上方 App 榜单口径，展示最近一次全网采集运行结果。</div>\n            </div>\n          </div>\n          <div class="detail-summary">\n            <div class="mini-metric"><span>数据日期</span><span id="collectionOverviewDate">—</span></div>\n            <div class="mini-metric"><span>Web平台</span><span id="collectionOverviewPlatforms">—</span></div>\n            <div class="mini-metric"><span>采集目标</span><span id="collectionOverviewTargets">—</span></div>\n            <div class="mini-metric"><span>采集记录</span><span id="collectionOverviewRows">—</span></div>\n          </div>\n          <div class="subtle" id="collectionOverviewStatus" style="margin-top:10px;">等待采集监控数据。</div>\n        </section>\n\n        <div class="grid-2">'''
    html = replace_once(html, stats_end, overview, 'collection overview section')

    render_anchor = '''      $('#metricContinuing').textContent = formatNumber.format(day.continuingTitles);'''
    render_patch = '''      $('#metricContinuing').textContent = formatNumber.format(day.continuingTitles);\n      const platformNames = day.platforms.map(item => item.name);\n      const scopeText = `${day.platforms.length}个平台 · ${day.totalRows}个榜位`;\n      if ($('#metricScope')) $('#metricScope').textContent = scopeText;\n      if ($('#dashboardScope')) {\n        $('#dashboardScope').textContent = `App榜单快照 · ${activeDate} · ${scopeText}${platformNames.length ? ` · ${platformNames.join('、')}` : ''}`;\n      }\n\n      const monitoring = summary.monitoring || {};\n      const coverage = monitoring.coverage || null;\n      const statusCounts = monitoring.statusCounts || {};\n      const overview = $('#collectionOverview');\n      if (overview && monitoring.available && coverage) {\n        overview.style.display = '';\n        $('#collectionOverviewDate').textContent = monitoring.collectionDate || coverage.collection_date || '—';\n        $('#collectionOverviewPlatforms').textContent = formatNumber.format(Number(coverage.web_platforms_with_jobs || 0));\n        $('#collectionOverviewTargets').textContent = formatNumber.format(Number(coverage.target_jobs || statusCounts.total || 0));\n        $('#collectionOverviewRows').textContent = formatNumber.format(Number(coverage.extracted_rows || 0));\n        $('#collectionOverviewStatus').textContent = `成功 ${statusCounts.succeeded || 0} · 部分成功 ${statusCounts.partial || 0} · 待复核 ${statusCounts.needsReview || 0}。Web采集与App榜单分开统计，不互相替代。`;\n      } else if (overview) {\n        $('#collectionOverviewStatus').textContent = '暂未读取到全网采集监控数据；上方 App 榜单数据不受影响。';\n      }'''
    html = replace_once(html, render_anchor, render_patch, 'dashboard monitoring renderer')

    catalog_anchor = '''      $('#catalogDatePill').textContent = '采集日 ' + activeDate;'''
    catalog_patch = '''      $('#catalogDatePill').textContent = '采集日 ' + activeDate;\n      $('#catalog-title').textContent = `${activeDate} App榜单`;'''
    html = replace_once(html, catalog_anchor, catalog_patch, 'catalog dynamic title')

    INDEX_PATH.write_text(html, encoding='utf-8')
    print('[runtime-ui-patch] applied dashboard scope + web collection overview')


if __name__ == '__main__':
    main()
