"""Pure functions over league.db. No network access."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

BENCH, IR, FLEX = 20, 21, 23

# lineup slot id -> player position ids allowed in it
SLOT_POSITIONS: Dict[int, Tuple[int, ...]] = {
    0: (1,),        # QB
    2: (2,),        # RB
    4: (3,),        # WR
    6: (4,),        # TE
    16: (16,),      # D/ST
    17: (5,),       # K
    FLEX: (2, 3, 4),
}
DEFAULT_SLOT_COUNTS = {0: 1, 2: 2, 4: 2, 6: 1, FLEX: 1, 16: 1, 17: 1}


def _meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def last_completed_week(conn: sqlite3.Connection) -> int:
    return int(_meta(conn, "last_completed_week") or 0)


def starting_slots(conn: sqlite3.Connection) -> Dict[int, int]:
    """Starting slot counts from league settings (bench/IR/unused slots dropped)."""
    raw = _meta(conn, "lineup_slot_counts")
    counts = {int(k): int(v) for k, v in json.loads(raw).items()} if raw else {}
    slots = {s: n for s, n in counts.items() if n > 0 and s in SLOT_POSITIONS}
    return slots or dict(DEFAULT_SLOT_COUNTS)


def optimal_points(players: List[Tuple[int, float]], slots: Dict[int, int]) -> float:
    """Best legal lineup total. players = [(position_id, points)].

    Dedicated slots are filled with the top scorers at each position, then FLEX
    takes the best remaining RB/WR/TE. Greedy is exact for this slot structure.
    """
    remaining = sorted(players, key=lambda p: p[1], reverse=True)
    total = 0.0
    for slot in sorted(slots, key=lambda s: s == FLEX):  # FLEX last
        for _ in range(slots[slot]):
            for i, (pos, pts) in enumerate(remaining):
                if pos in SLOT_POSITIONS[slot]:
                    total += pts
                    del remaining[i]
                    break
    return total


def bench_weeks(conn: sqlite3.Connection, through_week: Optional[int] = None,
                include_ir: bool = False) -> List[Dict[str, Any]]:
    """One row per team per week: actual starters vs best lineup in hindsight."""
    through_week = through_week or last_completed_week(conn)
    slots = starting_slots(conn)
    rows = conn.execute(
        """SELECT l.team_id, l.week, l.slot_id, l.points, p.position_id, p.name
           FROM lineups l JOIN players p USING (player_id)
           WHERE l.week <= ? ORDER BY l.team_id, l.week""", (through_week,)).fetchall()
    grouped: Dict[Tuple[int, int], List[Tuple]] = {}
    for r in rows:
        grouped.setdefault((r[0], r[1]), []).append(r[2:])

    out = []
    for (team_id, week), entries in sorted(grouped.items()):
        actual = sum(pts for slot, pts, _, _ in entries if slot not in (BENCH, IR))
        pool = [(pos, pts) for slot, pts, pos, _ in entries
                if include_ir or slot != IR]
        sat = [(pts, name) for slot, pts, _, name in entries
               if slot == BENCH or (include_ir and slot == IR)]
        top = max(sat) if sat else (0.0, None)
        optimal = optimal_points(pool, slots)
        out.append({"team_id": team_id, "week": week, "actual": actual,
                    "optimal": optimal, "lost": optimal - actual,
                    "top_bench_name": top[1], "top_bench_points": top[0]})
    return out


def bench_summary(conn: sqlite3.Connection, through_week: Optional[int] = None,
                  include_ir: bool = False) -> Dict[int, Dict[str, Any]]:
    """Season total lost per team, plus that team's worst week."""
    out: Dict[int, Dict[str, Any]] = {}
    for w in bench_weeks(conn, through_week, include_ir):
        s = out.setdefault(w["team_id"], {"total_lost": 0.0, "worst": None})
        s["total_lost"] += w["lost"]
        if s["worst"] is None or w["lost"] > s["worst"]["lost"]:
            s["worst"] = w
    return out


def weekly_scores(conn: sqlite3.Connection, through_week: Optional[int] = None
                  ) -> Dict[int, Dict[int, float]]:
    """{week: {team_id: points}} from ESPN's matchup totals."""
    through_week = through_week or last_completed_week(conn)
    scores: Dict[int, Dict[int, float]] = {}
    for week, h, hp, a, ap in conn.execute(
            """SELECT week, home_team_id, home_points, away_team_id, away_points
               FROM matchups WHERE week <= ?""", (through_week,)):
        for team, pts in ((h, hp), (a, ap)):
            if team is not None:
                scores.setdefault(week, {})[team] = pts or 0.0
    return scores


def records(conn: sqlite3.Connection, through_week: Optional[int] = None
            ) -> Dict[int, List[int]]:
    """Actual head-to-head [wins, losses, ties] per team."""
    through_week = through_week or last_completed_week(conn)
    rec: Dict[int, List[int]] = {}
    for h, hp, a, ap in conn.execute(
            """SELECT home_team_id, home_points, away_team_id, away_points
               FROM matchups WHERE week <= ?""", (through_week,)):
        if h is None or a is None:
            continue
        rh, ra = rec.setdefault(h, [0, 0, 0]), rec.setdefault(a, [0, 0, 0])
        if hp > ap:
            rh[0] += 1; ra[1] += 1
        elif ap > hp:
            ra[0] += 1; rh[1] += 1
        else:
            rh[2] += 1; ra[2] += 1
    return rec


def all_play(conn: sqlite3.Connection, through_week: Optional[int] = None
             ) -> Dict[int, Dict[str, Any]]:
    """Each team vs every other team every week, and the expected wins that implies."""
    out: Dict[int, Dict[str, Any]] = {}
    for week, scores in weekly_scores(conn, through_week).items():
        for team, pts in scores.items():
            s = out.setdefault(team, {"wins": 0, "losses": 0, "ties": 0, "games": 0})
            s["games"] += 1
            for other, opts in scores.items():
                if other == team:
                    continue
                s["wins" if pts > opts else "losses" if pts < opts else "ties"] += 1
    for s in out.values():
        n = s["wins"] + s["losses"] + s["ties"]
        rate = (s["wins"] + 0.5 * s["ties"]) / n if n else 0.0
        s["win_rate"] = rate
        s["expected_wins"] = rate * s["games"]
    return out


# ---------------------------------------------------------------------------
# Dashboard analytics
# ---------------------------------------------------------------------------

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 20: "Bench",
        21: "IR", 23: "FLEX"}


def _players(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    cur = conn.execute("SELECT player_id, name, position_id, pro_team_id, on_team_id, "
                       "status, season_points, season_avg, adp FROM players")
    cols = [c[0] for c in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur}


def bench_detail(conn: sqlite3.Connection, through_week: Optional[int] = None
                 ) -> Dict[str, Any]:
    """IR-excluded bench report with what-if results.

    For every team-week: the points left on the bench, whether the best lineup
    would have beaten the opponent's real lineup, and whether it would have
    beaten the opponent's own best lineup. Plus the costliest single sit/start
    call each week and whether that one swap would have flipped the game.
    """
    through_week = through_week or last_completed_week(conn)
    slots = starting_slots(conn)
    rows = conn.execute(
        """SELECT l.team_id, l.week, l.slot_id, l.points, p.position_id, p.name
           FROM lineups l JOIN players p USING (player_id)
           WHERE l.week <= ?""", (through_week,)).fetchall()
    grouped: Dict[Tuple[int, int], List[Tuple]] = {}
    for r in rows:
        grouped.setdefault((r[0], r[1]), []).append(r[2:])
    opp: Dict[Tuple[int, int], int] = {}
    for week, h, a in conn.execute(
            "SELECT week, home_team_id, away_team_id FROM matchups WHERE week <= ?",
            (through_week,)):
        if h is not None and a is not None:
            opp[(h, week)], opp[(a, week)] = a, h

    weeks: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for (team_id, week), entries in sorted(grouped.items()):
        starters = [e for e in entries if e[0] not in (BENCH, IR)]
        bench = [e for e in entries if e[0] == BENCH]
        actual = sum(e[1] for e in starters)
        optimal = optimal_points([(e[2], e[1]) for e in starters + bench], slots)
        filled: Dict[int, int] = {}
        for e in starters:
            filled[e[0]] = filled.get(e[0], 0) + 1
        empty = [s for s, n in slots.items() for _ in range(n - filled.get(s, 0))]
        # Costliest single call: a bench player vs the weakest starter he could replace.
        worst = None
        targets = [(e[0], e[1], e[3]) for e in starters] + [(s, 0.0, "(empty slot)") for s in empty]
        for bp in bench:
            options = [t for t in targets if bp[2] in SLOT_POSITIONS.get(t[0], ())]
            if not options:
                continue
            t = min(options, key=lambda x: x[1])
            gain = bp[1] - t[1]
            if gain > 0 and (worst is None or gain > worst["gain"]):
                worst = {"sat": bp[3], "sat_points": bp[1], "started": t[2],
                         "started_points": t[1], "gain": gain}
        weeks[(team_id, week)] = {"team_id": team_id, "week": week, "actual": actual,
                                  "optimal": optimal, "lost": optimal - actual,
                                  "worst_call": worst}

    def outcome(mine: float, theirs: float) -> str:
        return "W" if mine > theirs else "L" if mine < theirs else "T"
    for (team_id, week), w in weeks.items():
        o = weeks.get((opp.get((team_id, week)), week))
        if not o:
            continue
        w["opp_id"] = o["team_id"]
        w["opp_actual"], w["opp_optimal"] = o["actual"], o["optimal"]
        w["result"] = outcome(w["actual"], o["actual"])
        w["perfect_result"] = outcome(w["optimal"], o["actual"])
        w["ideal_result"] = outcome(w["optimal"], o["optimal"])
        w["margin"] = w["actual"] - o["actual"]
        w["perfect_margin"] = w["optimal"] - o["actual"]
        w["ideal_margin"] = w["optimal"] - o["optimal"]
        w["avoidable_loss"] = w["result"] == "L" and w["perfect_result"] == "W"
        if w["worst_call"]:
            w["worst_call"]["flips"] = (w["result"] == "L"
                                        and w["actual"] + w["worst_call"]["gain"] > o["actual"])

    def tally(rec: List[int], res: Optional[str]) -> None:
        if res:
            rec["WLT".index(res)] += 1
    teams: Dict[int, Dict[str, Any]] = {}
    for w in weeks.values():
        t = teams.setdefault(w["team_id"], {
            "team_id": w["team_id"], "total_lost": 0.0, "actual": 0.0, "optimal": 0.0,
            "record": [0, 0, 0], "perfect_record": [0, 0, 0], "ideal_record": [0, 0, 0],
            "avoidable_losses": 0, "one_call_losses": 0})
        t["total_lost"] += w["lost"]
        t["actual"] += w["actual"]
        t["optimal"] += w["optimal"]
        tally(t["record"], w.get("result"))
        tally(t["perfect_record"], w.get("perfect_result"))
        tally(t["ideal_record"], w.get("ideal_result"))
        t["avoidable_losses"] += bool(w.get("avoidable_loss"))
        t["one_call_losses"] += bool(w["worst_call"] and w["worst_call"].get("flips"))
    for t in teams.values():
        t["efficiency"] = t["actual"] / t["optimal"] if t["optimal"] else None
    calls = sorted((dict(w["worst_call"], team_id=w["team_id"], week=w["week"],
                         result=w.get("result"), margin=w.get("margin"),
                         opp_id=w.get("opp_id"))
                    for w in weeks.values() if w["worst_call"]),
                   key=lambda c: -c["gain"])
    flips = [c for c in calls if c.get("flips")]
    return {"weeks": list(weeks.values()),
            "teams": sorted(teams.values(), key=lambda t: -t["total_lost"]),
            "worst_calls": calls[:20], "game_losing_calls": flips}


def schedule_swap(conn: sqlite3.Connection, through_week: Optional[int] = None
                  ) -> Dict[int, Dict[int, int]]:
    """wins[a][b] = team a's wins had it played team b's schedule."""
    through_week = through_week or last_completed_week(conn)
    scores = weekly_scores(conn, through_week)
    opp: Dict[int, Dict[int, int]] = {}
    for week, h, a in conn.execute(
            "SELECT week, home_team_id, away_team_id FROM matchups WHERE week <= ?",
            (through_week,)):
        if h is not None and a is not None:
            opp.setdefault(week, {})[h] = a
            opp[week][a] = h
    teams = sorted({t for w in scores.values() for t in w})
    out: Dict[int, Dict[int, int]] = {a: {b: 0 for b in teams} for a in teams}
    for week, sc in scores.items():
        for a in teams:
            for b in teams:
                o = opp.get(week, {}).get(b)
                if o is None or a not in sc:
                    continue
                if o == a:  # b's opponent that week was a: a plays b instead
                    o = b
                if sc[a] > sc.get(o, 0.0):
                    out[a][b] += 1
    return out


def draft_analysis(conn: sqlite3.Connection, through_week: Optional[int] = None
                   ) -> Dict[str, Any]:
    """Draft value across positions, the redraft, and who was passed over.

    Everything is measured in points over replacement: a player's season points
    minus those of a replacement-level starter at his position (QB12, RB30,
    WR30, TE12, K12, D/ST12 in a 12-team league with this lineup). That puts a
    running back and a receiver on the same scale. A pick's value is its points
    over replacement (never below zero: a dud is simply replaced) minus the
    average return of the picks made within half a round either side of it.
    """
    through_week = through_week or last_completed_week(conn)
    players = _players(conn)
    by_pos: Dict[int, List[Dict[str, Any]]] = {}
    for p in players.values():
        if p["position_id"] in POS and p["season_points"] is not None:
            by_pos.setdefault(p["position_id"], []).append(p)
    finish: Dict[int, int] = {}
    for plist in by_pos.values():
        plist.sort(key=lambda p: -p["season_points"])
        for i, p in enumerate(plist):
            finish[p["player_id"]] = i + 1

    n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] or 12
    slots = starting_slots(conn)
    repl: Dict[int, float] = {}
    repl_rank: Dict[str, int] = {}
    for pos_id, slot_id in ((1, 0), (2, 2), (3, 4), (4, 6), (5, 17), (16, 16)):
        n = n_teams * slots.get(slot_id, 0)
        if pos_id in (2, 3):
            n += n_teams * slots.get(FLEX, 0) // 2
        ranked = by_pos.get(pos_id, [])
        repl[pos_id] = ranked[min(n, len(ranked)) - 1]["season_points"] if ranked and n else 0.0
        repl_rank[POS[pos_id]] = n

    def over(p: Dict[str, Any]) -> float:  # raw, can be negative
        return (p.get("season_points") or 0.0) - repl.get(p.get("position_id"), 0.0)

    # Consensus order: rank by ESPN ADP rather than the raw number, so league
    # size does not matter. ESPN parks rarely drafted players at about 170.
    real_adp = sorted((p for p in players.values() if p["adp"] and 0 < p["adp"] < 165),
                      key=lambda p: p["adp"])
    adp_rank = {p["player_id"]: i + 1 for i, p in enumerate(real_adp)}

    board = sorted((p for plist in by_pos.values() for p in plist), key=lambda p: -over(p))
    redraft_pick = {p["player_id"]: i + 1 for i, p in enumerate(board)}

    picks = []
    drafted_order: Dict[int, int] = {}
    for overall, rnd, team_id, pid in conn.execute(
            "SELECT overall_pick, round, team_id, player_id FROM draft_picks ORDER BY overall_pick"):
        p = players.get(pid, {})
        pos = p.get("position_id")
        drafted_order[pos] = drafted_order.get(pos, 0) + 1
        rank = adp_rank.get(pid)
        if rank is not None:
            vs_adp = overall - rank
        else:  # consensus says undrafted: only a reach if taken before the consensus ran out
            vs_adp = min(0, overall - (len(real_adp) + 1))
        picks.append({"pick": overall, "round": rnd, "team_id": team_id, "player_id": pid,
                      "name": p.get("name") or "Player %s" % pid, "pos": POS.get(pos, "?"),
                      "pos_drafted": drafted_order[pos], "pos_finish": finish.get(pid),
                      "points": p.get("season_points") or 0.0,
                      "over": max(0.0, over(p)) if pos in POS else 0.0,
                      "redraft_pick": redraft_pick.get(pid),
                      "adp_rank": rank, "vs_adp": vs_adp,
                      "on_team_id": p.get("on_team_id")})
    half = n_teams // 2
    threshold = 4.0 * max(1, through_week)
    for pk in picks:
        near = [o["over"] for o in picks if abs(o["pick"] - pk["pick"]) <= half]
        pk["expected"] = sum(near) / len(near)
        pk["value"] = pk["over"] - pk["expected"]
        pk["label"] = ("steal" if pk["value"] >= threshold
                       else "bust" if pk["value"] <= -threshold else "")

    # Passed over: anyone, at any position, taken before this team picked again.
    by_team: Dict[int, List[Dict[str, Any]]] = {}
    for pk in picks:
        by_team.setdefault(pk["team_id"], []).append(pk)
    for pk in picks:
        later_own = [o["pick"] for o in by_team[pk["team_id"]] if o["pick"] > pk["pick"]]
        next_own = min(later_own) if later_own else 10 ** 6
        window = [o for o in picks if pk["pick"] < o["pick"] < next_own and o["over"] > pk["over"]]
        best = max(window, key=lambda o: o["over"]) if window else None
        pk["better_in_window"] = len(window)
        pk["passed"] = best and {"name": best["name"], "pos": best["pos"], "pick": best["pick"],
                                 "team_id": best["team_id"], "points": best["points"],
                                 "over": best["over"], "gap": best["over"] - pk["over"]}

    teams = []
    for t, tp in by_team.items():
        rs = [p for p in tp if p["vs_adp"] <= -n_teams]
        bs = [p for p in tp if p["vs_adp"] >= n_teams]
        best, worst = max(tp, key=lambda p: p["value"]), min(tp, key=lambda p: p["value"])
        teams.append({
            "team_id": t, "value": sum(p["value"] for p in tp),
            "over": sum(p["over"] for p in tp),
            "steals": sum(1 for p in tp if p["label"] == "steal"),
            "busts": sum(1 for p in tp if p["label"] == "bust"),
            "best": {"name": best["name"], "pick": best["pick"], "value": best["value"]},
            "worst": {"name": worst["name"], "pick": worst["pick"], "value": worst["value"]},
            "avg_vs_adp": sum(p["vs_adp"] for p in tp) / len(tp),
            "reaches": len(rs), "bargains": len(bs),
            "reach_value": sum(p["value"] for p in rs) / len(rs) if rs else None,
            "bargain_value": sum(p["value"] for p in bs) / len(bs) if bs else None,
            "best_reach": max(rs, key=lambda p: p["value"])["name"] if rs else None,
            "worst_reach": min(rs, key=lambda p: p["value"])["name"] if rs else None})
    teams.sort(key=lambda t: -t["value"])
    for i, t in enumerate(teams):
        t["rank"] = i + 1

    actual = {pk["player_id"]: pk for pk in picks}
    redraft = [{"redraft_pick": i + 1, "name": p["name"], "pos": POS[p["position_id"]],
                "pos_finish": finish.get(p["player_id"]), "points": p["season_points"],
                "over": over(p),
                "actual_pick": (actual.get(p["player_id"]) or {}).get("pick"),
                "team_id": (actual.get(p["player_id"]) or {}).get("team_id")}
               for i, p in enumerate(board[:len(picks)])]
    regrets = sorted((dict(pk["passed"], team_id=pk["team_id"], took=pk["name"],
                           took_pos=pk["pos"], took_pick=pk["pick"],
                           took_points=pk["points"], taken_by=pk["passed"]["team_id"])
                      for pk in picks if pk["passed"]), key=lambda r: -r["gap"])[:20]
    drafted_ids = set(actual)
    undrafted = sorted((p for p in players.values()
                        if p["player_id"] not in drafted_ids and p["position_id"] in POS
                        and over(p) > 0), key=lambda p: -over(p))[:15]
    return {"picks": picks, "teams": teams, "regrets": regrets, "redraft": redraft,
            "replacement": {POS[k]: {"rank": repl_rank[POS[k]], "points": v}
                            for k, v in repl.items()},
            "label_threshold": threshold,
            "undrafted": [{"name": p["name"], "pos": POS[p["position_id"]],
                           "points": p["season_points"], "over": over(p),
                           "on_team_id": p["on_team_id"],
                           "pos_finish": finish.get(p["player_id"])} for p in undrafted]}


def _week_starts(conn: sqlite3.Connection) -> Dict[int, int]:
    return dict(conn.execute("SELECT week, MIN(date) FROM pro_games GROUP BY week"))


def trade_ledger(conn: sqlite3.Connection, through_week: Optional[int] = None
                 ) -> List[Dict[str, Any]]:
    """Points scored by each side's incoming players since the trade."""
    through_week = through_week or last_completed_week(conn)
    players = _players(conn)
    starts = _week_starts(conn)
    out = []
    for trade_id, date in conn.execute("SELECT trade_id, date FROM trades ORDER BY date DESC"):
        later = [w for w, d in starts.items() if d and d > date]
        first_week = min(later) if later else through_week + 1
        sides: Dict[int, Dict[str, Any]] = {}
        for pid, frm, to in conn.execute(
                "SELECT player_id, from_team_id, to_team_id FROM trade_items WHERE trade_id=?",
                (trade_id,)):
            pts, started = conn.execute(
                """SELECT COALESCE(SUM(points),0),
                          COALESCE(SUM(CASE WHEN slot_id NOT IN (20,21) AND team_id=? THEN points END),0)
                   FROM lineups WHERE player_id=? AND week BETWEEN ? AND ?""",
                (to, pid, first_week, through_week)).fetchone()
            side = sides.setdefault(to, {"team_id": to, "players": [], "points": 0.0,
                                         "started_points": 0.0})
            side["players"].append({"name": (players.get(pid) or {}).get("name") or str(pid),
                                    "pos": POS.get((players.get(pid) or {}).get("position_id"), "?"),
                                    "points": pts, "started_points": started})
            side["points"] += pts
            side["started_points"] += started
            sides.setdefault(frm, {"team_id": frm, "players": [], "points": 0.0,
                                   "started_points": 0.0})
        out.append({"trade_id": trade_id, "date": date, "first_week": first_week,
                    "weeks_counted": max(0, through_week - first_week + 1),
                    "sides": list(sides.values())})
    return out


def _moves(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Executed adds and drops (waiver + free agent)."""
    out = []
    for week, typ, date, items in conn.execute(
            """SELECT week, type, proposed_date, items FROM transactions
               WHERE type IN ('FREEAGENT','WAIVER') AND status='EXECUTED'
               ORDER BY proposed_date"""):
        adds, drops, team = [], [], None
        for it in json.loads(items or "[]"):
            if it.get("type") == "ADD":
                adds.append(it.get("playerId"))
                team = it.get("toTeamId")
            elif it.get("type") == "DROP":
                drops.append(it.get("playerId"))
                team = team or it.get("fromTeamId")
        if team:
            out.append({"week": week, "type": typ, "date": date, "team_id": team,
                        "adds": adds, "drops": drops})
    return out


def waiver_scores(conn: sqlite3.Connection, through_week: Optional[int] = None
                  ) -> List[Dict[str, Any]]:
    """Points produced, for the adding team, by players picked up in season."""
    through_week = through_week or last_completed_week(conn)
    players = _players(conn)
    first_add: Dict[Tuple[int, int], int] = {}
    adds_count: Dict[int, int] = {}
    for m in _moves(conn):
        for pid in m["adds"]:
            adds_count[m["team_id"]] = adds_count.get(m["team_id"], 0) + 1
            first_add.setdefault((m["team_id"], pid), m["week"])
    teams: Dict[int, Dict[str, Any]] = {}
    for (team_id, pid), week in first_add.items():
        total, started = conn.execute(
            """SELECT COALESCE(SUM(points),0),
                      COALESCE(SUM(CASE WHEN slot_id NOT IN (20,21) THEN points END),0)
               FROM lineups WHERE team_id=? AND player_id=? AND week BETWEEN ? AND ?""",
            (team_id, pid, week, through_week)).fetchone()
        t = teams.setdefault(team_id, {"team_id": team_id, "started_points": 0.0,
                                       "roster_points": 0.0, "best": None})
        t["started_points"] += started
        t["roster_points"] += total
        if started > 0 and (t["best"] is None or started > t["best"]["started_points"]):
            t["best"] = {"name": (players.get(pid) or {}).get("name") or str(pid),
                         "started_points": started, "week": week}
    for team_id, t in teams.items():
        t["adds"] = adds_count.get(team_id, 0)
    return sorted(teams.values(), key=lambda t: -t["started_points"])


def activity(conn: sqlite3.Connection, limit: int = 40) -> List[Dict[str, Any]]:
    players = _players(conn)

    def nm(pid: int) -> str:
        return (players.get(pid) or {}).get("name") or "Player %s" % pid
    feed = [{"date": m["date"], "week": m["week"], "team_id": m["team_id"],
             "kind": "Waiver" if m["type"] == "WAIVER" else "Free agent",
             "adds": [nm(p) for p in m["adds"]], "drops": [nm(p) for p in m["drops"]]}
            for m in _moves(conn)]
    return sorted(feed, key=lambda f: -(f["date"] or 0))[:limit]


def live(conn: sqlite3.Connection, now_ms: int) -> Dict[str, Any]:
    """Current week matchups with per-player status."""
    week = int(_meta(conn, "current_week") or 0)
    games: Dict[int, int] = dict(conn.execute(
        "SELECT pro_team_id, MIN(date) FROM pro_games WHERE week=? GROUP BY pro_team_id",
        (week,)))
    rosters: Dict[int, List[Dict[str, Any]]] = {}
    for team_id, slot, pts, proj, pro, name, pos in conn.execute(
            """SELECT l.team_id, l.slot_id, l.points, l.projected, l.pro_team_id, p.name,
                      p.position_id
               FROM lineups l JOIN players p USING (player_id) WHERE l.week=?""", (week,)):
        kickoff = games.get(pro)
        if kickoff is None:
            state = "bye"
        elif kickoff > now_ms:
            state = "upcoming"
        elif now_ms < kickoff + int(3.75 * 3600 * 1000):
            state = "playing"
        else:
            state = "done"
        rosters.setdefault(team_id, []).append({
            "name": name, "pos": POS.get(pos, "?"), "slot": SLOT.get(slot, str(slot)),
            "slot_id": slot, "points": pts, "projected": proj, "state": state,
            "kickoff": kickoff})
    order = {0: 0, 2: 1, 4: 2, 6: 3, 23: 4, 16: 5, 17: 6, 20: 7, 21: 8}

    def side(team_id: Optional[int], final: float, live_pts: float, proj: float
             ) -> Optional[Dict[str, Any]]:
        if team_id is None:
            return None
        roster = sorted(rosters.get(team_id, []),
                        key=lambda r: (order.get(r["slot_id"], 9), -(r["projected"] or 0)))
        starters = [r for r in roster if r["slot_id"] not in (BENCH, IR)]
        return {"team_id": team_id, "points": live_pts if live_pts else (final or 0.0),
                "projected": proj,
                "yet_to_play": sum(1 for r in starters if r["state"] == "upcoming"),
                "playing": sum(1 for r in starters if r["state"] == "playing"),
                "roster": roster}
    matchups = []
    for h, hp, a, ap, hl, al, hj, aj, winner in conn.execute(
            """SELECT home_team_id, home_points, away_team_id, away_points, home_live,
                      away_live, home_proj, away_proj, winner
               FROM matchups WHERE week=? ORDER BY matchup_id""", (week,)):
        matchups.append({"home": side(h, hp, hl, hj), "away": side(a, ap, al, aj),
                         "winner": winner})
    in_window = any(d <= now_ms < d + int(4.5 * 3600 * 1000) for d in games.values() if d)
    return {"week": week, "matchups": matchups, "games_in_progress": in_window}


def drop_regret(conn: sqlite3.Connection, through_week: Optional[int] = None
                ) -> List[Dict[str, Any]]:
    """Players a manager cut who then produced in someone else's starting lineup,
    set against what that manager actually started in the same weeks.

    For each week the dropped player was started elsewhere, he is compared with
    the weakest starter on the dropping team that he could have replaced (same
    position slot or FLEX; an empty slot counts as zero).
    """
    through_week = through_week or last_completed_week(conn)
    players = _players(conn)
    slots = starting_slots(conn)
    starters: Dict[Tuple[int, int], List[Tuple[int, float]]] = {}
    for team_id, week, slot, pts in conn.execute(
            "SELECT team_id, week, slot_id, points FROM lineups WHERE slot_id NOT IN (20,21)"):
        starters.setdefault((team_id, week), []).append((slot, pts))
    seen = set()
    out = []
    for mv in _moves(conn):
        for pid in mv["drops"]:
            if (mv["team_id"], pid) in seen:
                continue
            seen.add((mv["team_id"], pid))
            p = players.get(pid) or {}
            pos = p.get("position_id")
            rows = conn.execute(
                """SELECT week, team_id, points FROM lineups
                   WHERE player_id=? AND team_id<>? AND slot_id NOT IN (20,21)
                     AND week BETWEEN ? AND ?""",
                (pid, mv["team_id"], mv["week"], through_week)).fetchall()
            if not rows:
                continue
            his = mine = 0.0
            for week, _, pts in rows:
                mine_wk = starters.get((mv["team_id"], week), [])
                options = [x[1] for x in mine_wk if pos in SLOT_POSITIONS.get(x[0], ())]
                want = sum(n for sl, n in slots.items() if pos in SLOT_POSITIONS.get(sl, ()))
                if len(options) < want:
                    options.append(0.0)
                his += pts
                mine += min(options) if options else 0.0
            by_team: Dict[int, float] = {}
            for _, tid, pts in rows:
                by_team[tid] = by_team.get(tid, 0.0) + pts
            out.append({"team_id": mv["team_id"], "week": mv["week"],
                        "name": p.get("name") or str(pid), "pos": POS.get(pos, "?"),
                        "weeks": len(rows), "started_points": his,
                        "own_points": mine, "net": his - mine,
                        "beneficiary_id": max(by_team, key=lambda t: by_team[t])})
    return sorted(out, key=lambda d: -d["net"])


def history(conn: sqlite3.Connection, through_week: Optional[int] = None) -> Dict[str, Any]:
    """All-time records by manager across the seasons ESPN has."""
    through_week = through_week or last_completed_week(conn)
    season = int(_meta(conn, "season") or 0)
    current = {r[0]: {"team_id": r[1], "name": r[2]} for r in conn.execute(
        "SELECT owner_id, team_id, name FROM teams")}

    def label(owner: str) -> Dict[str, Any]:
        c = current.get(owner)
        return {"owner": owner, "team_id": c["team_id"] if c else None,
                "name": c["name"] if c else None}
    mgr: Dict[str, Dict[str, Any]] = {}
    owner_of: Dict[Tuple[int, int], str] = {}
    team_name: Dict[Tuple[int, int], str] = {}
    for s, tid, owner, name, w, l, t, pf, pa, rank, seed, made in conn.execute(
            "SELECT * FROM history_teams ORDER BY season"):
        owner_of[(s, tid)] = owner
        team_name[(s, tid)] = name
        m = mgr.setdefault(owner, dict(label(owner), seasons=0, wins=0, losses=0, ties=0,
                                       points_for=0.0, titles=0, playoffs=0, finishes={}))
        m["seasons"] += 1
        m["wins"] += w or 0
        m["losses"] += l or 0
        m["ties"] += t or 0
        m["points_for"] += pf or 0.0
        m["titles"] += rank == 1
        m["playoffs"] += made or 0
        m["finishes"][s] = {"rank": rank, "name": name, "record": "%d-%d" % (w or 0, l or 0)}
    for tid, (owner,) in ((r[0], (r[1],)) for r in conn.execute(
            "SELECT team_id, owner_id FROM teams")):
        owner_of[(season, tid)] = owner
        team_name[(season, tid)] = (current.get(owner) or {}).get("name") or ""
        mgr.setdefault(owner, dict(label(owner), seasons=0, wins=0, losses=0, ties=0,
                                   points_for=0.0, titles=0, playoffs=0, finishes={}))
    for m in mgr.values():
        ranks = [f["rank"] for f in m["finishes"].values() if f["rank"]]
        m["avg_finish"] = sum(ranks) / len(ranks) if ranks else None
        g = m["wins"] + m["losses"] + m["ties"]
        m["win_pct"] = (m["wins"] + 0.5 * m["ties"]) / g if g else None

    # Head to head and single-game records: finished seasons plus this year so far.
    games = [(s, w, tier, h, hp, a, ap) for s, w, tier, h, hp, a, ap in conn.execute(
        "SELECT * FROM history_games") if h is not None and a is not None
        and tier in ("NONE", "WINNERS_BRACKET")]
    games += [(season, w, "NONE", h, hp, a, ap) for w, h, hp, a, ap in conn.execute(
        """SELECT week, home_team_id, home_points, away_team_id, away_points FROM matchups
           WHERE week <= ? AND away_team_id IS NOT NULL""", (through_week,))]
    h2h: Dict[str, Dict[str, List[int]]] = {}
    singles = []
    for s, w, tier, h, hp, a, ap in games:
        oh, oa = owner_of.get((s, h)), owner_of.get((s, a))
        if not oh or not oa or hp is None or ap is None or (hp == 0 and ap == 0):
            continue
        for me_, them, mp, tp in ((oh, oa, hp, ap), (oa, oh, ap, hp)):
            rec = h2h.setdefault(me_, {}).setdefault(them, [0, 0, 0])
            rec[0 if mp > tp else 1 if mp < tp else 2] += 1
        for tid, o, p, op, otid in ((h, oh, hp, ap, a), (a, oa, ap, hp, h)):
            singles.append({"season": s, "week": w, "owner": o, "team": team_name.get((s, tid)),
                            "points": p, "opp_points": op, "opp_team": team_name.get((s, otid)),
                            "playoff": tier != "NONE"})
    records = {
        "high": sorted(singles, key=lambda x: -x["points"])[:5],
        "low": sorted(singles, key=lambda x: x["points"])[:5],
        "blowout": sorted(singles, key=lambda x: -(x["points"] - x["opp_points"]))[:5],
        "heartbreak": sorted((x for x in singles if x["points"] < x["opp_points"]),
                             key=lambda x: -x["points"])[:5],
    }

    # Draft tendencies: positions taken in rounds 1-3, and when the first QB goes.
    pos = dict(conn.execute("SELECT player_id, position_id FROM players"))
    pname = dict(conn.execute("SELECT player_id, name FROM players"))
    picks = [(s, rnd, tid, pid) for s, rnd, tid, pid in conn.execute(
        "SELECT season, round, team_id, player_id FROM history_picks")]
    picks += [(season, rnd, tid, pid) for rnd, tid, pid in conn.execute(
        "SELECT round, team_id, player_id FROM draft_picks")]
    tend: Dict[str, Dict[str, Any]] = {}
    for s, rnd, tid, pid in picks:
        o = owner_of.get((s, tid))
        p = POS.get(pos.get(pid))
        if not o or not p:
            continue
        t = tend.setdefault(o, {"early": {}, "first_qb": {}, "first_te": {},
                                "drafts": set(), "taken": {}})
        t["drafts"].add(s)
        t["taken"].setdefault(pid, {})[s] = rnd
        if rnd <= 3:
            t["early"][p] = t["early"].get(p, 0) + 1
        for key, want in (("first_qb", "QB"), ("first_te", "TE")):
            if p == want:
                t[key][s] = min(t[key].get(s, 99), rnd)
    tendencies = []
    for o, t in tend.items():
        avg = lambda d: sum(d.values()) / len(d) if d else None  # noqa: E731
        # Favourites: the same player drafted in two or more seasons.
        favs = sorted(({"name": pname.get(pid) or "Player %s" % pid,
                        "pos": POS.get(pos.get(pid), "?"), "times": len(yrs),
                        "picks": [{"season": y, "round": yrs[y]} for y in sorted(yrs)]}
                       for pid, yrs in t["taken"].items() if len(yrs) >= 2),
                      key=lambda f: (-f["times"], sum(p["round"] for p in f["picks"])))
        tendencies.append(dict(label(o), drafts=len(t["drafts"]), early=t["early"],
                               favorites=favs,
                               first_qb=avg(t["first_qb"]), first_te=avg(t["first_te"])))
    seasons = sorted({s for s, _ in owner_of if s != season})
    managers = sorted(mgr.values(), key=lambda m: (-(m["titles"]), -(m["win_pct"] or 0)))
    # ESPN member ids stay server-side; the page gets short keys instead.
    key = {m["owner"]: "m%d" % i for i, m in enumerate(managers)}
    for group in (managers, tendencies, *records.values()):
        for row in group:
            if "owner" in row:
                row["key"] = key.get(row.pop("owner"))
    return {"seasons": seasons, "managers": managers,
            "h2h": {key[a]: {key[b]: r for b, r in row.items() if b in key}
                    for a, row in h2h.items() if a in key},
            "records": records,
            "tendencies": sorted(tendencies, key=lambda t: t["first_qb"] or 99)}


def dashboard(conn: sqlite3.Connection, now_ms: int) -> Dict[str, Any]:
    """Everything the web page needs, in one JSON-ready dict."""
    through = last_completed_week(conn)
    teams = {r[0]: {"team_id": r[0], "name": r[1], "abbrev": r[2], "points_for": r[3],
                    "points_against": r[4]}
             for r in conn.execute(
                 "SELECT team_id, name, abbrev, points_for, points_against FROM teams")}
    recs, ap = records(conn, through), all_play(conn, through)
    standings = []
    for tid, t in teams.items():
        w, l, ties = recs.get(tid, [0, 0, 0])
        a = ap.get(tid, {"wins": 0, "losses": 0, "ties": 0, "expected_wins": 0.0})
        standings.append({"team_id": tid, "wins": w, "losses": l, "ties": ties,
                          "points_for": t["points_for"], "points_against": t["points_against"],
                          "all_play": "%d-%d" % (a["wins"], a["losses"]),
                          "expected_wins": a["expected_wins"],
                          "luck": w + 0.5 * ties - a["expected_wins"]})
    standings.sort(key=lambda s: (-(s["wins"] + 0.5 * s["ties"]), -(s["points_for"] or 0)))
    return {
        "league": _meta(conn, "league_name"), "season": _meta(conn, "season"),
        "my_team_id": int(_meta(conn, "my_team_id") or 0) or None,
        "through_week": through, "ingested_at": int(_meta(conn, "ingested_at") or 0),
        "playoff_teams": int(_meta(conn, "playoff_team_count") or 0),
        "teams": teams, "standings": standings,
        "live": live(conn, now_ms),
        "bench": bench_detail(conn, through),
        "draft": draft_analysis(conn, through),
        "schedule_swap": schedule_swap(conn, through),
        "trades": trade_ledger(conn, through),
        "waivers": waiver_scores(conn, through),
        "drop_regret": drop_regret(conn, through),
        "history": history(conn, through),
        "activity": activity(conn),
    }
