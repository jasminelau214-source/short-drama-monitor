# Integration-only ShortMax SSR vs rendered-browser route audit.
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from collector_import import validate_and_normalize
from official_web_collectors import collect_shortmax, fetch_html_with_evidence


URL = 'https://www.shorttv.live/'

SHORTMAX_JS = r"""
() => {
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"]'));
  const heading = headings.find(el => clean(el.textContent).toLowerCase().startsWith('most popular'));
  if (!heading) return {heading:'', rows:[]};
  const section = heading.closest('section') || heading.parentElement?.parentElement;
  if (!section) return {heading:clean(heading.textContent), rows:[]};
  const cards = Array.from(section.querySelectorAll('.drama-card, .card-item'));
  const rows = [];
  const seen = new Set();
  for (const card of cards) {
    const titleEl = card.querySelector('.card-title, .overlay-title, [class*="card-title"]');
    const linkEl = card.querySelector(
      'a.card-title-layout, a.card-text, a.overlay-title, a[href*="/drama/"]'
    );
    const title = clean(titleEl && titleEl.textContent);
    const href = linkEl && linkEl.href ? String(linkEl.href) : '';
    const key = title.toLowerCase().replace(/[^a-z0-9]+/g, '');
    if (!title || !key || seen.has(key)) continue;
    seen.add(key);
    rows.push({rank: rows.length + 1, title, source_url: href});
  }
  return {heading: clean(heading.textContent), rows};
}
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument('--output', default='')
    args = parser.parse_args(argv)

    result = {
        'platform': 'ShortMax',
        'phase': 'INTEGRATION_ROUTE_AUDIT',
        'productionWrite': False,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'collectionDate': args.date,
        'requestedTarget': 'Most Popular Top10',
    }

    try:
        fetched = fetch_html_with_evidence(URL)
        ssr_doc = str(fetched['document'])
        ssr_payload = collect_shortmax(
            url=URL,
            section='Most Popular',
            collection_date=args.date,
            top_n=None,
            document=ssr_doc,
        )
        result['ssr'] = {
            'httpStatus': fetched['evidence'].get('httpStatus'),
            'pageUrl': fetched['evidence'].get('pageUrl'),
            'rowCount': len(ssr_payload.get('rows') or []),
            'rankingType': ssr_payload.get('ranking_type'),
            'canSatisfyTop10': len(ssr_payload.get('rows') or []) >= 10,
            'titles': [x.get('title') for x in (ssr_payload.get('rows') or [])],
        }
    except Exception as exc:
        result['ssr'] = {
            'error': f'{type(exc).__name__}: {exc}',
            'canSatisfyTop10': False,
        }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={'width': 390, 'height': 844},
                locale='en-US',
                user_agent=(
                    'Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36'
                ),
                is_mobile=True,
                has_touch=True,
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            response = page.goto(URL, wait_until='domcontentloaded', timeout=90000)
            page.wait_for_timeout(3500)
            for _ in range(4):
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(300)
            page.evaluate('window.scrollTo(0, 0)')
            page.wait_for_timeout(500)

            probe = page.evaluate(SHORTMAX_JS) or {}
            rows = list(probe.get('rows') or [])
            heading = str(probe.get('heading') or '')
            http_status = int(response.status) if response is not None else 0
            final_url = str(page.url or '')
            fetched_at = datetime.now(timezone.utc).isoformat()

            browser_payload = {
                'platform': 'ShortMax',
                'source_type': 'OFFICIAL_WEB',
                'source_id': 'officialweb_shortmax',
                'target_key': 'web_most_popular_all',
                'ranking_type': heading,
                'category': 'All',
                'collection_method': 'WEB_SCRAPE',
                'collection_date': args.date,
                'top_n': 10,
                'batch_complete': len(rows) >= 10,
                'rows': rows[:10],
                'collector_version': 'shortmax-browser-route-audit-v1',
                'collected_at': fetched_at,
                'locale': 'en-US',
                'evidence': {
                    'url': URL,
                    'requestedUrl': URL,
                    'httpStatus': http_status,
                    'pageUrl': final_url,
                    'fetchedAt': fetched_at,
                    'section': heading,
                    'row_count': min(len(rows), 10),
                    'renderedCardCount': len(rows),
                    'semanticVerified': heading.lower().startswith('most popular'),
                    'route': 'playwright-rendered-dom',
                },
                'evidence_persistence': 'URL_AND_PARSED_FACTS',
                'provider': 'official-web-playwright-audit',
            }

            import_error = ''
            normalized = None
            if len(rows) >= 10:
                try:
                    normalized = validate_and_normalize(browser_payload, set())
                except Exception as exc:
                    import_error = f'{type(exc).__name__}: {exc}'

            result['browser'] = {
                'httpStatus': http_status,
                'pageUrl': final_url,
                'heading': heading,
                'renderedCardCount': len(rows),
                'canSatisfyTop10': len(rows) >= 10,
                'top10Titles': [x.get('title') for x in rows[:10]],
                'importBoundaryPass': bool(normalized and normalized.get('rowCount') == 10),
                'importError': import_error,
            }
        finally:
            browser.close()

    ssr_ok = bool((result.get('ssr') or {}).get('canSatisfyTop10'))
    browser_ok = bool((result.get('browser') or {}).get('canSatisfyTop10'))
    browser_import = bool((result.get('browser') or {}).get('importBoundaryPass'))

    if ssr_ok:
        result['status'] = 'SSR_TOP10_AVAILABLE'
    elif browser_ok and browser_import:
        result['status'] = 'SSR_TOP10_BLOCKED_BROWSER_TOP10_PASS'
    elif browser_ok:
        result['status'] = 'BROWSER_TOP10_IMPORT_BLOCKED'
    else:
        result['status'] = 'TOP10_CURRENTLY_UNAVAILABLE'

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
