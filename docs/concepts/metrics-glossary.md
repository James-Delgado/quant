# Performance Metrics Glossary

> Reference for all metrics reported by `src/quant/backtest/metrics.py` and
> `src/quant/backtest/report.py`. Read this before interpreting backtest output
> or adding new metrics.

---

## Return metrics

### Total return
`(final equity / initial equity) - 1`

The simplest measure. Easy to understand; hard to compare across strategies
with different holding periods or start dates.

### Annualized return
`(1 + total_return) ^ (252 / n_bars) - 1`

Total return scaled to a one-year equivalent using 252 trading days. Makes
strategies of different lengths comparable. Degenerates to -1.0 (total ruin)
when equity reaches zero — annualisation through zero is undefined.

### Max drawdown
`min over t of: (equity[t] - peak[t]) / peak[t]`

The largest peak-to-trough decline in the equity curve, expressed as a
fraction. Always ≤ 0. A drawdown of -0.25 means the portfolio fell 25% from
its previous high before recovering.

This is the number that gets strategies shut down in practice. Risk managers
watch it, not Sharpe. Size positions so that the expected max drawdown is
survivable for the operator.

---

## Risk-adjusted metrics

### Sharpe ratio
`(mean daily return / std of daily returns) × √252`

Return per unit of total volatility, annualised. The most widely used
single-number summary of risk-adjusted performance.

| Value | Interpretation |
|-------|----------------|
| < 0 | Strategy loses money |
| 0 – 0.5 | Weak, barely above noise |
| 0.5 – 1.0 | Acceptable for a live strategy |
| 1.0 – 2.0 | Good |
| > 2.0 | Excellent — verify carefully for data leakage |

Uses `ddof=1` for the standard deviation. Returns 0.0 when std is zero
(constant or zero returns).

### Sortino ratio
`(mean daily return / downside std) × √252`

Like Sharpe, but the denominator uses only *negative* return days:
`sqrt(mean(r²) for r < 0)`. The argument is that upside volatility is not risk.

A strategy with Sortino >> Sharpe has spiky gains and smooth losses — generally
a desirable property.

### Calmar ratio
`annualized_return / |max_drawdown|`

Return per unit of worst historical loss. A Calmar of 1.0 means the strategy
earns back its max drawdown in one year.

Preferred by practitioners who think in terms of capital at risk rather than
return variance. Reported as `NaN` (shown as "—") when max drawdown is zero —
the ratio is undefined, not infinite, in that case.

---

## Trade-level metrics

### Hit rate
`number of profitable trades / total trades`

Fraction of trades that closed with positive P&L. 0.5 is random. A strategy
can be profitable with a hit rate below 0.5 if winners are larger than losers
(see profit factor).

Always emitted even without a trade log (defaults to 0.0) so callers never get
a KeyError.

### Profit factor
`sum of winning P&L / |sum of losing P&L|`

How much the strategy makes on winners relative to what it loses on losers.

| Value | Interpretation |
|-------|----------------|
| < 1.0 | Losers outweigh winners — net loss |
| 1.0 | Break-even before costs |
| > 1.0 | Winners outweigh losers |
| ∞ | No losing trades |

A hit rate of 40% with a profit factor of 2.0 is viable: you lose 6 small
trades for every 4 large wins and still come out ahead. Always emitted; `NaN`
when no trades exist, `inf` when no losing trades exist.

---

## IS / OOS gap

The summary table and report show `IS metric - OOS metric` for each key metric.

**In-sample (IS):** performance on data the model was *trained* on.
**Out-of-sample (OOS):** performance on data the model *never saw*.

IS almost always beats OOS — that is expected. The question is by how much.

| Sharpe gap | Interpretation |
|------------|----------------|
| < 0.3 | Normal estimation noise, probably fine |
| 0.3 – 0.7 | Investigate — check for feature leakage |
| > 0.7 | Overfit — model learned noise, not signal |

The OOS Sharpe is the honest number. A large IS/OOS gap means the IS Sharpe
is not a reliable estimate of live performance.

---

## Not yet implemented

- **Deflated Sharpe Ratio (DSR)** — corrects for multiple-testing across
  strategy configurations. Planned for Phase 2.
- **Turnover** — fraction of portfolio traded per day. Costs scale with it;
  high-turnover strategies need a much larger gross Sharpe to survive.
- **Information ratio** — excess return over a benchmark per unit of tracking
  error. Relevant once a benchmark is defined.

---

## References

- W. Sharpe, "The Sharpe Ratio", *Journal of Portfolio Management*, 1994.
- M. López de Prado, *Advances in Financial Machine Learning*, ch. 14–16.
