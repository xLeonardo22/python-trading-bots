Algorithmic trading bot for the NASDAQ (US100) index on the M5 timeframe.
Strategy: momentum-following — detects market compression phases and 
enters on volume expansion breakouts.


I spent months testing strategies and indicators until I reached v1 — the first version with a real statistical edge. Backtests confirmed roughly 5% annual gain across 2023, 2024 and 2025. The problem appeared when I extended the test to 2021–2022: the bot lost 22% during the high-volatility, trend-less market of that period, which made clear that the approach wasn't robust enough.
That failure led me to rethink the core logic. v2 adopts a momentum-following strategy: instead of predicting direction, the algorithm waits for compression phases and enters only on confirmed volume expansion breakouts. It also processes market data faster and generates more selective signals. The 4-year backtest — including 2022 — returned 8% overall profit.
