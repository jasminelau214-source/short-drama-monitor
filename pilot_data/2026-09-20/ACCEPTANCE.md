# Web Top10 Pilot Acceptance — 2026-09-20

- Overall state: **AWAITING_USER_CONFIRMATION_CORE_SCOPE**
- Exact Web Top10 valid (all observed platforms): **7/8**
- Core promotion scope: **7 platforms**
- Core exact Web Top10 valid: **7/7**
- Core exact Web Top10 stable (3 daily runs): **7/7**
- Non-blocking special scope: **DramaWave**
- Production write: **false**
- Core promotion requires user confirmation: **true**

| Platform | Official source | Ranking meaning | Completeness | Stability | Evidence | Fallback | Decision |
|---|---|---|---:|---:|---|---|---|
| DramaBox | https://www.dramaboxdb.com/channel/trending | Trending | 10/10 | 3/3 | official Trending page via render-singapore; HTTP 200 | independent cloud egress (jsm-dramabox-pilot-probe); still reads official Web source | CONTINUE_STABILITY_VALIDATION |
| DramaWave | https://m.mydramawave.com/ | Most Trending | 6/10 | 0/3 | explicit Most Trending labels [1, 2, 3, 4, 6, 10]; rendered cards=310; H5 explicit-rank probe: explicit=0/10; complete=NO; stable=0/3; alternate: Popular Choices 10/10; alt stable=3/3 | NONE_EQUIVALENT; H5 Popular Choices is diagnostic only, not Most Trending | CONTINUE_ALT_STABILITY_BUT_DO_NOT_PROMOTE_WITHOUT_SCOPE_CONFIRMATION |
| FlexTV | https://www.flextv.cc/drama/Top-in-FlexTV | Top in FlexTV | 10/10 | 4/3 | JSON-LD ItemList | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| GoodShort | https://www.goodshort.com/channel/Top-in-GoodShort | Top in GoodShort | 10/10 | 3/3 | server-rendered official Top in GoodShort; rows=10 | same official page via rendered-browser adapter | CONTINUE_STABILITY_VALIDATION |
| MoboReels | https://www.moboreels.com/ | Popular Series | 10/10 | 4/3 | MoboReels Popular Series DOM | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| NetShort | https://netshort.com/ | Trending Now | 10/10 | 4/3 | JSON-LD ItemList (Trending Now) | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| ReelShort | https://www.reelshort.com/shelf/top-short-movies-dramas-51001122 | TOP | 10/10 | 4/3 | Next.js __NEXT_DATA__ pageProps.list (shelf=TOP) | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| ShortMax | https://www.shorttv.live/ | Most Popular | 10/10 | 3/3 | ShortMax live DOM Most Popular section; section-scoped DOM; rendered cards=14; diagnostic alternate: Catalog sorted by playNum desc 10/10; alt stable=0/3 | NONE_EQUIVALENT; playNum-derived ordering is rejected | CONTINUE_STABILITY_VALIDATION |

## Non-blocking special-scope observations

- **DramaWave**: excluded from the current core promotion gate. Web currently verifies only explicit Top1–5; the H5 probe exposes no equivalent Most Trending rank labels, and the 10-item Popular Choices shelf remains semantically different.

No test result in this report is authorized for production ingestion.
