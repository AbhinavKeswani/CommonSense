# Research Plan — Fundamental Stock Selection (Quality-First, Mispricing-Signal)

**Status:** Handoff brief for a research Claude agent.
**Consumers:** `commonsense/analysis/scoring.py` (quality score), `commonsense/screener.py`
(cross-sectional mispricing rank), and the Atlas **Picks** feature (per-pick thesis rendering).

> This document is both a *spec* (the rubric the code implements) and a *research assignment*
> (the sector overlays and worked examples a research agent must fill in). Sections marked
> **[RESEARCH]** are deliverables for the downstream agent; sections marked **[IMPLEMENTED]**
> already exist in code and define the contract.

---

## 1. Objective & thesis

Identify **durable, long-term compounders** to hold on **fundamentals + industry research**, not
price action. The strategy is **quality-first**:

1. Score each company's **current fundamental quality** (is this a good business, today?).
2. Among high-quality businesses, the **entry signal is *unjustified* price action** — a cheap
   multiple or a drawdown that is **not** matched by any deterioration in the quality picture or a
   red flag in the MD&A. Quality intact + price down/cheap = a long candidate.
3. Price action is used only to *time and size* entries, never to pick the universe. Portfolio
   weighting and tax/horizon-aware rebalancing are a later phase (see `Atlas` plan).

**Quality gate:** growth is rewarded only when the business is also profitable, cash-generative,
and not over-levered — this avoids low-quality "growth traps."

---

## 2. Universal quality core  **[IMPLEMENTED — contract for `scoring.py`]**

Four pillars, each scored 0-100 by mapping a metric linearly between a floor (=0) and a target
(=100), clamped, then averaged within the pillar. The composite quality score is a weighted mean.

| Pillar (weight) | Signal | CommonSense source (column) | Floor → Target |
| :-- | :-- | :-- | :-- |
| **Profitability (0.30)** | Net margin % | `ratios_financial_health.csv:net_margin_pct` | 0 → 25 |
| | Return on equity % | `return_on_equity_pct` | 5 → 30 |
| | Return on assets % | `return_on_assets_pct` | 2 → 15 |
| | Gross margin % | `gross_margin_pct` | 20 → 70 |
| **Growth (0.25)** | Revenue CAGR % | `ratios_valuation_multiples.csv:revenue_cagr_pct` | 0 → 20 |
| | Earnings CAGR % | `earnings_cagr_pct` | 0 → 25 |
| **Balance sheet (0.20)** | Debt/Equity (lower better) | `debt_to_equity` | 2.0 → 0.0 (inverted) |
| | Current ratio | `current_ratio` | 1.0 → 2.5 |
| **Cash conversion (0.25)** | FCF margin % | `free_cash_flow_margin_pct` | 0 → 25 |
| | OCF / Net income | `operating_cash_flow_to_net_income` | 0.6 → 1.3 |

**Verdict buckets:** `>=75 strong`, `>=60 solid`, `>=45 watch`, else `weak`.

**[RESEARCH]** Validate and tune the floor/target bands against a labelled set of ~30 known
high-quality compounders vs. known value-traps across sectors; recommend band adjustments and
whether any pillar weight should shift. Document the evidence.

---

## 3. Industry overlays  **[RESEARCH — highest-priority deliverable]**

The universal core silently mis-scores whole sectors (a bank has no "gross margin"; a REIT's "EPS"
is meaningless). Deliver a **sector → metric map**: for each sector below, (a) which universal
signals to **suppress**, (b) the **replacement metrics** and their floor/target bands, and (c) the
**SEC XBRL concept(s)** that feed each (so the screener can extract them). Include one worked
example ticker per sector.

| Sector | Core metrics to suppress | Replacement metrics (research the bands + SEC concepts) |
| :-- | :-- | :-- |
| **Banks / financials** | gross margin, current ratio, EV/EBITDA | Net interest margin, efficiency ratio, ROTCE, CET1 capital ratio, NPL/coverage, deposit growth |
| **SaaS / software** | (keep) | Rule of 40 (rev growth % + FCF margin %), net revenue retention, gross-margin-adjusted CAC payback, SBC as % rev |
| **Capital-intensive / industrials** | net margin alone | ROIC vs. WACC spread, maintenance vs. growth capex split, FCF conversion, backlog/book-to-bill |
| **REITs** | EPS, P/E, net margin | FFO / AFFO per share, FFO payout ratio, occupancy, same-store NOI growth, net debt/EBITDA |
| **Energy / commodities** | margin trend (cyclical) | reserve replacement, all-in sustaining cost, netback, mid-cycle FCF, debt/EBITDA at strip |
| **Biotech / pharma** | current earnings/margins | pipeline stage value, cash runway (quarters), R&D productivity, patent cliff exposure |
| **Retail / consumer** | (keep) | same-store sales growth, inventory turns, gross margin trend, sales per sq ft, e-comm mix |

**[RESEARCH]** For each: propose whether the sector needs its own composite (banks/REITs likely do)
or a small override on top of the universal core. Map every replacement metric to the SEC concept
name(s) actually present in company facts (verify against real filings — concept names vary).

---

## 4. Valuation & the mispricing signal

**Multiples [IMPLEMENTED]** — `ratios_valuation_multiples.csv` / `scores.json.multiples`:
P/E, P/S, P/B, EV/EBITDA (EV/EBIT fallback), PEG, plus market cap, EV, and CAGRs.

**Mispricing rank [SCREENER — to build in `screener.py`]:** valuation is only meaningful
*cross-sectionally*. The screener computes, within a peer group (same sector), the percentile of
each name's multiples, then flags a **long candidate** when:

- quality_score is high (>= solid, i.e. >= 60), **and**
- the name is **cheap vs. its sector peers** (e.g. EV/EBITDA or P/E in the cheapest tertile), **and**
- there is **no quality deterioration** — recent `flux_ratios_financial_health.csv` shows margins /
  ROE / FCF stable-or-improving, and the MD&A carries no new material red flag.

"Unjustified price action" = cheap **without** a matching decline in the quality picture. A cheap
name whose quality is *also* falling is a value trap, not a pick.

**[RESEARCH]** Define the exact peer-grouping (GICS sub-industry vs. custom), the cheapness
threshold per sector (banks trade on P/B and P/TBV, not EV/EBITDA), and how many quarters of stable
flux count as "quality intact."

---

## 5. Per-pick thesis structure  **[contract for the Atlas Picks drawer]**

Each pick's detail renders these fields. `scores.json` supplies the quantitative half; the Atlas
Claude bridge + news API supply the narrative half. Emit as a JSON object:

```
{
  "symbol", "quality_score", "verdict", "subscores", "multiples", "flags",
  "reasons":        [ 3-5 bullets: why this is a pick, each tied to a metric ],
  "problem_solved":  "one paragraph: what problem the company solves, in which industry",
  "competitors":    [ {name, ticker, one-line how-they-compare} ],
  "industry_health": "one paragraph: how the industry/peers are doing (growth, cycle, threats)",
  "news":           [ {date, headline, source, one-line why-it-matters} ],
  "risks":          [ 2-3 bullets: what would break the thesis ],
  "mispricing_note": "why current price looks unjustified vs. quality (or 'fairly valued')"
}
```

**[RESEARCH]** Specify the prompt the Atlas Claude bridge uses to generate `reasons`,
`problem_solved`, `competitors`, `industry_health`, `risks`, `mispricing_note` from `scores.json`
+ raw news + peer list — with a hard rule to cite a metric for every claim and to never invent
numbers not present in the provided data.

---

## 6. Growth-fund extension

Apply the same rubric to funds/ETFs via **holdings look-through**: score each top holding, then
aggregate to a fund-level quality score weighted by position size; surface the fund's weighted
multiples and its exposure to low-quality names.

**[RESEARCH]** Source of holdings (fund 13F / N-PORT / provider), how many holdings to look through
(top-N by weight), and how to treat non-scoreable holdings (cash, foreign, non-filers).

---

## 7. Deliverable format & acceptance

The research agent returns:
1. Tuned universal bands (§2) with the labelled-set evidence.
2. The full **sector→metric→SEC-concept map** (§3) — the core deliverable.
3. Peer-grouping + cheapness thresholds + "quality intact" definition (§4).
4. The thesis-generation prompt (§5).
5. Fund look-through spec (§6).
6. A **buy / watch / pass** decision rubric combining quality bucket + mispricing flag, with a
   worked example ticker per sector (bank, SaaS, industrial, REIT, consumer) showing the full path
   from CommonSense outputs → score → verdict → thesis.

Everything must be machine-readable enough that `screener.py` and the Atlas `server.py:349` ranking
can consume it without human interpretation.
