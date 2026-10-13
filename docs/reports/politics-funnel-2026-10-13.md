# Politics funnel 2026-10-13

Window 2026-09-30T00:00:00Z through 2026-10-14T00:00:00Z exclusive.
Counts are log lines and unique tickers. The same open name repeats.
New flags are off in live, so their log drops are zero until they are enabled.

| stage | lines | unique tickers |
|---|---:|---:|
| candidates seen | 30912 | 122 |
| band_guard | 1230 | 9 |
| yes_floor | 0 | 0 |
| risk_cap | 0 | 0 |
| exposure | 0 | 0 |
| family | 3232 | 17 |
| veto | 0 | 0 |
| watch | 8 | 3 |
| edge | 0 | 0 |

Paper entries in the window: n=0 fee_net=0.0000.
Live entries in the window: n=6 settled=3 open=3 settled fee_net=-1.8144.
Open rows are cost only and are not in fee-net.
Live submission-hold blocks in the window: 919 lines. That hold is not one of these flags.

Fee-net of the settled live rows in the window, by flag:

| stage | n | fee_net | positive fee-net removed |
|---|---:|---:|---:|
| baseline | 3 | -1.8144 | 0.0000 |
| yes_floor | 2 | 5.7082 | 0.0000 |
| risk_cap | 3 | 2.0968 | 0.6024 |
| exposure | 3 | not_measured | not_measured |
| family | 3 | not_measured | not_measured |
| maker | 3 | quote_delta=0.4006 | 0.0000 |

All-history replay, including paper rows from August and September:

| mode | n | fee_net | delta | positive fee-net removed |
|---|---:|---:|---:|---:|
| baseline | 9 | -0.2784 | 0.0000 | 0.0000 |
| yes_floor | 8 | 7.2442 | 7.5226 | 0.0000 |
| risk_cap | 9 | 4.6720 | 4.9504 | 4.3998 |
| one_per_family | 8 | -2.2506 | -1.9722 | 1.9722 |
| exposure | 9 | not_measured | not_measured | not_measured |
| maker | 9 | 0.7400 | 1.0184 | 0.0000 |
| all | 7 | 6.8318 | 7.1102 | not_separated |

The filter that removes the most positive fee-net is risk_cap (4.3998). That figure is the all-history replay, n=9.
The YES floor removes a loss. Its positive fee-net removed is 0.0000.
Maker is a same-price fee quote. Fill probability is not modeled.
Family live overlap is not_measured because settlement clocks are absent.
Exposure is not_measured because equity at each entry is unknown.
