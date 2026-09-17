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
        '<button data-view-button="lifecycle">历史生命周期</button>',
        '<button data-view-button="lifecycle">历史生命周期</button>\n        <button data-view-button="webCollection">Web采集分析</button>',
        'web collection nav',
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
    html = replace_once(
        html,
        '<div class="metric"><div class="label">记录采集日</div><div class="value" id="metricDates">0</div><div class="subtle">不补编历史</div></div>',
        '<div class="metric"><div class="label">App榜单采集日</div><div class="value" id="metricDates">0</div><div class="subtle">已发布日榜日期数</div></div>',
        'dashboard date metric',
    )
    html = replace_once(
        html,
        '<button class="metric new-title" type="button" data-status-filter="new"><div class="label">今日新剧</div>',
        '<button class="metric new-title" type="button" data-status-filter="new"><div class="label">当日新剧</div>',
        'new title metric label',
    )
    html = replace_once(
        html,
        '已纳入真实采集日：2026-09-04、2026-09-07。趋势和生命周期只使用截图采集记录。',
        '已纳入 App 榜单采集日：正在读取。趋势和生命周期只使用已发布 App 榜单事实记录。',
        'sidebar data scope copy',
    )
    html = replace_once(
        html,
        '<div class="subtle">只展示真实截图采集日，不补造中间日期或排名。</div>',
        '<div class="subtle">只展示已发布 App 榜单采集日，不补造中间日期或排名。</div>',
        'lifecycle scope copy',
    )
    html = replace_once(
        html,
        '<div class="date-pill">2个真实采集日</div>',
        '<div class="date-pill" id="lifecycleDateCount">App榜单采集日 —</div>',
        'lifecycle date count',
    )
    html = replace_once(
        html,
        '<span>热度与排名来自当日截图；空白字段保持为空。</span>',
        '<span>热度与排名来自当日已发布 App 榜单采集；空白字段保持为空。</span>',
        'catalog source copy',
    )
    html = replace_once(
        html,
        "      $('#resultCount').textContent = `显示 ${filtered.length} / ${dayItems.length} 部${statusLabel}`;",
        "      $('#resultCount').textContent = `显示 ${filtered.length} / ${dayItems.length} 条榜单记录${statusLabel}`;",
        'catalog row count copy',
    )

    stats_end = '''          <button class="metric continuing-title" type="button" data-status-filter="continuing"><div class="label">继续在榜</div><div class="value" id="metricContinuing">0</div><div class="subtle">点击查看此前已出现剧目</div></button>\n        </div>\n\n        <div class="grid-2">'''
    overview = '''          <button class="metric continuing-title" type="button" data-status-filter="continuing"><div class="label">继续在榜</div><div class="value" id="metricContinuing">0</div><div class="subtle">点击查看此前已出现剧目</div></button>\n        </div>\n\n        <section class="section" id="collectionOverview" style="margin-top:14px;">\n          <div class="section-head">\n            <div>\n              <h2>全网采集概览</h2>\n              <div class="subtle">独立于上方 App 榜单口径，展示最近一次 Official Web 采集覆盖；采集记录为各目标抽取行数之和，不等于去重剧目数。</div>\n            </div>\n            <button class="tool-button" type="button" data-view-button="webCollection">查看采集明细</button>\n          </div>\n          <div class="detail-summary">\n            <div class="mini-metric"><span>Web数据日期</span><span id="collectionOverviewDate">—</span></div>\n            <div class="mini-metric"><span>Web来源平台</span><span id="collectionOverviewPlatforms">—</span></div>\n            <div class="mini-metric"><span>采集目标</span><span id="collectionOverviewTargets">—</span></div>\n            <div class="mini-metric"><span>采集记录（行）</span><span id="collectionOverviewRows">—</span></div>\n          </div>\n          <div class="subtle" id="collectionOverviewStatus" style="margin-top:10px;">等待采集监控数据。</div>\n        </section>\n\n        <div class="grid-2">'''
    html = replace_once(html, stats_end, overview, 'collection overview section')

    web_view_anchor = '''      </section>\n    </main>'''
    web_view = '''      </section>\n\n      <section id="webCollection" class="view" aria-labelledby="web-collection-title">\n        <div class="topbar">\n          <div>\n            <h1 id="web-collection-title">Official Web采集分析</h1>\n            <div class="subtle">与 App 日榜分层展示。采集通过的数据进入内容分析队列；部分采集与待复核数据保留展示，但不作为 App 榜单事实。</div>\n          </div>\n          <div class="date-pill" id="webCollectionDate">采集日 —</div>\n        </div>\n\n        <div class="stats-grid">\n          <div class="metric"><div class="label">采集记录</div><div class="value" id="webRows">0</div><div class="subtle">目标抽取行数</div></div>\n          <div class="metric"><div class="label">去重剧目</div><div class="value" id="webTitles">0</div><div class="subtle">跨目标按剧名去重</div></div>\n          <div class="metric"><div class="label">来源平台</div><div class="value" id="webPlatforms">0</div><div class="subtle">Official Web</div></div>\n          <div class="metric"><div class="label">采集目标</div><div class="value" id="webTargets">0</div><div class="subtle">榜单/分类目标</div></div>\n          <div class="metric new-title"><div class="label">进入分析队列</div><div class="value" id="webAnalysisRows">0</div><div class="subtle">采集通过</div></div>\n          <div class="metric continuing-title"><div class="label">待质量处理</div><div class="value" id="webQualityRows">0</div><div class="subtle">部分采集 / 待复核</div></div>\n        </div>\n\n        <div class="filters" style="grid-template-columns:minmax(160px,1fr) minmax(160px,1fr) minmax(220px,1.4fr);">\n          <div class="field"><label for="webPlatformFilter">平台</label><select id="webPlatformFilter"><option value="">全部</option></select></div>\n          <div class="field"><label for="webStatusFilter">处理状态</label><select id="webStatusFilter"><option value="">全部</option></select></div>\n          <div class="field"><label for="webSearchInput">剧名搜索</label><input id="webSearchInput" type="search" placeholder="输入剧名"></div>\n        </div>\n        <div class="table-meta"><span id="webResultCount"></span><span>采集日期固定使用后台 collection_date；本批次应显示 2026-09-16。</span></div>\n        <div class="table-wrap">\n          <table style="min-width:1180px;">\n            <thead><tr><th>采集日</th><th>平台</th><th>采集目标</th><th>排名</th><th>剧名</th><th>质量状态</th><th>分析状态</th><th>标签/数据</th></tr></thead>\n            <tbody id="webCollectionBody"></tbody>\n          </table>\n        </div>\n        <div id="webEmptyState" class="empty">暂无 Official Web 采集数据。</div>\n      </section>\n    </main>'''
    html = replace_once(html, web_view_anchor, web_view, 'web collection view')

    render_anchor = '''      $('#metricContinuing').textContent = formatNumber.format(day.continuingTitles);'''
    render_patch = '''      $('#metricContinuing').textContent = formatNumber.format(day.continuingTitles);\n      const platformNames = day.platforms.map(item => item.name);\n      const scopeText = `${day.platforms.length}个平台 · ${day.totalRows}个榜位`;\n      if ($('#metricScope')) $('#metricScope').textContent = scopeText;\n      if ($('#dashboardScope')) {\n        const batchRule = '同日按平台取最新完整批次';\n        $('#dashboardScope').textContent = `App榜单快照 · ${activeDate} · ${scopeText}${platformNames.length ? ` · ${platformNames.join('、')}` : ''} · ${batchRule}`;\n      }\n      const sidebarNote = document.querySelector('.side-note');\n      if (sidebarNote) {\n        const web = summary.webCollection || {};\n        const webText = web.collectionDate ? `<br>Official Web：${web.collectionDate} · ${web.rowCount || 0}条采集记录。` : '';\n        sidebarNote.innerHTML = `<strong>数据范围</strong><br>已纳入 App 榜单采集日：${summary.dates.join('、')}。趋势和生命周期只使用已发布 App 榜单事实记录。${webText}`;\n      }\n      if ($('#lifecycleDateCount')) $('#lifecycleDateCount').textContent = `App榜单采集日 ${summary.dateCount} 个`;\n\n      const monitoring = summary.monitoring || {};\n      const coverage = monitoring.coverage || null;\n      const statusCounts = monitoring.statusCounts || {};\n      const overview = $('#collectionOverview');\n      if (overview && monitoring.available && coverage) {\n        overview.style.display = '';\n        const targetTotal = Number(coverage.target_jobs ?? statusCounts.total ?? 0);\n        const targetSucceeded = Number(coverage.succeeded_targets ?? statusCounts.succeeded ?? 0);\n        const targetPartial = Number(coverage.partial_targets ?? statusCounts.partial ?? 0);\n        const targetReview = Number(coverage.review_targets ?? statusCounts.needsReview ?? 0);\n        $('#collectionOverviewDate').textContent = monitoring.collectionDate || coverage.collection_date || '—';\n        $('#collectionOverviewPlatforms').textContent = formatNumber.format(Number(coverage.web_platforms_with_jobs || 0));\n        $('#collectionOverviewTargets').textContent = formatNumber.format(targetTotal);\n        $('#collectionOverviewRows').textContent = formatNumber.format(Number(coverage.extracted_rows || 0));\n        $('#collectionOverviewStatus').textContent = `目标状态：成功 ${targetSucceeded} · 部分成功 ${targetPartial} · 待复核 ${targetReview}。Web采集与 App 榜单分开统计，不互相替代。`;\n      } else if (overview) {\n        $('#collectionOverviewStatus').textContent = '暂未读取到全网采集监控数据；上方 App 榜单数据不受影响。';\n      }'''
    html = replace_once(html, render_anchor, render_patch, 'dashboard monitoring renderer')

    web_js_anchor = '''    function renderLifecycle() {'''
    web_js = '''    function webMetricText(metrics) {\n      if (!metrics || typeof metrics !== 'object') return '';\n      return Object.entries(metrics).filter(([, value]) => value !== '' && value !== null && value !== undefined).map(([key, value]) => `${key}: ${value}`).join(' · ');\n    }\n\n    function setupWebCollectionFilters() {\n      const web = summary.webCollection || { rows: [], platforms: [] };\n      const platformSelect = $('#webPlatformFilter');\n      const statusSelect = $('#webStatusFilter');\n      if (!platformSelect || !statusSelect) return;\n      optionList(platformSelect, web.platforms || []);\n      const statuses = uniq((web.rows || []).map(row => row.analysisStatus));\n      optionList(statusSelect, statuses);\n      ['webPlatformFilter', 'webStatusFilter', 'webSearchInput'].forEach(id => {\n        const el = $('#' + id);\n        if (el) el.addEventListener('input', renderWebCollection);\n      });\n    }\n\n    function renderWebCollection() {\n      const web = summary.webCollection || { rows: [], platforms: [], qualityCounts: {}, analysisCounts: {} };\n      const allRows = web.rows || [];\n      const passedRows = allRows.filter(row => row.jobStatus === 'SUCCEEDED').length;\n      const qualityRows = allRows.length - passedRows;\n      if ($('#webCollectionDate')) $('#webCollectionDate').textContent = web.collectionDate ? `采集日 ${web.collectionDate}` : '采集日 —';\n      if ($('#webRows')) $('#webRows').textContent = formatNumber.format(web.rowCount || allRows.length || 0);\n      if ($('#webTitles')) $('#webTitles').textContent = formatNumber.format(web.uniqueTitles || 0);\n      if ($('#webPlatforms')) $('#webPlatforms').textContent = formatNumber.format(web.platformCount || 0);\n      if ($('#webTargets')) $('#webTargets').textContent = formatNumber.format(web.targetCount || 0);\n      if ($('#webAnalysisRows')) $('#webAnalysisRows').textContent = formatNumber.format(passedRows);\n      if ($('#webQualityRows')) $('#webQualityRows').textContent = formatNumber.format(qualityRows);\n\n      const platform = $('#webPlatformFilter')?.value || '';\n      const status = $('#webStatusFilter')?.value || '';\n      const query = ($('#webSearchInput')?.value || '').trim().toLowerCase();\n      const rows = allRows.filter(row => !platform || row.platform === platform)\n        .filter(row => !status || row.analysisStatus === status)\n        .filter(row => !query || String(row.title || '').toLowerCase().includes(query));\n      if ($('#webResultCount')) $('#webResultCount').textContent = `显示 ${rows.length} / ${allRows.length} 条采集记录`;\n      if ($('#webEmptyState')) $('#webEmptyState').style.display = rows.length ? 'none' : 'block';\n      if (!$('#webCollectionBody')) return;\n      $('#webCollectionBody').innerHTML = rows.map(row => {\n        const extra = [row.tags, webMetricText(row.metrics)].filter(Boolean).join(' · ');\n        const qualityClass = row.jobStatus === 'SUCCEEDED' ? 'blue' : row.jobStatus === 'NEEDS_REVIEW' ? 'rose' : 'amber';\n        const analysisClass = row.analysisStatus === '内容分析完成' ? 'blue' : row.analysisStatus.includes('复核') || row.analysisStatus.includes('失败') ? 'rose' : 'amber';\n        return `<tr>\n          <td class="nowrap">${escapeHtml(row.collectionDate || web.collectionDate || '')}</td>\n          <td><span class="chip neutral">${escapeHtml(row.platform || '')}</span></td>\n          <td><div>${escapeHtml(row.rankingType || row.targetKey || '')}</div><div class="subtle">${escapeHtml(row.category || '')} · ${escapeHtml(row.targetKey || '')}</div></td>\n          <td><span class="rank">${escapeHtml(row.rank)}</span></td>\n          <td><strong>${escapeHtml(row.title || '')}</strong></td>\n          <td><span class="chip ${qualityClass}">${escapeHtml(row.qualityStatus || '')}</span></td>\n          <td><span class="chip ${analysisClass}">${escapeHtml(row.analysisStatus || '')}</span></td>\n          <td>${escapeHtml(extra || '—')}</td>\n        </tr>`;\n      }).join('');\n    }\n\n    function renderLifecycle() {'''
    html = replace_once(html, web_js_anchor, web_js, 'web collection renderer')

    catalog_anchor = '''      $('#catalogDatePill').textContent = '采集日 ' + activeDate;'''
    catalog_patch = '''      $('#catalogDatePill').textContent = '采集日 ' + activeDate;\n      $('#catalog-title').textContent = `${activeDate} App榜单`;'''
    html = replace_once(html, catalog_anchor, catalog_patch, 'catalog dynamic title')

    init_anchor = '''      setupFilters();\n      renderDashboard();\n      renderCatalog();\n      renderLifecycle();'''
    init_patch = '''      setupFilters();\n      setupWebCollectionFilters();\n      renderDashboard();\n      renderCatalog();\n      renderLifecycle();\n      renderWebCollection();'''
    html = replace_once(html, init_anchor, init_patch, 'web collection initialize')

    INDEX_PATH.write_text(html, encoding='utf-8')
    print('[runtime-ui-patch] applied dashboard scope + Official Web analysis view')


if __name__ == '__main__':
    main()
