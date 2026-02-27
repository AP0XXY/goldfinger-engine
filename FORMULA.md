# Goldfinger Money Formula — Baseline v3.1

*Last updated: 2026-02-27*

---

## The Core Bet

Kalshi 15-minute binary options on BTC and ETH.
Each contract pays **$1.00 if correct, $0.00 if wrong**.
We buy the side the market is mispricing — and we only buy cheap contracts (≤ $0.35).

---

## The Edge: Fair Value vs Market Price

```
Fair Value  =  N(d2)   [Black-Scholes d2 term]

d2 = ( ln(S/K)  −  0.5 × σ² × T )  /  ( σ × √T )

Where:
  S  =  spot price (BTC or ETH, from CoinGecko)
  K  =  strike price (extracted from Kalshi market)
  T  =  time to expiry in years  (minutes_left / 525,960)
  σ  =  annualized vol  →  BTC: 0.80,  ETH: 0.90

Edge  =  Fair Value  −  Market Ask Price
```

If the market is selling YES at $0.20 but our model says it's worth $0.28 → **$0.08 edge**.

---

## Entry Filters (ALL must pass)

| Filter | Threshold | Why |
|--------|-----------|-----|
| Time to expiry | ≥ 3 min | Avoid last-minute noise / illiquidity |
| Market ask price | ≤ $0.35 | Asymmetry: win $0.65+, risk $0.35 = 1.86:1 min |
| Edge | ≥ $0.04 | 4¢ after fees is meaningful |
| Reward/Risk | ≥ 2.0:1 | (1 − price) / price ≥ 2.0 → price ≤ $0.33 |
| Confidence score | ≥ 40 | Composite gate (see below) |

---

## Confidence Scoring (0–100)

```
Edge component      0–30 pts    (1pt per cent of edge; +10 bonus if edge ≥ 10¢)
EMA-20 alignment    +25 pts     (trend aligned with bet direction)
                    +10 pts     (neutral trend)
                    −30 pts     (counter-trend — effectively blocked unless exceptional)
Risk/Reward         +20 pts     (R/R ≥ 10)
                    +15 pts     (R/R ≥ 5)
                    +10 pts     (R/R ≥ 3)
                    +7 pts      (R/R ≥ 2.5)
Time window         +15 pts     (sweet spot: 5–12 min to expiry)
                    +10 pts     (12–14.5 min)
                    +8 pts      (3–5 min)
```

**Minimum passing score: 40**

---

## Position Sizing (Fractional Kelly)

```
Kelly  =  edge / (price × odds)          odds = (1 − price) / price
Fraction  =  Kelly × kelly_factor        BTC: 0.08,  ETH: 0.06
Dollars   =  balance × fraction
Contracts =  floor(dollars / price)      Hard cap: max 10 contracts
```

With a $500 balance, a 10¢ edge on ETH at $0.20:
```
odds     = 0.80 / 0.20 = 4.0
kelly    = 0.10 / (0.20 × 4.0) = 0.125 = 12.5%
fraction = 0.125 × 0.06 = 0.0075 = 0.75%
dollars  = $500 × 0.0075 = $3.75
contracts = floor(3.75 / 0.20) = 18  →  capped at 10
```

---

## Fee Model

```
Fee  =  0.07 × count × price × (1 − price)
Per-contract fee: capped at $0.02/contract, min $0.01/contract
```

Example: 5 contracts @ $0.20
```
raw = 0.07 × 5 × 0.20 × 0.80 = $0.056
per contract = $0.056 / 5 = $0.0112  (within $0.01–$0.02 range)
total fee = $0.056
```

---

## P&L Per Trade

```
Cost    =  price × contracts  +  fee
Payout  =  contracts × $1.00  (if correct)

Win:   P&L = (1.0 − price) × contracts  −  fee
Loss:  P&L = −(price × contracts + fee)

Breakeven win rate  =  (price + fee_per_contract) / 1.0
```

At $0.20 per contract (fee ≈ $0.011/contract):
```
Win:  profit = $0.789/contract
Loss: loss   = $0.211/contract
Breakeven = 21.1% win rate  (model needs > 21.1% accuracy to print money)
```

At $0.35 per contract (fee ≈ $0.016/contract):
```
Win:  profit = $0.634/contract
Loss: loss   = $0.366/contract
Breakeven = 36.6% win rate
```

---

## The Full Loop

```
1. Fetch spot prices (CoinGecko, 30s cache)
2. Fetch 1-min Coinbase candles → compute EMA-20 → get trend
3. Fetch Kalshi markets (KXBTC, KXETH 15M series)
4. For each market:
   a. Extract strike price
   b. Compute fair value with B-S d2
   c. Compute edge vs YES ask and NO ask
   d. Run all 5 filters
   e. Score confidence
5. Surface ranked opportunities (highest confidence first)
6. Size positions via fractional Kelly
7. Execute via Kalshi API
8. Track P&L in trades.json
```

---

## Current Numbers (v3.1 baseline)

| Parameter | BTC | ETH |
|-----------|-----|-----|
| Implied vol | 0.80 | 0.90 |
| Kelly fraction | 8% | 6% |
| Max position | 10 contracts | 10 contracts |
| Min edge | $0.04 | $0.04 |
| Max entry price | $0.35 | $0.35 |
| Min R/R | 2.0:1 | 2.0:1 |
| Min confidence | 40 | 40 |

---

## What We're Optimizing Toward

The formula makes money when: **actual win rate > model-implied breakeven rate**

The main levers:
1. **Vol calibration** — wrong vol = phantom or missed edges (currently tuned up after v1/v2 over-traded)
2. **Signal quality** — confidence score weights / thresholds
3. **Time of entry** — time window scoring (5–12 min sweet spot)
4. **Filter tightness** — how many signals we let through vs how precise
5. **Position sizing** — Kelly fractions (currently conservative while calibrating)

Baseline deployed: 3 trades (2026-02-27), all on demo. Production switch pending.
