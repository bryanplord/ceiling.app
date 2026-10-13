# Rx — the daily training prescription

## The problem
Endurance athletes pay for Strava, Garmin Connect+, TrainingPeaks and trail apps, and none of them answer the only question that matters each morning: **what exactly should I do today?** Existing tools describe data or suggest workouts. Rx prescribes.

## The loop
1. **Prescribe** — each morning: what to do, with an HR range, a hard ceiling and a maximum duration.
2. **Score** — after the session: how closely it was followed.
3. **Recover** — overnight: sleep, HRV and resting HR, each against the athlete's own baseline.
4. **Adjust** — today's prescription changes based on last night.
5. **If/then** — what today's choice sets up for tomorrow.

## What the athlete cares about (the bar for every feature)
1. **Real metrics.** Measured from their own data. No "medium", no "average", no assigned defaults.
2. **Am I progressing?** Answered with measures only fitness can move.
3. **An engaged, accurate coach** that says exactly what to do and not do.
4. **The price of not following it:** what skipping costs, what overdoing costs, and how it shows up.

## Every prescription, in order
1. **Ideal** — the exact session: sport, HR range, hard ceiling, max duration.
2. **Compress** — the shortest version that still does the job.
3. **Stretch** — the most that can be added without making tomorrow worse (computed by projecting tomorrow).
4. **If you skip it** — fitness given up today, and over 4 more skipped days.
5. **If you overdo it** — what tomorrow becomes, plus the athlete's own measured HRV cost and recovery time.

## The prescription format (non-negotiable)
Every prescription has five parts:

| Part | Example |
|---|---|
| **Verdict** | Light day. |
| **Why** | Slept well, but 7-day load is 40% above your norm. |
| **Do** | Walk, 4 miles max. HR under 118. |
| **Don't** | No running. No biking. |
| **Tonight** | In bed by 9:30 PM. |

Plus if/then scenarios: *"Walk today and, with a normal night, tomorrow is green: up to 3 hours, hard efforts allowed."*

Rules for the copy:
- Specific numbers only. Never "moderate effort", "listen to your body".
- Rest is a valid prescription, not a failure state.
- Times in 12-hour AM/PM.

## Physiology principles
- **Nothing from device zones, Peloton zones, power zones or 220-minus-age.** Every number comes from the athlete's own data.
- **Max HR** = highest *credible* sustained heart rate observed: 15-second sustained peaks, spikes rejected, cadence lock rejected, smooth ramp required. Reported as a floor ("proven"), with corroboration status.
- **Threshold HR (LTHR)** from the best sustained 20–60 minute efforts.
- **Aerobic threshold (LT1)** = the highest steady HR held with <5% decoupling (speed-per-beat drift, first half vs second half) on runs, walks and road rides. MTB and skiing excluded: terrain drives their speed.
- **Zones** are anchored on measured LT1 and LT2: Z2 ends exactly at LT1, Z4 exactly at LT2. Until LT1 is measured, zones are flagged provisional.
- **Progress**: aerobic efficiency at the same HR, resting HR, HRV, HR recovery after hard efforts, 6-week load — last few weeks vs ~2 months earlier.
- **Personal response**: next-morning HRV vs yesterday's relative load, fitted per athlete; plus mornings-to-baseline after their biggest days. This is what makes "the price" personal.
- **Recovery** is always relative to the athlete's own baseline (HRV ln-RMSSD vs 60-day mean ± SD; resting HR vs 60-day median; sleep score vs personal median). Never compare HRV across devices or people.
- **Load**: Banister TRIMP per session from second-by-second HR; fatigue (7-day) and fitness (42-day) exponential averages; form = fitness − fatigue.
- **Numbers come from code, never from an LLM.** An LLM may later reword the prescription, but the verdict, ranges, ceiling and duration are computed deterministically and are testable.

## Sports
MTB, road, trail running, running, walking/hiking, ski touring, alpine skiing. No race required: the default goal is "fit and durable year-round".

## Data sources (as of Oct 2026)
| Source | Status | Use |
|---|---|---|
| intervals.icu API (personal key) | Works today. Warning posted 9 Oct 2026: Garmin-sourced data may be blocked from the API soon. Self-uploaded files likely unaffected. | Primary prototype feed: activities, HR streams, wellness |
| python-garminconnect (unofficial, logs in as the user) | Works; unsanctioned; can break when Garmin changes login | Personal prototype only: HRV, sleep, training readiness. Never in production. |
| Garmin account export zip | Always available (user's own data) | Full-history backfill |
| Garmin Connect Developer Program | Paused for new applicants since spring 2026 | Apply when it reopens |
| Strava API | Prohibits AI use of its data | Do not use |
| Terra / Junction aggregators | Paid, licensed | Multi-user production path |

## Product surface
**Web first.** No native iOS or Android for now. Pages: Today (prescription), Yesterday (score), Recovery (trends), Progress, Profile (max HR, thresholds, zones).

## Open questions
- Validate retention: does the athlete still follow it after month two?
- Does Garmin's raw overnight HRV survive any export path other than the unofficial library?
