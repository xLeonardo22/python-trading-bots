# Python Trading Bots — cTrader API

Algorithmic trading bot for the NASDAQ (US100) index on the M5 timeframe.
Strategy: momentum-following — detects market compression phases and enters
on volume expansion breakouts.

---

## Evolution: v1 → v2

I spent months testing strategies and indicators until I reached v1 — the first
version with a real statistical edge. Backtests confirmed roughly 5% annual gain
across 2023, 2024 and 2025. The problem appeared when I extended the test to
2021–2022: the bot lost 22% during the high-volatility, trend-less market of
that period, which made clear that the approach wasn't robust enough.

That failure led me to rethink the core logic. v2 adopts a momentum-following
strategy: instead of predicting direction, the algorithm waits for compression
phases and enters only on confirmed volume expansion breakouts. It also processes
market data faster and generates more selective signals. The 4-year backtest —
including 2022 — returned 8% overall profit.

---

## Repository structure

```
python-trading-bots/
├── BotTradingM5_v1.py       # Bias-based strategy: EMA structure + Hurst filter
└── MomentumBot_v2.py        # Momentum strategy: WAE + Choppiness + Bollinger Squeeze
```

---

## Indicators

This bot is based on the cTrader API, and the strategy relies on mathematical
indicators such as Exponential Moving Average, Average True Range, and Average
Directional Index (Wilder smoothing) to measure market momentum. Between v1 and
v2, the indicator set was upgraded to include the Hurst Fractal Efficiency Index,
Bollinger Bands with dynamic Bandwidth Percentile, Waddah Attar Explosion (WAE),
and the Choppiness Index — all implemented from scratch without external
libraries. This shift allowed the algorithm to distinguish between trending and
ranging market conditions more reliably, which was the core weakness of v1.

| Indicator | Bot | Purpose |
|-----------|-----|---------|
| Exponential Moving Average (EMA) | v1 | Trend direction and bias detection |
| Average True Range (ATR) | v1, v2 | Volatility measurement and SL sizing |
| Average Directional Index (ADX) | v1, v2 | Trend strength filter |
| Hurst Fractal Efficiency Index | v1, v2 | Measures directional efficiency of price movement |
| Bollinger Bands + BBw Percentile | v1, v2 | Channel compression and breakout detection |
| Waddah Attar Explosion (WAE) | v2 | Volume expansion and breakout force |
| Choppiness Index | v2 | Distinguishes trending vs ranging conditions |
| Macro Efficiency | v2 | Filters chaotic market regimes |
| Candle Conviction | v2 | Entry quality filter based on candle structure |

---

## Key technical features

- All indicators implemented from scratch — no external libraries
- Full OOP architecture with event-driven design (OnBar / OnTick)
- Real-time price buffer sampled at 15-second intervals
- Multi-level risk management: daily circuit breaker + monthly drawdown control
- Automatic SL/TP recalculation on execution slippage
- Loss zone memory to avoid re-entering losing price areas

---

## Technologies

- Python 3.x
- cTrader Open API
- Standard library only (`math`, `traceback`)

## How to run

This bot runs natively inside the cTrader platform:

1. Open cTrader and navigate to **Automate**
2. Create a new **cBot** and paste the source code
3. Attach it to a **NASDAQ (US100) M5** chart
4. Configure risk parameters in `on_start()` before running

> No external dependencies required — all indicators are implemented
> from scratch using Python's standard library only.
