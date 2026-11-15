"""Compare analytics.py against the validation fixture in HANDOFF.md (weeks 1-9).

    python3 validate.py
"""
from __future__ import annotations

import sqlite3
import sys

import analytics
from ingest import DB_PATH

WEEKS = 9
# team_id: (record, all-play, expected wins, bench lost, worst week, worst lost,
#           top bench scorer that week, their points)
FIXTURE = {
    1: ("6-3", "53-46", 4.8, 196.4, 7, 48.5, "Tony Pollard", 36.5),
    2: ("4-5", "32-67", 2.9, 172.5, 4, 39.3, "Romeo Doubs", 23.8),
    3: ("5-4", "61-38", 5.5, 109.3, 6, 19.4, "Germie Bernard", 25.7),
    4: ("4-5", "51-48", 4.6, 183.4, 1, 32.7, "Devaughn Vele", 19.9),
    5: ("6-3", "47-52", 4.3, 126.6, 9, 31.9, "KC Concepcion", 18.8),
    6: ("2-7", "26-73", 2.4, 177.9, 2, 48.9, "Tyler Shough", 22.4),
    7: ("5-4", "58-41", 5.3, 123.6, 6, 32.2, "Bryce Young", 19.8),
    8: ("4-5", "53-46", 4.8, 114.8, 5, 26.3, "Mike Evans", 26.3),
    9: ("4-5", "51-48", 4.6, 140.0, 4, 38.2, "RJ Harvey", 19.3),
    10: ("3-6", "30-69", 2.7, 286.8, 8, 84.1, "Joe Burrow", 39.5),
    11: ("6-3", "62-37", 5.6, 161.5, 4, 32.4, "Kyle Monangai", 28.0),
    12: ("5-4", "70-29", 6.4, 176.9, 8, 65.4, "Tyler Allgeier", 36.1),
}
TOL = 0.051  # fixture is rounded to one decimal


def wl(w: int, l: int, t: int) -> str:
    return "%d-%d" % (w, l) + ("-%d" % t if t else "")


def main() -> int:
    if not DB_PATH.exists():
        print("league.db not found; run ingest.py first", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    names = dict(conn.execute("SELECT team_id, name FROM teams"))
    recs = analytics.records(conn, WEEKS)
    ap = analytics.all_play(conn, WEEKS)
    bench = analytics.bench_summary(conn, WEEKS, include_ir=True)
    bench_no_ir = analytics.bench_summary(conn, WEEKS, include_ir=False)
    scores = analytics.weekly_scores(conn, WEEKS)

    # Sanity: our starter totals should equal ESPN's totalPoints.
    worst_gap = max((abs(w["actual"] - scores[w["week"]][w["team_id"]])
                     for w in analytics.bench_weeks(conn, WEEKS, include_ir=True)), default=0.0)

    mismatches = 0
    print("Each cell: computed (expected if different). Weeks 1-%d, IR eligible.\n" % WEEKS)
    print("| id | Team | Record | All-play | xWins | Bench lost | Worst week | Top bench scorer | No-IR lost | |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for tid, (f_rec, f_ap, f_xw, f_lost, f_wk, f_wlost, f_name, f_pts) in FIXTURE.items():
        r, a, b = recs.get(tid, [0, 0, 0]), ap.get(tid), bench.get(tid)
        if not a or not b:
            print("| %d | (no data) |" % tid)
            mismatches += 1
            continue
        w = b["worst"]
        ok = True

        def cell(got: str, want: str, same: bool) -> str:
            nonlocal ok
            ok = ok and same
            return got if same else "**%s** (%s)" % (got, want)

        cells = [
            cell(wl(*r), f_rec, wl(*r) == f_rec),
            cell(wl(a["wins"], a["losses"], a["ties"]), f_ap,
                 wl(a["wins"], a["losses"], a["ties"]) == f_ap),
            cell("%.1f" % a["expected_wins"], "%.1f" % f_xw,
                 abs(a["expected_wins"] - f_xw) <= TOL),
            cell("%.1f" % b["total_lost"], "%.1f" % f_lost,
                 abs(b["total_lost"] - f_lost) <= TOL),
            cell("Wk %d (%.1f)" % (w["week"], w["lost"]), "Wk %d (%.1f)" % (f_wk, f_wlost),
                 w["week"] == f_wk and abs(w["lost"] - f_wlost) <= TOL),
            cell("%s %.1f" % (w["top_bench_name"], w["top_bench_points"]),
                 "%s %.1f" % (f_name, f_pts),
                 w["top_bench_name"] == f_name and abs(w["top_bench_points"] - f_pts) <= TOL),
        ]
        mismatches += 0 if ok else 1
        print("| %d | %s | %s | %.1f | %s |" % (
            tid, names.get(tid, "?"), " | ".join(cells),
            bench_no_ir[tid]["total_lost"], "OK" if ok else "DIFF"))

    print("\nStarter totals vs ESPN totalPoints: max gap %.2f" % worst_gap)
    print("%d of %d teams match the fixture." % (len(FIXTURE) - mismatches, len(FIXTURE)))
    return 0 if mismatches == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
