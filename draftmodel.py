"""Lineup-based draft grading.

A draft is worth the starting lineups it lets you field. Each week a roster's
best legal lineup (QB, RB, RB, WR, WR, TE, FLEX; kickers and defenses are left
out) is built from its drafted players plus unlimited replacement-level
fillers, and the roster is credited with what that lineup scores above a
lineup made only of fillers.

* A filler at a position scores the weekly average of the replacement-level
  player there (QB12, RB30, WR30, TE12 in a 12-team league with this lineup).
* A pick's "added" is how much the roster's value rose when he joined it, in
  draft order. A second quarterback therefore adds only the weeks he beats the
  first one and the filler.
* "Best on board" at a pick is the undrafted-so-far player who would have
  added the most to that roster at that moment.
* The perfect draft replays a team's pick slots, each time taking the best on
  board for the roster built so far. Efficiency = actual value / perfect value.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

QB, RB, WR, TE = 1, 2, 3, 4
FLEX_SLOT = 23
POS_SLOT = {QB: 0, RB: 2, WR: 4, TE: 6}


class DraftModel:
    def __init__(self, weekly: Dict[int, Dict[int, float]], position: Dict[int, Optional[int]],
                 picks: List[Tuple[int, int, int]], slots: Dict[int, int], n_teams: int,
                 weeks: Iterable[int]):
        """weekly = {player_id: {week: points}}; picks = [(overall, team_id, player_id)]."""
        self.weeks = sorted(weeks)
        self.pos = {pid: p for pid, p in position.items() if p in POS_SLOT}
        self.pts = {pid: [weekly.get(pid, {}).get(w, 0.0) for w in self.weeks]
                    for pid in self.pos}
        self.k = {p: slots.get(s, 0) for p, s in POS_SLOT.items()}
        self.flex = slots.get(FLEX_SLOT, 0)

        # Replacement level: the last starter league-wide at each position.
        totals: Dict[int, List[float]] = {p: [] for p in POS_SLOT}
        for pid, p in self.pos.items():
            totals[p].append(sum(self.pts[pid]))
        self.repl_rank: Dict[int, int] = {}
        self.repl: Dict[int, float] = {}
        n_weeks = max(1, len(self.weeks))
        for p in POS_SLOT:
            n = n_teams * self.k[p] + (n_teams * self.flex // 2 if p in (RB, WR) else 0)
            ranked = sorted(totals[p], reverse=True)
            self.repl_rank[p] = n
            self.repl[p] = (ranked[min(n, len(ranked)) - 1] / n_weeks) if ranked and n else 0.0
        self.flex_repl = max(self.repl[RB], self.repl[WR], self.repl[TE])

        # Standalone surplus: what a player adds to an otherwise all-filler team.
        self.surplus = {pid: sum(max(0.0, x - self.repl[self.pos[pid]]) for x in self.pts[pid])
                        for pid in self.pos}
        self.candidates = sorted((pid for pid, s in self.surplus.items() if s > 0),
                                 key=lambda pid: -self.surplus[pid])

        self.picks = sorted(picks)
        self.pick_of = {pid: overall for overall, _, pid in self.picks}
        self._run()

    # ---- valuation -------------------------------------------------------

    def value(self, roster: List[int]) -> float:
        """Season points the roster's best weekly lineups score above all-filler lineups."""
        by_pos: Dict[int, List[List[float]]] = {p: [] for p in POS_SLOT}
        for pid in roster:
            p = self.pos.get(pid)
            if p is not None:
                by_pos[p].append(self.pts[pid])
        total = 0.0
        for w in range(len(self.weeks)):
            rest: List[float] = []
            for p, series in by_pos.items():
                if not series:
                    continue
                vals = sorted((s[w] for s in series), reverse=True)
                k, r = self.k[p], self.repl[p]
                for v in vals[:k]:
                    if v > r:
                        total += v - r
                if p != QB:
                    rest.extend(vals[k:])
            if self.flex and rest:
                rest.sort(reverse=True)
                for v in rest[:self.flex]:
                    if v > self.flex_repl:
                        total += v - self.flex_repl
        return total

    def best_on_board(self, roster: List[int], at_pick: int, base: Optional[float] = None
                      ) -> Tuple[Optional[int], float]:
        """The available player who adds most to this roster at this pick."""
        base = self.value(roster) if base is None else base
        taken = set(roster)
        best_pid, best_add = None, 0.0
        for pid in self.candidates:
            if self.surplus[pid] <= best_add:
                break  # nobody further down can add more than his standalone surplus
            if pid in taken or self.pick_of.get(pid, 10 ** 9) < at_pick:
                continue
            add = self.value(roster + [pid]) - base
            if add > best_add:
                best_pid, best_add = pid, add
        return best_pid, best_add

    # ---- the draft -------------------------------------------------------

    def _run(self) -> None:
        self.pick_rows: List[Dict[str, Any]] = []
        self.teams: Dict[int, Dict[str, Any]] = {}
        rosters: Dict[int, List[int]] = {}
        perfect: Dict[int, List[int]] = {}
        for overall, team_id, pid in self.picks:
            roster = rosters.setdefault(team_id, [])
            base = self.value(roster)
            graded = pid in self.pos or self.pos.get(pid) is not None
            added = self.value(roster + [pid]) - base if pid in self.pos else 0.0
            best_pid, best_add = self.best_on_board(roster, overall, base)
            self.pick_rows.append({
                "pick": overall, "team_id": team_id, "player_id": pid,
                "graded": pid in self.pos, "added": added,
                "best_id": best_pid, "best_add": best_add,
                "gap": max(0.0, best_add - added) if graded else None})
            roster.append(pid)

            # Perfect draft for the same slot.
            ideal = perfect.setdefault(team_id, [])
            ideal_base = self.value(ideal)
            choice, gain = self.best_on_board(ideal, overall, ideal_base)
            t = self.teams.setdefault(team_id, {"perfect_picks": []})
            if choice is not None:
                ideal.append(choice)
                t["perfect_picks"].append({"pick": overall, "player_id": choice, "added": gain})
        for team_id, t in self.teams.items():
            t["actual"] = self.value(rosters[team_id])
            t["perfect"] = self.value(perfect[team_id])
            t["efficiency"] = t["actual"] / t["perfect"] if t["perfect"] else None
