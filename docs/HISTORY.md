# How much history does the daily prescription need? (backtest, Oct 13 2026)

Data: Bryan's full Garmin export (2,829 activities since Feb 2018; sleep since 2018; resting HR since 2019; overnight HRV since Sept 2025).
Method: for each of 316 days (Dec 1 2025 – Oct 12 2026), rebuild Ceiling's inputs using only the last N months, run the same decision pieces (recovery reading, load, sleep bank), compare.
Limits: activity summaries only (no per-second streams), so max HR and threshold use summary values with a spike filter. The app itself uses streams.

## A. Zones each window would set (as of Oct 12 2026)
| History | Max HR | Threshold | Easy ceiling |
|---|---|---|---|
| 1–2 months | 173–176 | 135 | 120 (too low: not enough hard efforts) |
| 3 months | 180 | 145 | 129 |
| 4 months | 180 | 151 | 134 |
| 6 months | 180 | 152 | 135 |
| 12 months | 181 | 152 | 135 |
| 24 months | 184 | 158 | 141 (+6 stale) |
| Full archive (2018–) | 188 | 169 | 150 (+15 stale) |

Best hour-long effort by year: 169 (2021–22), 161 (2023–24), 158 (2025), 152 (2026). Old efforts describe an older athlete.
Raw summaries also carry sensor spikes (243 on a walk, 242 in strength training, 229 on a run) that a stream filter must remove.

## B. History length alone (same current zones; reference = 24 months)
| History | Same call as reference | Off by 2+ levels | Fitness number error (mean / worst) |
|---|---|---|---|
| 1 month | 44% | 17% | 50% / 84% |
| 2 months | 75% | 7% | 23% / 37% |
| 3 months | 85% | 0% | 12% / 17% |
| 4 months | 92% | 0% | 6% / 14% |
| 6 months | 98% | 0% | 1.4% / 2.8% |
| 12 months | 100% | 0% | 0% |

## Conclusion
- 6 months of history gives the same daily call 98% of the time; 12 months, 100%.
- Zones should come from the last 6 months of efforts (max HR from the last 12). Data older than 12 months makes zones worse.
- Commercial: request 6–12 months at connect; never require a full Garmin export.
