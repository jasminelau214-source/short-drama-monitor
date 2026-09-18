# Web Top10 Pilot Acceptance — 2026-09-18

- Overall state: **PILOT_RUNNING_SCOPE_DECISION_REQUIRED**
- Exact Web Top10 valid: **7/8**
- Exact Web Top10 stable (3 daily runs): **0/8**
- Production write: **false**
- Core promotion requires user confirmation: **true**

| Platform | Official source | Ranking meaning | Completeness | Stability | Evidence | Fallback | Decision |
|---|---|---|---:|---:|---|---|---|
| DramaBox | https://www.dramaboxdb.com/channel/trending | Trending | 10/10 | 1/3 | official Trending page via render-singapore; HTTP 200 | independent cloud egress (jsm-dramabox-pilot-probe); still reads official Web source | CONTINUE_STABILITY_VALIDATION |
| DramaWave | https://m.mydramawave.com/ | Most Trending | 5/10 | 0/3 | explicit Most Trending labels [1, 2, 3, 4, 5]; rendered cards=310; H5 explicit-rank probe: explicit=0/10; complete=NO; stable=0/3; alternate: Popular Choices 10/10; alt stable=1/3 | NONE_EQUIVALENT; H5 Popular Choices is diagnostic only, not Most Trending | EVALUATE_SPECIAL_TOP5_SCOPE_OR_OTHER_OFFICIAL_RANKING_ENTRY |
| FlexTV | https://www.flextv.cc/drama/Top-in-FlexTV | Top in FlexTV | 10/10 | 2/3 | JSON-LD ItemList | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| GoodShort | https://www.goodshort.com/channel/Top-in-GoodShort | Top in GoodShort | 10/10 | 1/3 | server-rendered official Top in GoodShort; rows=10 | same official page via rendered-browser adapter | CONTINUE_STABILITY_VALIDATION |
| MoboReels | https://www.moboreels.com/ | Popular Series | 10/10 | 2/3 | MoboReels Popular Series DOM | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| NetShort | https://netshort.com/ | Trending Now | 10/10 | 2/3 | JSON-LD ItemList (Trending Now) | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| ReelShort | https://www.reelshort.com/shelf/top-short-movies-dramas-51001122 | TOP | 10/10 | 2/3 | Next.js __NEXT_DATA__ pageProps.list (shelf=TOP) | same official Web page via alternate browser profile; no cross-source substitution | CONTINUE_STABILITY_VALIDATION |
| ShortMax | https://www.shorttv.live/ | Most Popular | 10/10 | 1/3 | ShortMax live DOM Most Popular section; section-scoped DOM; rendered cards=16; diagnostic alternate: Catalog sorted by playNum desc 10/10; alt stable=0/3 | NONE_EQUIVALENT; playNum-derived ordering is rejected | CONTINUE_STABILITY_VALIDATION |

## Scope decisions still required before promotion

- **DramaWave**: Web currently verifies only explicit Top1–5; the H5 probe exposes no equivalent Most Trending rank labels, and the 10-item Popular Choices shelf remains semantically different.

No test result in this report is authorized for production ingestion.
