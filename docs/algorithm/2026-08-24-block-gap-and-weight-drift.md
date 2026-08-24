# 2026-08-24 — the block gap, and the weight drift underneath it

**Trigger:** the group said the published teams for session 11 (2026-08-24) were not fair.

**Verdict:** they were right, and the bot's own message had told them so in a colour it
had trained them to ignore. The split was the exact optimum of the objective the bot
minimises. The objective was the problem.

---

## 1. What was published

Session 11, 14 attendees, 7v7. What the group saw:

```
🟩🟩🟩🟩🟩  حمله    (0.03)
🟩🟩🟩🟩⬜  دریافت  (0.16)
🟨🟨🟨⬜⬜  دفاع    (0.73)
🟩🟩🟩🟩🟩  پاسور   (0.10)
🟩🟩🟩🟩🟩  سرویس   (0.10)
🟩🟩🟩🟩⬜  جاگیری  (0.25)

A 1.76 · B 1.52 · Δ 0.24 · confidence: low
```

The block gap was **0.73, which is 4.5x the next-worst skill and 53% of all imbalance in
the split**. The headline `Δ 0.24` could not show it: the composite is a weighted sum, so
a large gap in one skill gets cancelled by small gaps in the others. Blocking is also the
most visible skill on a volleyball court, which is why this specific concentration is the
one that got noticed.

Team rosters by block rating (published team in brackets):

| Player | Team | Block |
|---|---|---|
| Ali I | A | +1.06 |
| Hamzeh Hosseini | B | +0.87 |
| EM | A | +0.52 |
| Meysam Bz | B | +0.28 |
| Esmaeil, Alireza, Naji, Mohammad, Taherifar | mixed | ≈ 0.00 |
| Homayoun Soltani | B | −0.16 |
| Hessam | B | −0.46 |
| Ali Sh | A | −0.49 |
| Saeed Hosseini | A | −0.51 |
| Morteza | B | −0.75 |

Team B got three of the four weakest blockers and only one of the top three.

## 2. Root cause: a sum of absolute gaps is indifferent to concentration

`_optimal_assign` enumerated all C(14,7) = 3432 splits and minimised
`Σ_skill weight · |gap_skill|`. The published split **is** the global minimum of that
objective, confirmed exhaustively, not sampled.

The flaw is that an absolute-value sum scores "six 0.23 gaps" and "one 0.73 gap plus five
near-zeros" almost identically, so the optimiser will concentrate every ounce of slack in
one skill to shave a hair off the total. On session 11 it bought a 0.03 attack gap and a
0.10 serve gap by handing Team A two of the top three blockers.

It was avoidable. Swapping **Ali Sh ↔ Morteza** drops block to 0.20; the cost is receive
rising to 0.43 and serve to 0.54, and the total across all skills rising 1.38 → 1.77. The
old objective refuses that trade because it only reads the total.

Not a one-off. Re-running every past roster (against *today's* ratings, so these are not
byte-identical to what was published at the time):

| Session | Worst skill gap, `Σw·\|gap\|` | Worst skill gap, `Σw·gap²` |
|---|---|---|
| s3 | 0.395 | 0.395 |
| s4 | 0.961 | 0.724 |
| s5 | 0.831 | 0.843 |
| s6 | 0.723 | 0.723 |
| s7 | 0.349 | 0.349 |
| s8 | **1.793** | 0.687 |
| s9 | 0.454 | 0.454 |
| s10 | **0.988** | 0.465 |
| s11 | **0.734** | 0.537 |

The stored historical metrics show the same pattern in what was actually published:
s5 had a 1.36 block gap, s6 a 0.65 positioning gap, s7 a 0.64 serve gap.

### The change

`w * abs(gap)` → `w * gap * gap`. Squaring makes a 0.73 gap cost 4.5x what four 0.20 gaps
cost, so concentrating imbalance stops being free. No new thresholds, no extra config, and
the exhaustive search is unchanged.

### Verified effect on session 11 (real code path, live DB copy)

```
🟩🟩🟩🟩⬜  حمله    (0.18)
🟨🟨🟨⬜⬜  دریافت  (0.43)
🟩🟩🟩🟩⬜  دفاع    (0.20)   <- was 0.73
🟩🟩🟩🟩⬜  پاسور   (0.28)
🟨🟨⬜⬜⬜  سرویس   (0.54)
🟩🟩🟩🟩⬜  جاگیری  (0.13)

worst skill gap 0.537 (was 0.734) · composite Δ 0.029 (was 0.240)
```

It moves exactly two players, Ali Sh and Morteza, as predicted. Note the bars are now
*more* alarming while the teams are *more* balanced: that is correct. The old display
was flattering the old split.

## 3. Second finding: the live weights were from a retired rating model

`/opt/toop/.env` on the VPS, and `.env.example` in the repo it was copied from, still held
the three-axis weights from before the six-indicator migration:

| Indicator | Intended | Actually live | Source |
|---|---|---|---|
| attack | 0.167 | **0.400** | `WEIGHT_ATTACK` |
| setting | 0.167 | **0.200** | `WEIGHT_SETTING` |
| receive | 0.167 | 0.167 | default |
| block | 0.167 | 0.167 | default |
| serve | 0.167 | 0.167 | default |
| positioning | 0.167 | 0.167 | default |
| **total** | **1.000** | **1.267** | |

`WEIGHT_DEFENSE=0.4` was also still in the file. That field no longer exists and
`Settings` is configured with `extra="ignore"`, so pydantic dropped it in silence. The bot
logged `Composite weights sum to 1.2666, not 1.0` on every startup and nobody was reading
the logs.

**Honest scope: this did not cause the session-11 split.** Re-running session 11 under
both weight vectors selects the identical teams. But attack was weighted 2.4x block in the
objective, which is precisely the bias that makes trading away block cheap, and it
inflated every composite the group saw.

### Effect on scores

Correcting the weights compresses every composite toward zero (the weight sum drops
1.267 → 1.000) and **reorders 11 of 25 players**. Largest movers:

| Player | Old (live) | New (equal) | Change |
|---|---|---|---|
| Fazel | −1.709 | −1.331 | +0.378 |
| Vahid | −1.018 | −0.745 | +0.272 |
| Mahdi Shakouri | +1.178 | +0.899 | −0.279 |
| Hamzeh Hosseini | +0.640 | +0.403 | −0.237 |
| Ghasem | −0.989 | −0.764 | +0.225 |
| Meysam Bz | +0.899 | +0.690 | −0.210 |

Direction is as expected: attack specialists come down, everyone else comes up. Rank swaps
are all adjacent pairs (Esmaeil ↔ Hamzeh, Alireza/Naji ↔ H. Shakouri, Mohammad ↔
Taherifar, Homayoun ↔ Mohammad Amin, Vahid ↔ Ghasem); nobody jumps more than two places.

### Guard added

`config.unknown_weight_keys()` scans the environment and `.env` for `WEIGHT_*` keys that
no longer map to an indicator, and `Settings` logs a warning per stray key at startup. The
silent drop was the actual failure mode, so it is now loud.

## 4. Third finding: the fairness bars were lying by threshold

`_FAIR_BALANCED = 0.40` / `_FAIR_OK = 0.80` were inherited from the composite-only model.
Under them a 0.73 gap rendered 🟨 "قابل‌قبول" while being 4.5x the next-worst skill.

New thresholds are set from the observed distribution of the 54 per-skill gaps across
sessions 3-11 under the squared objective (median 0.28, p90 0.60, max 0.84):

| | Old | New | Rationale |
|---|---|---|---|
| green | ≤ 0.40 | ≤ 0.30 | p60 |
| amber | ≤ 0.80 | ≤ 0.60 | p90 |
| bar scale | 1.5 | 1.0 | the bar barely moved across the real gap range |

Under the new thresholds 56% of skill-gaps read green, 33% amber, 11% red, and 4 of 9
historical sessions would show at least one red row. Red stays rare enough to mean
something, which is the whole point: `confidence: low` has fired on every message since
session 3 and the group has stopped seeing it.

## 5. What was deliberately not changed

Items 1-6 in the [open issues table](README.md#known-open-issues). The largest remaining
real-accuracy problem is coverage, not code: ratings per player range from 2 (Homayoun
Soltani) to 14, and shrinkage flattens the thin ones toward "average". No objective
function fixes that.

## Method

- Live SQLite copied from `de-rarecloud:/opt/toop/data/toop.db` plus its WAL, checkpointed
  locally. The bot was never stopped and nothing was written back.
- Session-11 figures come from the stored `snapshots.metrics_json`, so they are exactly
  what the group saw.
- All 3432 possible 7v7 splits were enumerated for session 11, so "the published split is
  the optimum of the old objective" is exhaustive.
- Cross-session comparisons re-run historical rosters against today's ratings and are
  labelled as such above.

Full narrative version with charts: `2026-08-24-block-gap-and-weight-drift.html`, next to this file.
