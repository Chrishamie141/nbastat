# NFL game-market baseline v2

## Frozen v1 audit

`nfl_game_baseline_v1` accepts either at least four deduplicated completed-game
rows or the latest pregame aggregate for both teams. Every accepted timestamp
must precede kickoff. It averages season points scored with the opponent's
points allowed (22.5 points is the missing-defense fallback), then adds 0.75 to
home scoring and subtracts 0.75 from away scoring. Thus margin is home points
minus away points, total is their sum, and the total home-field adjustment is
1.5 points. H2H and spread probabilities use a normal CDF with 13.86 points of
margin deviation; totals use 14.5. Confidence starts at 55, increases with
history, and is capped at 75.

The weaknesses are equal weighting of old and recent games, schedule strength,
venue splits, rest, score variance, and team quality all being ignored. Fixed
distribution widths and a heuristic confidence score can also be poorly
calibrated. These behaviors remain available unchanged as v1 and are pinned by
a regression test.

## V2 architecture and configuration

`nfl_game_baseline_v2` adds exponential form (0.90 decay), a five-game recent
window blended at 30%, home/away splits shrunk by six prior games, and a
schedule adjustment comparing each performance with the opponent's pre-kickoff
scoring baselines. The score projection blends venue offense with opposing
venue defense. Its score distribution estimates team scoring deviation from
the eligible history with an eight-point stability floor.

Elo starts at 1500, uses K=20, 48 Elo points of home advantage, optional
margin-of-victory scaling, and regresses 33% toward 1500 when the season
changes. Elo contributes 35% of raw H2H probability; the score distribution
contributes 65%. All parameters are constructor configuration, not fitted to
the replay outcomes.

Market blending defaults to zero. The independent probability, no-vig market
consensus, and blended value remain distinct. Consensus lines/probabilities
use sportsbook medians, while the best executable American price remains a
separate quote. The existing two-percentage-point betting threshold is not
changed.

## Leakage and data limitations

V2 requires `completed_at` and `data_as_of` to be before target kickoff and
requires the observation to have become known no earlier than completion. It
rejects the target game and orders Elo updates chronologically. Opponent
baselines use only this same filtered pool. For 2025 Week 1 that means only
completed 2024 games may contribute.

The current canonical team-history schema has no turnovers, interceptions,
fumbles, plays, drives, EPA, success rate, injuries, or starting-quarterback
identity. No turnover feature is fabricated. Adding pregame quarterback and
injury availability plus play-level efficiency is the highest-value next data
enhancement; turnovers can then be added with strong regression to league
average.

## Week 1 comparison status

The supplied v1 replay is 96 candidates, 34 accepted and graded, 11 wins, 23
losses, zero pushes, and a 32.35% win rate. This checkout does not contain the
local Week 1 snapshot (`backtesting/data` contains only `.gitkeep`), so a valid
v2 replay, ROI, mean edge, Brier score, and log loss cannot be reported here.
The fixture bundle also lacks the 544 team-history rows. Inventing or fetching
those inputs would violate the offline and no-rebuild constraints. Therefore
the Week 1 comparison is inconclusive, and no overall accuracy improvement is
claimed. Once the unchanged private snapshot is mounted, select each version
through `BacktestConfig.model_version` and run the identical replay twice.
