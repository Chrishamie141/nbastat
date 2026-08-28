# Regular Season Week 1 feature readiness

This is an availability audit, not a model-tuning plan. Missing inputs must not
be replaced with invented calculations.

| Feature | Status | Current evidence / gap |
|---|---|---|
| Prior-season team strength | AVAILABLE | ESPN completed scores and frozen team history |
| QB changes | PARTIAL | Roster identities exist; verified starter-change feed is absent |
| Coaching changes | NEEDS_NEW_PROVIDER | No structured coaching-change feed |
| Roster movement | PARTIAL | Current roster snapshot; no normalized transaction timeline |
| Offensive efficiency | PARTIAL | Points and basic ESPN box-score fields; no complete efficiency feed |
| Defensive efficiency | PARTIAL | Points allowed and basic ESPN fields |
| EPA | NEEDS_NEW_PROVIDER | No verified EPA feed configured |
| Success rate | NEEDS_NEW_PROVIDER | No verified play-level success-rate feed configured |
| Sacks / pressure | PARTIAL | ESPN sacks are available; pressure rate is unavailable |
| Turnovers | PARTIAL | Basic ESPN team box-score turnovers when populated |
| Home-field advantage | AVAILABLE | Schedule venue and home/away role |
| Injuries | NEEDS_NEW_PROVIDER | Configured ESPN adapter returns no verified injuries |
| Weather | PARTIAL | OpenWeather current conditions; game-time forecast normalization incomplete |
| Rest / travel | PARTIAL | Rest days available; travel distance/time-zone effects unavailable |
| Sportsbook movement | AVAILABLE | Append-only Odds API observations when provider access is healthy |

The largest gaps before any prospective Week 1 model expansion are verified
personnel/injury data, EPA/success-rate data, coaching/transaction events,
pressure metrics, and normalized game-time weather/travel inputs.
