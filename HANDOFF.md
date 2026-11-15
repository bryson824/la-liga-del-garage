# Fantasy Football Dashboard: Handoff Brief

Project brief for building a local dashboard for Bryson's ESPN fantasy football league.
Written 2026-11-15, during NFL week 10 of the 2026 season.

## Goal

A local Python project that pulls league data from ESPN's fantasy API and serves a
dashboard showing live scores, recent trades, and grades for draft picks and
lineup (bench) decisions, plus the analytics listed under "Features".

Read-only. Do not automate adds, drops, trades or lineup changes. The most this
project should do on that front is recommend moves for Bryson to make himself.

## League facts (verified against the live API)

| Item | Value |
|---|---|
| Platform | ESPN Fantasy (the league was formerly on NFL.com, which shut down fantasy for 2026) |
| League name | La Liga Del Garage |
| League ID | `1851736904` |
| Season | `2026` |
| Visibility | Private (unauthenticated requests return `AUTH_LEAGUE_NOT_VISIBLE`) |
| Teams | 12 |
| Bryson's team | teamId `2`, "God Adamit" |
| Scoring | Head-to-head points (`H2H_POINTS`) |
| Draft | Snake, 192 picks (16 rounds), held around Aug 30, 2026 (`draftSettings.date` = 1788141600000) |
| Waivers | `WAIVERS_TRADITIONAL` (API also reports `acquisitionBudget` 100) |
| Regular season | 14 matchup periods |
| Playoffs | 8 teams, final scoring period 17 |
| Earlier seasons on ESPN | 2023, 2024, 2025 (`status.previousSeasons`, imported history) |
| League URL | https://fantasy.espn.com/football/team?leagueId=1851736904&teamId=2&seasonId=2026 |

### Starting lineup

1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 D/ST, 1 K, 7 bench, 2 IR.

### Teams

| teamId | Name | Abbrev |
|---|---|---|
| 1 | Rex | jeff |
| 2 | God Adamit (Bryson) | ER |
| 3 | i hate brandon | 6969 |
| 4 | the special one | Jack |
| 5 | Obusseus | CMEN |
| 6 | Cal Fan Now | Oski |
| 7 | THE BROCKENING | HALL |
| 8 | Bjian MUUUUUUUUUSTAAAAAAARDson | Coli |
| 9 | CeeDee New Set👨💅 on a QB | Sam |
| 10 | Sacks And the City | SACK |
| 11 | Sloan | ZYN6 |
| 12 | The Price is Right | JPR |

Team names have trailing spaces and emoji in the raw data; trim and handle Unicode.

## Authentication

ESPN private leagues need two cookies from a logged-in browser session, sent on
every request:

- `espn_s2` (long session token)
- `SWID` (GUID in curly braces)

Store them in a `.env` file in the project root. Never commit it, never print the values.

```
ESPN_S2=...
ESPN_SWID={...}
LEAGUE_ID=1851736904
SEASON=2026
MY_TEAM_ID=2
```

To get them: in a browser logged in to ESPN, open DevTools, then
Application > Cookies > `https://fantasy.espn.com`, and copy `espn_s2` and `SWID`.
They are long-lived but expire eventually and are invalidated by logging out.
A 401 or `AUTH_LEAGUE_NOT_VISIBLE` response means they need refreshing.

Add `.env` to `.gitignore` before anything else.

## API

Unofficial and undocumented; it can change without notice. Keep all ESPN access
in one module so breakage is contained.

Base URL:

```
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/1851736904
```

Data is selected with repeated `view=` query parameters.

### Verified working (2026-11-15)

| Request | Returns |
|---|---|
| `?view=mSettings&view=mTeam&view=mStatus` | Top-level keys: `draftDetail`, `gameId`, `id`, `members`, `scoringPeriodId`, `seasonId`, `segmentId`, `settings`, `status`, `teams`. About 51 KB. |
| `?view=mMatchup&view=mMatchupScore&scoringPeriodId={week}` | `schedule[]` with each matchup's sides, scores and full rosters for that week |
| `?view=mDraftDetail` | `draftDetail.picks[]`, 192 entries |
| `?view=mTransactions2&scoringPeriodId={week}` | `transactions[]` for that week |

### Field paths used successfully

League and teams:
- `settings.name`, `settings.size`, `settings.isPublic`
- `settings.rosterSettings.lineupSlotCounts` (map of slot ID to count)
- `settings.scheduleSettings.matchupPeriodCount`, `.playoffTeamCount`
- `status.currentMatchupPeriod`, `status.latestScoringPeriod`, `status.finalScoringPeriod`
- `teams[].id`, `.name`, `.abbrev`
- `teams[].record.overall.wins`, `.losses`, `.ties`, `.pointsFor`, `.pointsAgainst`

Matchups (filter `schedule[]` to `matchupPeriodId == week`):
- `schedule[].home` and `.away` (`.away` can be missing on a bye), each with `.teamId`, `.totalPoints`
- `...rosterForCurrentScoringPeriod.entries[]`
  - `.lineupSlotId` (where the manager put the player)
  - `.playerId`
  - `.playerPoolEntry.appliedStatTotal` (fantasy points that week)
  - `.playerPoolEntry.player.fullName`
  - `.playerPoolEntry.player.defaultPositionId`

Draft:
- `draftDetail.picks[]`: `overallPickNumber`, `roundId`, `roundPickNumber`, `teamId`, `playerId`, `keeper`, `bidAmount`, `autoDraftTypeId`
- Picks carry only `playerId`; player names must be joined from roster or player data.

Transactions:
- `transactions[].type` and `.status`. Types seen in week 9: `FREEAGENT`, `ROSTER`, `FUTURE_ROSTER`, all `EXECUTED`.
- `ROSTER` and `FUTURE_ROSTER` are lineup moves, not acquisitions; filter them out of the activity feed.

### ID maps

Lineup slot IDs (`lineupSlotId`):

| ID | Slot |
|---|---|
| 0 | QB |
| 2 | RB |
| 4 | WR |
| 6 | TE |
| 16 | D/ST |
| 17 | K |
| 20 | Bench |
| 21 | IR |
| 23 | FLEX (RB/WR/TE) |

Player position IDs (`defaultPositionId`):

| ID | Position |
|---|---|
| 1 | QB |
| 2 | RB |
| 3 | WR |
| 4 | TE |
| 5 | K |
| 16 | D/ST |

### Not yet verified (check before relying on these)

- Trade transactions: none occurred in the week sampled, so the trade record shape is unconfirmed. Look for trade-type entries in `mTransactions2` across all weeks.
- Live in-game scoring for the current week: expected from `mMatchupScore` / `mScoreboard` on the current `scoringPeriodId`, polled about once a minute on game days.
- Projected points: believed to be in each player's `stats[]` with `statSourceId` 1 (projected) versus 0 (actual).
- Free agents / available players: believed to be `view=kona_player_info` with an `x-fantasy-filter` request header.
- Prior seasons (2023 to 2025): try the same URL with the season swapped; older seasons may need the `leagueHistory` endpoint instead.

### Alternative: `espn-api` package

The open-source `espn-api` Python package (github.com/cwendt94/espn-api) wraps
most of the above: `League(league_id, year, espn_s2, swid)` with box scores,
draft, recent activity and free agents. Reasonable to start with it and drop to
raw requests where it falls short. Confirm it supports the 2026 season before committing.

## Suggested architecture

1. `espn_client.py`: all HTTP calls, cookie auth, retries, raw JSON caching to disk.
2. `ingest.py`: normalise into SQLite tables: `teams`, `players`, `matchups`, `lineups` (team, week, player, slot, points), `draft_picks`, `transactions`.
3. `analytics.py`: pure functions over those tables, one per feature below.
4. Dashboard: a small local web app (Streamlit or FastAPI plus a static page) reading the database.
5. Scheduling: full refresh once after each week's games; a faster polling loop for live scores during game windows only.

Completed weeks never change, so cache them and only re-fetch the current week.

## Features, in build order

1. **Live matchups**: current week scores, players yet to play per side.
2. **Points left on bench**: actual lineup vs. best legal lineup in hindsight, per team per week, plus season totals and a "worst benching" list. Flag weeks that look like an unset lineup (for example, starters on bye or a very large gap) separately from ordinary bad calls.
3. **Draft value chart**: draft slot vs. current season rank at the position; label steals and busts.
4. **Luck / expected wins**: all-play record (each team vs. every other team each week) compared with the real record.
5. **Schedule swap**: each team's record if it had played another team's schedule.
6. **Waiver GM score**: points produced by players each manager added during the season.
7. **Trade ledger**: points scored by each side of every trade since it happened.
8. **Manager profiles**: tendencies drawn from draft and transaction history.
9. **Playoff odds**: simulate the remaining schedule several thousand times (8 of 12 make it).
10. **Weekly recap**: generated write-up with awards, formatted to paste into the group chat.
11. **Recommendations (advice only)**: ranked add/drop and start/sit suggestions for Bryson's team from free agents and projections.

## Validation fixture

Computed from the live API on 2026-11-15 for weeks 1 to 9 (week 10 in progress and excluded).
Use it to check `analytics.py`. Computed starter totals matched ESPN's `totalPoints`
for every team in every week (tolerance 0.5).

Optimal lineup method used: top 1 QB, 2 RB, 2 WR, 1 TE, 1 K, 1 D/ST by points from the
whole roster (including bench and IR), then the best remaining RB/WR/TE as FLEX.
Bench points lost = optimal minus actual starters.

| teamId | Team | Record | All-play | Expected wins | Bench points lost | Worst week (lost) | Top bench scorer that week |
|---|---|---|---|---|---|---|---|
| 1 | Rex | 6-3 | 53-46 | 4.8 | 196.4 | Wk 7 (48.5) | Tony Pollard 36.5 |
| 2 | God Adamit | 4-5 | 32-67 | 2.9 | 172.5 | Wk 4 (39.3) | Romeo Doubs 23.8 |
| 3 | i hate brandon | 5-4 | 61-38 | 5.5 | 109.3 | Wk 6 (19.4) | Germie Bernard 25.7 |
| 4 | the special one | 4-5 | 51-48 | 4.6 | 183.4 | Wk 1 (32.7) | Devaughn Vele 19.9 |
| 5 | Obusseus | 6-3 | 47-52 | 4.3 | 126.6 | Wk 9 (31.9) | KC Concepcion 18.8 |
| 6 | Cal Fan Now | 2-7 | 26-73 | 2.4 | 177.9 | Wk 2 (48.9) | Tyler Shough 22.4 |
| 7 | THE BROCKENING | 5-4 | 58-41 | 5.3 | 123.6 | Wk 6 (32.2) | Bryce Young 19.8 |
| 8 | Bjian MUUUUUUUUUSTAAAAAAARDson | 4-5 | 53-46 | 4.8 | 114.8 | Wk 5 (26.3) | Mike Evans 26.3 |
| 9 | CeeDee New Set👨💅 on a QB | 4-5 | 51-48 | 4.6 | 140.0 | Wk 4 (38.2) | RJ Harvey 19.3 |
| 10 | Sacks And the City | 3-6 | 30-69 | 2.7 | 286.8 | Wk 8 (84.1) | Joe Burrow 39.5 |
| 11 | Sloan | 6-3 | 62-37 | 5.6 | 161.5 | Wk 4 (32.4) | Kyle Monangai 28.0 |
| 12 | The Price is Right | 5-4 | 70-29 | 6.4 | 176.9 | Wk 8 (65.4) | Tyler Allgeier 36.1 |

Expected wins = all-play win rate x 9 games. Season points for and against at the
time: see `teams[].record.overall` (for example team 12: 1176.6 for, 1087.1 against).

Known limitation of the fixture: IR players were eligible in the optimal lineup,
which slightly overstates bench points where an IR player scored. Decide whether
the real version should exclude IR.

## Constraints

- Read-only against ESPN; no write actions on the account.
- Keep request volume low: cache completed weeks, poll live data no faster than about once a minute, and only during games.
- Cookies stay in `.env` on the local machine.
- Other managers' team names and results appear in the dashboard; it is for the league's own use.
