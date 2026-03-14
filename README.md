# Python Trading Bots — cTrader API

## Overview
Collection of algorithmic trading bots developed with Python
and the cTrader Open API, applied to Nasdaq (US100) and S&P500.

## Indicators implemented from scratch
- **EMA** — Exponential Moving Average with optimized smoothing
- **ATR** — Average True Range for volatility measurement
- **ADX** — Average Directional Index with Wilder smoothing
- **Hurst Efficiency Index** — Fractal analysis of price movement
- **Bollinger Bands** — With dynamic BBw percentile compression

## Bot architecture (BotTradingM5)
- Directional bias detection (LONG / SHORT / NEUTRAL)
- Entry via Stop or Market orders depending on slippage
- Daily risk management (circuit breaker after 1 stop loss)
- Monthly risk management (4 levels: normal / halved / lock / stop)
- Automatic SL/TP correction on execution slippage
- Early exit based on structural reversal and Hurst deceleration

## Key technical features
- All indicators built natively — no external libraries
- Full OOP architecture with event-driven design
- Real-time price buffer sampling (15s intervals)
- Loss zone memory to avoid re-entering losing price areas

## Technologies
- Python 3.x
- cTrader Open API
- Standard library only (math, datetime, traceback)
