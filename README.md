# NBA Stat Predictor

## Manual injury filtering

You can manually exclude injured players by editing `injured_players.txt` in the project root.

Rules:
- One player name per line.
- Blank lines are ignored.
- Lines starting with `#` are ignored.
- Name matching is case-insensitive.
- Accent-insensitive matching is supported (for example `Luka Doncic` and `Luka Dončić` both match).

In `team_auto_roster` mode, injured players are marked as `OUT`, excluded from:
- team total calculations
- top 3 rankings
- highest stat rankings

An `OUT / EXCLUDED PLAYERS` section is always printed at the end.

In single-player mode, predictions still run, but a warning is printed if the player is listed as injured.
