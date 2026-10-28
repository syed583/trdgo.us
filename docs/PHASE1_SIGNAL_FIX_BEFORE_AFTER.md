
cases: 111   after-fix failures: 0   old-page mismatches: 26

BEFORE vs AFTER (every case where the old page disagreed with the backend):
| model | case | score | cov% | backend final | OLD page | NEW page |
|---|---|---:|---:|---|---|---|
| claude | boundary | 49.9 | 100 | SELL | NEUTRAL | SELL |
| claude | boundary | 69.9 | 100 | NEUTRAL | BUY | NEUTRAL |
| claude | boundary | 70 | 100 | NEUTRAL | BUY | NEUTRAL |
| claude | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| claude_pro | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| gemini | boundary | 42.1 | 100 | NEUTRAL (NEUTRAL) | SELL | NEUTRAL |
| gemini | boundary | 57.9 | 100 | NEUTRAL (NEUTRAL) | BUY | NEUTRAL |
| gemini | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| grok | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| chatgpt | boundary | 40 | 100 | NEUTRAL (NEUTRAL) | SELL | NEUTRAL |
| chatgpt | boundary | 40.1 | 100 | NEUTRAL (NEUTRAL) | SELL | NEUTRAL |
| chatgpt | boundary | 64.9 | 100 | NEUTRAL (NEUTRAL) | BUY | NEUTRAL |
| chatgpt | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| meta | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| kimi | boundary | 42.1 | 100 | NEUTRAL (NEUTRAL) | SELL | NEUTRAL |
| kimi | boundary | 57.9 | 100 | NEUTRAL (NEUTRAL) | BUY | NEUTRAL |
| kimi | low_data_69.9 | 80 | 69.9 | LOW_DATA | BUY | LOW DATA |
| claude | real_no_data | 100 | 8.2 | LOW_DATA | BUY | LOW DATA |
| claude_pro | real_no_data | 91.7 | 18 | LOW_DATA | BUY | LOW DATA |
| gemini | real_no_data | 55 | 21.7 | LOW_DATA | BUY | LOW DATA |
| meta | real_no_data | 100 | 5.3 | LOW_DATA | BUY | LOW DATA |
| kimi | real_no_data | 52.6 | 21.8 | LOW_DATA | NEUTRAL | LOW DATA |
| claude | gate_no_trade_ratio_gt_1.5 | 75 | 100 | NEUTRAL | BUY | NEUTRAL |
| claude | gate_priced_in | 75 | 100 | NEUTRAL | BUY | NEUTRAL |
| kimi | bucket_rule_options_opposes | 60 | 100 | NEUTRAL (NEUTRAL) | BUY | NEUTRAL |
| gemini | iv_filter_blocks_spec_buy | 60 | 100 | NEUTRAL (NEUTRAL) | BUY | NEUTRAL |
