"""Pull the league from ESPN and normalise it into SQLite (league.db).

    python3 ingest.py            # completed weeks from cache, the rest refetched
    python3 ingest.py --refresh  # refetch everything
    python3 ingest.py --light    # only refetch league status and the current week

The database is rebuilt from the JSON cache on every run (into a temp file that
is swapped in at the end), so it is always consistent with the cache.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

from espn_client import EspnClient, EspnError

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "league.db"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE teams (
    team_id INTEGER PRIMARY KEY, name TEXT, abbrev TEXT,
    wins INTEGER, losses INTEGER, ties INTEGER,
    points_for REAL, points_against REAL, owner_id TEXT
);
CREATE TABLE players (
    player_id INTEGER PRIMARY KEY, name TEXT, position_id INTEGER,
    pro_team_id INTEGER, on_team_id INTEGER, status TEXT,
    season_points REAL, season_avg REAL, adp REAL, pct_owned REAL
);
CREATE TABLE matchups (
    week INTEGER, matchup_id INTEGER,
    home_team_id INTEGER, home_points REAL,
    away_team_id INTEGER, away_points REAL,
    winner TEXT,
    home_live REAL, away_live REAL, home_proj REAL, away_proj REAL,
    PRIMARY KEY (week, matchup_id)
);
CREATE TABLE lineups (
    team_id INTEGER, week INTEGER, player_id INTEGER,
    slot_id INTEGER, points REAL, projected REAL, pro_team_id INTEGER,
    PRIMARY KEY (team_id, week, player_id)
);
CREATE TABLE draft_picks (
    overall_pick INTEGER PRIMARY KEY, round INTEGER, round_pick INTEGER,
    team_id INTEGER, player_id INTEGER, keeper INTEGER,
    bid_amount INTEGER, auto_draft_type_id INTEGER
);
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY, week INTEGER, type TEXT, status TEXT,
    team_id INTEGER, proposed_date INTEGER, items TEXT
);
CREATE TABLE trades (trade_id TEXT PRIMARY KEY, date INTEGER);
CREATE TABLE trade_items (
    trade_id TEXT, player_id INTEGER, from_team_id INTEGER, to_team_id INTEGER
);
CREATE TABLE pro_teams (pro_team_id INTEGER PRIMARY KEY, abbrev TEXT, bye_week INTEGER);
CREATE TABLE pro_games (pro_team_id INTEGER, week INTEGER, date INTEGER);
CREATE TABLE managers (owner_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT);
CREATE TABLE history_teams (
    season INTEGER, team_id INTEGER, owner_id TEXT, name TEXT,
    wins INTEGER, losses INTEGER, ties INTEGER, points_for REAL, points_against REAL,
    final_rank INTEGER, playoff_seed INTEGER, made_playoffs INTEGER,
    PRIMARY KEY (season, team_id)
);
CREATE TABLE history_games (
    season INTEGER, week INTEGER, tier TEXT,
    home_team_id INTEGER, home_points REAL, away_team_id INTEGER, away_points REAL
);
CREATE TABLE history_picks (
    season INTEGER, overall_pick INTEGER, round INTEGER, team_id INTEGER, player_id INTEGER
);
"""


def _load_side(conn: sqlite3.Connection, week: int, side: Optional[Dict[str, Any]]) -> None:
    if not side:
        return
    roster = side.get("rosterForCurrentScoringPeriod") or {}
    for entry in roster.get("entries", []):
        ppe = entry.get("playerPoolEntry") or {}
        player = ppe.get("player") or {}
        pid = entry.get("playerId")
        projected = None
        for s in player.get("stats") or []:
            if (s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1
                    and s.get("scoringPeriodId") == week):
                projected = s.get("appliedTotal")
        conn.execute(
            "INSERT OR IGNORE INTO players (player_id, name, position_id, pro_team_id) "
            "VALUES (?,?,?,?)",
            (pid, (player.get("fullName") or "").strip(),
             player.get("defaultPositionId"), player.get("proTeamId")))
        conn.execute("INSERT OR REPLACE INTO lineups VALUES (?,?,?,?,?,?,?)",
                     (side["teamId"], week, pid, entry.get("lineupSlotId"),
                      ppe.get("appliedStatTotal") or 0.0, projected,
                      player.get("proTeamId")))


def _upsert_player(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    if not p.get("name"):
        return
    conn.execute("INSERT OR REPLACE INTO players VALUES (?,?,?,?,?,?,?,?,?,?)", (
        p["id"], p["name"], p.get("positionId"), p.get("proTeamId"),
        p.get("onTeamId"), p.get("status"), p.get("seasonPoints"),
        p.get("seasonAvg"), p.get("adp"), p.get("pctOwned")))


def ingest(client: Any, db_path: Path = DB_PATH, refresh: bool = False,
           light: bool = False) -> Dict[str, int]:
    """refresh: refetch everything. light: refetch only league + current week."""
    league = client.league(refresh=True)
    status = league["status"]
    settings = league["settings"]
    current = int(status["currentMatchupPeriod"])
    reg_weeks = int(settings["scheduleSettings"]["matchupPeriodCount"])
    last_week = min(current, int(status.get("finalScoringPeriod") or current))
    slow = not light  # refetch the slower-moving data too

    tmp_path = Path(str(db_path) + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(str(tmp_path))
    conn.executescript(SCHEMA)

    meta = {
        "league_name": (settings.get("name") or "").strip(),
        "season": league.get("seasonId"),
        "current_week": current,
        "last_completed_week": current - 1,
        "regular_season_weeks": reg_weeks,
        "playoff_team_count": settings["scheduleSettings"].get("playoffTeamCount"),
        "lineup_slot_counts": json.dumps(
            settings.get("rosterSettings", {}).get("lineupSlotCounts", {})),
        "my_team_id": getattr(client, "my_team_id", None),
        "ingested_at": int(time.time() * 1000),
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)",
                     [(k, None if v is None else str(v)) for k, v in meta.items()])

    for t in league["teams"]:
        rec = (t.get("record") or {}).get("overall") or {}
        conn.execute("INSERT INTO teams VALUES (?,?,?,?,?,?,?,?,?)", (
            t["id"], (t.get("name") or "").strip(), (t.get("abbrev") or "").strip(),
            rec.get("wins"), rec.get("losses"), rec.get("ties"),
            rec.get("pointsFor"), rec.get("pointsAgainst"), t.get("primaryOwner")))
    for m in league.get("members", []):
        conn.execute("INSERT OR REPLACE INTO managers VALUES (?,?,?)",
                     (m.get("id"), m.get("firstName"), m.get("lastName")))

    wanted: Set[int] = set()  # player ids we need names / season totals for
    for week in range(1, last_week + 1):
        is_current = week >= current  # completed weeks never change
        data = client.matchups(week, refresh=refresh or is_current)
        for m in data.get("schedule", []):
            if m.get("matchupPeriodId") != week:
                continue
            home, away = m.get("home") or {}, m.get("away") or {}
            conn.execute("INSERT OR REPLACE INTO matchups VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                week, m.get("id"),
                home.get("teamId"), home.get("totalPoints"),
                away.get("teamId"), away.get("totalPoints"),
                m.get("winner"),
                home.get("totalPointsLive"), away.get("totalPointsLive"),
                home.get("totalProjectedPointsLive"), away.get("totalProjectedPointsLive")))
            _load_side(conn, week, home)
            _load_side(conn, week, away)

        tx = client.transactions(week, refresh=refresh or (is_current and slow))
        for t in tx.get("transactions", []) or []:
            items = t.get("items") or []
            wanted.update(i["playerId"] for i in items if i.get("playerId"))
            conn.execute("INSERT OR REPLACE INTO transactions VALUES (?,?,?,?,?,?,?)", (
                str(t.get("id")), week, t.get("type"), t.get("status"),
                t.get("teamId"), t.get("proposedDate"), json.dumps(items)))

    draft = client.draft(refresh=refresh)
    for p in (draft.get("draftDetail") or {}).get("picks", []):
        wanted.add(p.get("playerId"))
        conn.execute("INSERT OR REPLACE INTO draft_picks VALUES (?,?,?,?,?,?,?,?)", (
            p.get("overallPickNumber"), p.get("roundId"), p.get("roundPickNumber"),
            p.get("teamId"), p.get("playerId"), int(bool(p.get("keeper"))),
            p.get("bidAmount"), p.get("autoDraftTypeId")))

    for tr in client.trades(refresh=refresh or slow):
        conn.execute("INSERT OR REPLACE INTO trades VALUES (?,?)", (tr["id"], tr["date"]))
        for it in tr["items"]:
            wanted.add(it["playerId"])
            conn.execute("INSERT INTO trade_items VALUES (?,?,?,?)",
                         (tr["id"], it["playerId"], it["from"], it["to"]))

    for t in client.pro_schedule(refresh=refresh):
        conn.execute("INSERT OR REPLACE INTO pro_teams VALUES (?,?,?)",
                     (t["id"], t.get("abbrev"), t.get("byeWeek")))
        for wk, dates in (t.get("games") or {}).items():
            for d in dates:
                conn.execute("INSERT INTO pro_games VALUES (?,?,?)", (t["id"], int(wk), d))

    # Earlier seasons are finished, so they are fetched once and cached for good.
    for year in status.get("previousSeasons") or []:
        try:
            h = client.history(int(year))
        except EspnError:
            continue  # history is a nice-to-have; never block the current season
        for m in h["members"]:
            conn.execute("INSERT OR IGNORE INTO managers VALUES (?,?,?)",
                         (m["id"], m.get("firstName"), m.get("lastName")))
        cut = h.get("playoffTeamCount") or 0
        for t in h["teams"]:
            r = t["record"]
            seed = t.get("playoffSeed")
            conn.execute("INSERT OR REPLACE INTO history_teams VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                h["season"], t["id"], t.get("owner"), t["name"], r.get("wins"),
                r.get("losses"), r.get("ties"), r.get("pointsFor"), r.get("pointsAgainst"),
                t.get("finalRank"), seed, int(bool(seed and seed <= cut))))
        for g in h["games"]:
            conn.execute("INSERT INTO history_games VALUES (?,?,?,?,?,?,?)", (
                h["season"], g["week"], g["tier"], g["home"], g["homePoints"],
                g["away"], g["awayPoints"]))
        for p in h["picks"]:
            wanted.add(p["playerId"])
            conn.execute("INSERT INTO history_picks VALUES (?,?,?,?,?)", (
                h["season"], p["overall"], p["round"], p["teamId"], p["playerId"]))

    # Season totals: top scorers league-wide, plus anyone else we reference.
    top = client.players_top(refresh=refresh or slow)
    for p in top:
        _upsert_player(conn, p)
    wanted.update(r[0] for r in conn.execute("SELECT player_id FROM players"))
    wanted -= {p["id"] for p in top}
    wanted.discard(None)
    for p in client.players_by_ids(wanted, refresh=refresh or slow):
        _upsert_player(conn, p)

    conn.commit()
    counts = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
              for t in ("teams", "players", "matchups", "lineups", "draft_picks",
                        "transactions", "trades", "pro_games")}
    conn.close()
    os.replace(str(tmp_path), str(db_path))
    return counts


def main() -> int:
    try:
        counts = ingest(EspnClient.from_env(), refresh="--refresh" in sys.argv,
                        light="--light" in sys.argv)
    except EspnError as e:
        print("Ingest failed: %s" % e, file=sys.stderr)
        return 1
    for table, n in counts.items():
        print("%-13s %d rows" % (table, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
