# Cumulative-Call Empty-Response Audit

## Setup

- audit_started_utc: 2026-05-03T16:09:45.369996+00:00
- git_head: 99882b27c76b3e97f3fe948f71e40d2149e5af3c
- model: qwen3:14b
- base_url: http://localhost:11434
- ollama_version: ollama version is 0.22.1
- daemon_uptime_before: 02-02:26:21
- unload_method: ollama stop qwen3:14b: ok
- cycle_id: gc_2026-05-03_152836
- last_cycle_end_utc: 2026-05-03T15:28:36+00:00
- next_cycle_start_utc: 2026-05-03T17:28:36+00:00
- minutes_until_next_cycle_at_start: 78.8
- raw_call_log: /var/folders/8j/qvv2v9k139ddg18pkdj41w8w0000gn/T/gov-cumulative-oljjqqu_.jsonl
- status_counts: {'valid': 211}

## Verdict

H_CUMUL: **rejected**.

Empirical thresholds from 10-call trailing windows:

| threshold | N_crit |
| --- | --- |
| 25% | None |
| 50% | None |
| 75% | None |

Recovery batch empty rate: 0/10 (0.0%).

Recommendation: File candidate PROFIT-GOV-003 at LOW if this run confirmed sustained post-threshold empties; otherwise close the cumulative-call question and keep prompt iteration gated by clean sentinels.

## Empty Rate By 10-Call Window

| window | n | empty | empty_rate |
| --- | --- | --- | --- |
| 1-10 | 10 | 0 | 0.00 |
| 11-20 | 10 | 0 | 0.00 |
| 21-30 | 10 | 0 | 0.00 |
| 31-40 | 10 | 0 | 0.00 |
| 41-50 | 10 | 0 | 0.00 |
| 51-60 | 10 | 0 | 0.00 |
| 61-70 | 10 | 0 | 0.00 |
| 71-80 | 10 | 0 | 0.00 |
| 81-90 | 10 | 0 | 0.00 |
| 91-100 | 10 | 0 | 0.00 |
| 101-110 | 10 | 0 | 0.00 |
| 111-120 | 10 | 0 | 0.00 |
| 121-130 | 10 | 0 | 0.00 |
| 131-140 | 10 | 0 | 0.00 |
| 141-150 | 10 | 0 | 0.00 |
| 151-160 | 10 | 0 | 0.00 |
| 161-170 | 10 | 0 | 0.00 |
| 171-180 | 10 | 0 | 0.00 |
| 181-190 | 10 | 0 | 0.00 |
| 191-200 | 10 | 0 | 0.00 |

## Per-Call Result Table

| call_index | empty | action | confidence | elapsed_sec |
| --- | --- | --- | --- | --- |
| 1 | False | disable_source | 0.85 | 10.02 |
| 2 | False | disable_source | 0.85 | 13.55 |
| 3 | False | disable_source | 0.85 | 17.09 |
| 4 | False | disable_source | 0.85 | 20.64 |
| 5 | False | disable_source | 0.85 | 24.16 |
| 6 | False | disable_source | 0.85 | 27.69 |
| 7 | False | disable_source | 0.85 | 31.22 |
| 8 | False | disable_source | 0.85 | 34.75 |
| 9 | False | disable_source | 0.85 | 38.28 |
| 10 | False | disable_source | 0.85 | 41.81 |
| 11 | False | disable_source | 0.85 | 45.34 |
| 12 | False | disable_source | 0.85 | 48.87 |
| 13 | False | disable_source | 0.85 | 52.40 |
| 14 | False | disable_source | 0.85 | 55.92 |
| 15 | False | disable_source | 0.85 | 59.44 |
| 16 | False | disable_source | 0.85 | 62.97 |
| 17 | False | disable_source | 0.85 | 66.50 |
| 18 | False | disable_source | 0.85 | 70.04 |
| 19 | False | disable_source | 0.85 | 73.57 |
| 20 | False | disable_source | 0.85 | 77.11 |
| 21 | False | disable_source | 0.85 | 80.64 |
| 22 | False | disable_source | 0.85 | 84.17 |
| 23 | False | disable_source | 0.85 | 87.69 |
| 24 | False | disable_source | 0.85 | 91.22 |
| 25 | False | disable_source | 0.85 | 94.76 |
| 26 | False | disable_source | 0.85 | 98.30 |
| 27 | False | disable_source | 0.85 | 101.84 |
| 28 | False | disable_source | 0.85 | 105.38 |
| 29 | False | disable_source | 0.85 | 108.91 |
| 30 | False | disable_source | 0.85 | 112.43 |
| 31 | False | disable_source | 0.85 | 115.96 |
| 32 | False | disable_source | 0.85 | 119.48 |
| 33 | False | disable_source | 0.85 | 123.01 |
| 34 | False | disable_source | 0.85 | 126.55 |
| 35 | False | disable_source | 0.85 | 130.10 |
| 36 | False | disable_source | 0.85 | 133.64 |
| 37 | False | disable_source | 0.85 | 137.19 |
| 38 | False | disable_source | 0.85 | 140.72 |
| 39 | False | disable_source | 0.85 | 144.27 |
| 40 | False | disable_source | 0.85 | 147.81 |
| 41 | False | disable_source | 0.85 | 151.35 |
| 42 | False | disable_source | 0.85 | 154.89 |
| 43 | False | disable_source | 0.85 | 158.42 |
| 44 | False | disable_source | 0.85 | 161.96 |
| 45 | False | disable_source | 0.85 | 165.49 |
| 46 | False | disable_source | 0.85 | 169.03 |
| 47 | False | disable_source | 0.85 | 172.57 |
| 48 | False | disable_source | 0.85 | 176.11 |
| 49 | False | disable_source | 0.85 | 179.64 |
| 50 | False | disable_source | 0.85 | 183.18 |
| 51 | False | disable_source | 0.85 | 186.73 |
| 52 | False | disable_source | 0.85 | 190.28 |
| 53 | False | disable_source | 0.85 | 193.82 |
| 54 | False | disable_source | 0.85 | 197.37 |
| 55 | False | disable_source | 0.85 | 200.91 |
| 56 | False | disable_source | 0.85 | 204.46 |
| 57 | False | disable_source | 0.85 | 207.99 |
| 58 | False | disable_source | 0.85 | 211.53 |
| 59 | False | disable_source | 0.85 | 215.08 |
| 60 | False | disable_source | 0.85 | 218.62 |
| 61 | False | disable_source | 0.85 | 222.17 |
| 62 | False | disable_source | 0.85 | 225.72 |
| 63 | False | disable_source | 0.85 | 229.26 |
| 64 | False | disable_source | 0.85 | 232.81 |
| 65 | False | disable_source | 0.85 | 236.35 |
| 66 | False | disable_source | 0.85 | 239.89 |
| 67 | False | disable_source | 0.85 | 243.44 |
| 68 | False | disable_source | 0.85 | 246.98 |
| 69 | False | disable_source | 0.85 | 250.52 |
| 70 | False | disable_source | 0.85 | 254.06 |
| 71 | False | disable_source | 0.85 | 257.62 |
| 72 | False | disable_source | 0.85 | 261.18 |
| 73 | False | disable_source | 0.85 | 264.73 |
| 74 | False | disable_source | 0.85 | 268.29 |
| 75 | False | disable_source | 0.85 | 271.84 |
| 76 | False | disable_source | 0.85 | 275.40 |
| 77 | False | disable_source | 0.85 | 278.96 |
| 78 | False | disable_source | 0.85 | 282.51 |
| 79 | False | disable_source | 0.85 | 286.06 |
| 80 | False | disable_source | 0.85 | 289.62 |
| 81 | False | disable_source | 0.85 | 293.18 |
| 82 | False | disable_source | 0.85 | 296.74 |
| 83 | False | disable_source | 0.85 | 300.30 |
| 84 | False | disable_source | 0.85 | 303.85 |
| 85 | False | disable_source | 0.85 | 307.41 |
| 86 | False | disable_source | 0.85 | 310.97 |
| 87 | False | disable_source | 0.85 | 314.52 |
| 88 | False | disable_source | 0.85 | 318.08 |
| 89 | False | disable_source | 0.85 | 321.64 |
| 90 | False | disable_source | 0.85 | 325.19 |
| 91 | False | disable_source | 0.85 | 328.75 |
| 92 | False | disable_source | 0.85 | 332.30 |
| 93 | False | disable_source | 0.85 | 335.86 |
| 94 | False | disable_source | 0.85 | 339.42 |
| 95 | False | disable_source | 0.85 | 342.98 |
| 96 | False | disable_source | 0.85 | 346.54 |
| 97 | False | disable_source | 0.85 | 350.10 |
| 98 | False | disable_source | 0.85 | 353.66 |
| 99 | False | disable_source | 0.85 | 357.22 |
| 100 | False | disable_source | 0.85 | 360.77 |
| 101 | False | disable_source | 0.85 | 364.33 |
| 102 | False | disable_source | 0.85 | 367.88 |
| 103 | False | disable_source | 0.85 | 371.43 |
| 104 | False | disable_source | 0.85 | 374.99 |
| 105 | False | disable_source | 0.85 | 378.50 |
| 106 | False | disable_source | 0.85 | 381.98 |
| 107 | False | disable_source | 0.85 | 385.47 |
| 108 | False | disable_source | 0.85 | 388.96 |
| 109 | False | disable_source | 0.85 | 392.43 |
| 110 | False | disable_source | 0.85 | 395.99 |
| 111 | False | disable_source | 0.85 | 399.53 |
| 112 | False | disable_source | 0.85 | 403.09 |
| 113 | False | disable_source | 0.85 | 406.65 |
| 114 | False | disable_source | 0.85 | 410.20 |
| 115 | False | disable_source | 0.85 | 413.76 |
| 116 | False | disable_source | 0.85 | 417.32 |
| 117 | False | disable_source | 0.85 | 420.87 |
| 118 | False | disable_source | 0.85 | 424.43 |
| 119 | False | disable_source | 0.85 | 427.99 |
| 120 | False | disable_source | 0.85 | 431.54 |
| 121 | False | disable_source | 0.85 | 435.10 |
| 122 | False | disable_source | 0.85 | 438.66 |
| 123 | False | disable_source | 0.85 | 442.22 |
| 124 | False | disable_source | 0.85 | 445.78 |
| 125 | False | disable_source | 0.85 | 449.34 |
| 126 | False | disable_source | 0.85 | 452.90 |
| 127 | False | disable_source | 0.85 | 456.44 |
| 128 | False | disable_source | 0.85 | 460.02 |
| 129 | False | disable_source | 0.85 | 463.60 |
| 130 | False | disable_source | 0.85 | 467.18 |
| 131 | False | disable_source | 0.85 | 470.76 |
| 132 | False | disable_source | 0.85 | 474.34 |
| 133 | False | disable_source | 0.85 | 477.91 |
| 134 | False | disable_source | 0.85 | 481.49 |
| 135 | False | disable_source | 0.85 | 485.07 |
| 136 | False | disable_source | 0.85 | 488.64 |
| 137 | False | disable_source | 0.85 | 492.21 |
| 138 | False | disable_source | 0.85 | 495.80 |
| 139 | False | disable_source | 0.85 | 499.37 |
| 140 | False | disable_source | 0.85 | 502.95 |
| 141 | False | disable_source | 0.85 | 506.54 |
| 142 | False | disable_source | 0.85 | 510.12 |
| 143 | False | disable_source | 0.85 | 513.69 |
| 144 | False | disable_source | 0.85 | 517.26 |
| 145 | False | disable_source | 0.85 | 520.84 |
| 146 | False | disable_source | 0.85 | 524.41 |
| 147 | False | disable_source | 0.85 | 527.99 |
| 148 | False | disable_source | 0.85 | 531.56 |
| 149 | False | disable_source | 0.85 | 535.13 |
| 150 | False | disable_source | 0.85 | 538.70 |
| 151 | False | disable_source | 0.85 | 542.29 |
| 152 | False | disable_source | 0.85 | 545.86 |
| 153 | False | disable_source | 0.85 | 549.44 |
| 154 | False | disable_source | 0.85 | 553.02 |
| 155 | False | disable_source | 0.85 | 556.59 |
| 156 | False | disable_source | 0.85 | 560.17 |
| 157 | False | disable_source | 0.85 | 563.74 |
| 158 | False | disable_source | 0.85 | 567.32 |
| 159 | False | disable_source | 0.85 | 570.90 |
| 160 | False | disable_source | 0.85 | 574.48 |
| 161 | False | disable_source | 0.85 | 578.05 |
| 162 | False | disable_source | 0.85 | 581.63 |
| 163 | False | disable_source | 0.85 | 585.21 |
| 164 | False | disable_source | 0.85 | 588.79 |
| 165 | False | disable_source | 0.85 | 592.37 |
| 166 | False | disable_source | 0.85 | 595.94 |
| 167 | False | disable_source | 0.85 | 599.51 |
| 168 | False | disable_source | 0.85 | 603.09 |
| 169 | False | disable_source | 0.85 | 606.67 |
| 170 | False | disable_source | 0.85 | 610.25 |
| 171 | False | disable_source | 0.85 | 613.83 |
| 172 | False | disable_source | 0.85 | 617.41 |
| 173 | False | disable_source | 0.85 | 620.99 |
| 174 | False | disable_source | 0.85 | 624.57 |
| 175 | False | disable_source | 0.85 | 628.15 |
| 176 | False | disable_source | 0.85 | 631.73 |
| 177 | False | disable_source | 0.85 | 635.30 |
| 178 | False | disable_source | 0.85 | 638.87 |
| 179 | False | disable_source | 0.85 | 642.45 |
| 180 | False | disable_source | 0.85 | 646.03 |
| 181 | False | disable_source | 0.85 | 649.61 |
| 182 | False | disable_source | 0.85 | 653.19 |
| 183 | False | disable_source | 0.85 | 656.76 |
| 184 | False | disable_source | 0.85 | 660.34 |
| 185 | False | disable_source | 0.85 | 663.92 |
| 186 | False | disable_source | 0.85 | 667.50 |
| 187 | False | disable_source | 0.85 | 671.07 |
| 188 | False | disable_source | 0.85 | 674.64 |
| 189 | False | disable_source | 0.85 | 678.22 |
| 190 | False | disable_source | 0.85 | 681.80 |
| 191 | False | disable_source | 0.85 | 685.37 |
| 192 | False | disable_source | 0.85 | 688.94 |
| 193 | False | disable_source | 0.85 | 692.52 |
| 194 | False | disable_source | 0.85 | 696.09 |
| 195 | False | disable_source | 0.85 | 699.65 |
| 196 | False | disable_source | 0.85 | 703.23 |
| 197 | False | disable_source | 0.85 | 706.80 |
| 198 | False | disable_source | 0.85 | 710.38 |
| 199 | False | disable_source | 0.85 | 713.96 |
| 200 | False | disable_source | 0.85 | 717.54 |
