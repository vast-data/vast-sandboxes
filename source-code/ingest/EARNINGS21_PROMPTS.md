# Earnings-21 demo prompts

Ready-to-paste research tasks for the ingested corpus
(`earnings-calls` / `earnings21.call_chunks`).

Schema reminder (for reading answers):
`customer` = company · `sales_stage` = sector · `opportunity` = `Company Qn 2020 earnings`

Corpus sectors in this dataset: Conglomerate, Financial, Healthcare, Consumer Goods,
Industrial Goods, Utilities, Basic Materials, Technology, Services.

---

## Sales (demand / competition / next steps)

### 1. Utilities — COVID demand & rate narrative
**Title:** `Utilities — demand & regulatory talking points`

> Across Utilities-sector earnings (NextEra Energy, Spire, Enagas, Eversource, RWE,
> One Gas), what did management say about load/demand shifts, rate cases or regulatory
> risk, and capital plans? Per company, cite the strongest demand or regulatory signal
> and the single highest-impact talking point for an account team selling into that utility.

### 2. Industrials — orders, backlog, recovery
**Title:** `Industrials — orders & backlog recovery`

> For Industrial Goods names (Ingersoll Rand, Cementos Argos, Constellium, Culp,
> New Home Company), extract commentary on orders, backlog, pricing, and recovery timing.
> Per company, list one demand signal already demonstrated, the biggest remaining risk,
> and one sales follow-up to advance the account narrative.

### 3. Technology — product & competitive pressure
**Title:** `Tech earnings — product & competitive pressure`

> Across Technology-sector calls (Adtran, SiTime, Identiv, Phoenix New Media,
> Innovative Solutions and Support), what product, design-win, competitive, or
> customer-concentration themes appear? Rank themes by urgency for a tech sales team
> and give the top 2 talking points per company.

### 4. Energy / materials — commodity & CapEx
**Title:** `Energy & materials — CapEx and commodity risk`

> In Basic Materials / energy-adjacent calls (ConocoPhillips, Mosaic, Earthstone Energy,
> GCP Applied Technologies), where do speakers discuss commodity prices, CapEx discipline,
> volume outlook, or customer mix? Per company, cite the concern or signal and the one
> next step an account team should take.

### 5. Logistics & media — spend & network themes
**Title:** `Services — logistics & media spend`

> Compare Kuehne+Nagel, Nexstar Media, Cumulus Media, Zagg, and Lands' End on customer
> demand, advertising/spend trends, and competitive positioning. Rank the top themes by
> urgency for a services sales team and give one concrete next step per company.

### 6. Conglomerates beyond GE
**Title:** `Danaher / Rev / Bed Bath — multi-segment narratives`

> For Danaher, Rev Group, and Bed Bath & Beyond, what multi-segment demand, portfolio,
> or competitive themes did management and analysts emphasize? Per company, surface the
> single most important next step for an account team covering that narrative.

### 7. Biotech pipeline (sales lens)
**Title:** `Biotech — pipeline credibility for account teams`

> On Aldeyra Therapeutics, AcelRx Pharmaceuticals, and Affimed earnings calls, what did
> management and analysts say about pipeline milestones, partnerships, and clinical or
> commercial risk? Per company, cite the signal and the highest-impact next step for a
> life-sciences account team.

---

## Finance (guidance / margin / capital)

### 1. Utilities — rate base, FCF, capital intensity
**Title:** `Utilities — FCF, rate base & capital risk`

> Across Utilities calls (NextEra, Spire, Enagas, Eversource, RWE, One Gas), extract
> guidance, FCF, rate-base growth, and capital-intensity commentary that affects deal
> economics. Flag the highest-impact finance watch-item per company and one commercial
> mitigation before engaging the account.

### 2. Industrials — margin & cost actions
**Title:** `Industrials — margin & cost-action risk`

> For Ingersoll Rand, Cementos Argos, Constellium, and Culp, where did participants
> discuss margins, cost actions, pricing power, or revenue timing? Rank companies by
> margin risk and recommend one finance mitigation per company.

### 3. Technology — OpEx discipline & concentration
**Title:** `Tech — OpEx, concentration & revenue timing`

> In Technology-sector earnings (Adtran, SiTime, Identiv, Phoenix New Media,
> Innovative Solutions and Support), summarize OpEx discipline, customer concentration,
> and revenue-timing risk. Cite the highest-impact finance watch-item per company and
> one commercial action to protect margin.

### 4. Energy / materials — commodity & CapEx economics
**Title:** `Energy & materials — commodity & CapEx economics`

> For ConocoPhillips, Mosaic, Earthstone, and GCP, extract commodity-price, CapEx,
> volume, and cash-return commentary that changes deal economics. Flag margin and
> capital-allocation risks and the one finance action to take before engaging each account.

### 5. Consumer & retail — promo vs pricing power
**Title:** `Consumer/retail — promo intensity vs pricing power`

> Review Monro, Fomento Económico Mexicano (FEMSA), Lands' End, and Bed Bath & Beyond
> for pricing, promo intensity, inventory, and margin commentary. Rank by pricing-power
> risk and recommend one finance mitigation per company.

### 6. REITs & specialty financials
**Title:** `REITs & specialty finance — credit & occupancy`

> On Healthcare Realty Trust and Clipper Realty, what did management say about occupancy,
> rent collections, leverage, and capital returns? Cite the highest-impact finance
> watch-item per company and one commercial mitigation.

### 7. Danaher vs GE — capital allocation contrast
**Title:** `Danaher vs GE — capital allocation contrast`

> Compare Danaher's and General Electric's earnings commentary on capital allocation,
> margin trajectory, and portfolio actions. Flag where deal economics diverge and the
> single most important commercial action finance should take for each account.

---

## Quick Q&A follow-ups (after a finished report)

Use on the **Quick Q&A** tab — no new research. Swap in company/theme names from the
report you just ran.

1. Which citation most strongly supports the top recommendation, and what would weaken it?
2. Did analysts push back on management's guidance? Quote the tension in one sentence.
3. What single metric or phrase should an account team lead with in the next customer call?
4. Where does the report under-weight competitive pressure relative to the transcript?
5. If we only had 60 seconds with the CFO, which risk from this report do we raise first?
6. Map each "next step" to a concrete owner (sales vs finance vs SE) and a one-week deadline.
7. Which company in this report has the weakest evidence trail — and what should we search next?

---

## Smoke / retrieval checks (CLI)

Useful after ingest to prove the corpus is warm (not full research runs):

```bash
cd source-code/ingest
python -m temp_ingest.search --query "NextEra rate base and capital plan" --top-k 6
python -m temp_ingest.search --query "Ingersoll Rand orders backlog pricing" --top-k 6
python -m temp_ingest.search --query "SiTime design wins and competitive pressure" --top-k 6
python -m temp_ingest.search --query "ConocoPhillips CapEx and commodity outlook" --top-k 6
python -m temp_ingest.search --query "Aldeyra pipeline milestones partnership" --top-k 6
python -m temp_ingest.search --query "Kuehne Nagel demand and network capacity" --top-k 6
```

---

## Company cheat-sheet (by sector)

| Sector | Companies in Earnings-21 |
| --- | --- |
| Conglomerate | General Electric, Danaher, Rev Group, Bed Bath & Beyond |
| Financial | American Express, Travelers, Banco Santander, Healthcare Realty, Clipper Realty |
| Healthcare | Illumina, Dentsply Sirona, Affimed, Aldeyra, AcelRx |
| Consumer Goods | Hershey, Yeti, Pilgrim's Pride, Monro, FEMSA |
| Industrial Goods | Ingersoll Rand, Cementos Argos, Constellium, Culp, New Home Company |
| Utilities | NextEra Energy, Spire, Enagas, Eversource, RWE, One Gas |
| Basic Materials | Mosaic, ConocoPhillips, Earthstone Energy, GCP Applied Technologies |
| Technology | Adtran, SiTime, Identiv, Phoenix New Media, Innovative Solutions and Support |
| Services | Kuehne+Nagel, Nexstar Media, Cumulus Media, Zagg, Lands' End |
