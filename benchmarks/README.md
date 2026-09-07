# Bio-link extraction benchmark

Testing solutions for "given an IG/YT profile, recover links to the other
platforms" (the new non-Wikipedia approach). Same ~100 YouTube + ~100 Instagram
account links used for every solution.

**KPI = "Useful %"**: share of rows where we recovered a link to at least one
OTHER target platform (IG/FB/YT/TikTok/X) **or** an aggregator (Linktree etc.)
we could follow. That is what actually fills other-platform cells.

## Head-to-head

### YouTube (anchor = YouTube)
| Metric | Sol 1 — Internal scraper | Apify (youtube-channel-contacts) |
|---|---|---|
| Extraction success | **100%** | 91.6% |
| **Useful %** | **82%** | 72% |
| Avg other platforms/row | 2.39 | 2.12 |

→ **Internal scraper wins on YouTube — and it's free.** No need for paid Apify here.

### Instagram (anchor = Instagram) — the hard case
| Metric | Sol 1 — Internal scraper | Apify (instagram-user-info-cookieless) |
|---|---|---|
| Extraction success | 36% (64 PARSER_ERROR) | **96%** |
| Rows w/ another target platform | 3 | 20 |
| Rows w/ aggregator (Linktree…) | 9 | 16 |
| **Useful %** | 11% | **33%** |

→ **Apify wins decisively on Instagram extraction reliability (36% → 96%).**
But note the ceiling: even with a perfect read, IG bios rarely list FB/TikTok/X
directly (found: YT 19, X 2, FB 0, TikTok 0). The recoverable value is mostly a
**Linktree/aggregator link (16 rows)** — which must then be **followed** to get
the actual handles. Direct other-platform handles from IG are inherently sparse.

## Conclusions so far
1. **Split the readers:** YouTube → internal scraper (better + free); Instagram → Apify cookieless actor (only reliable reader).
2. **A Linktree/aggregator follower is mandatory** — it's the single biggest lever left on the Instagram side, regardless of reader.
3. Apify is paid, so scope it to **Instagram only** to control cost.

## Final direction
Benchmark closed as a 2-way. **Split the readers: YouTube → internal scraper
(free, better); Instagram → Apify cookieless actor; then follow Linktree; then
SerpApi fallback.** Apify scoped to Instagram only for cost control.

## Files
- `solution1_internal_scraper.json`
- `solution_apify_actors.json`
- ~~modified social-data-fetcher skill~~ — dropped (skill is post-oriented, not built for profile-bio extraction)

_(Row counts: internal = 100/100; Apify returned 107 YT / 101 IG — a few extra/duplicate rows; percentages normalize.)_
