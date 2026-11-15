import unittest

from village import farm


def P(**kw):
    return farm.validate(kw)


class ValidateTest(unittest.TestCase):
    def test_defaults(self):
        p = P()
        self.assertEqual((p["mode"], p["full"], p["max_attacks"]), ("full", "both", 60))

    def test_rejects_bad_input(self):
        for bad in ({"mode": "nuke"}, {"full": "gems"}, {"attacks": 0}, {"max_minutes": 1},
                    {"ranked": "yes"}, {"gold": -5}, {"mode": "target"}, {"bogus": 1},
                    {"attacks": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                farm.validate(bad)

    def test_attack_mode_raises_budget(self):
        self.assertEqual(P(mode="attacks", attacks=80, max_attacks=10)["max_attacks"], 80)

    def test_target_mode(self):
        p = P(mode="target", gold="15000000", elixir="")
        self.assertEqual((p["gold"], p["elixir"]), (15_000_000, None))


class GoalTest(unittest.TestCase):
    def test_full(self):
        both = P(full="both")
        self.assertFalse(farm.goal_reached(both, {"gold_fill": 1.0, "elixir_fill": 0.5}, 3)[0])
        self.assertTrue(farm.goal_reached(both, {"gold_fill": 0.99, "elixir_fill": 0.99}, 3)[0])
        self.assertTrue(farm.goal_reached(P(full="any"), {"gold_fill": 1.0, "elixir_fill": 0.1}, 3)[0])
        self.assertTrue(farm.goal_reached(P(full="gold"), {"gold_fill": 1.0, "elixir_fill": 0.1}, 3)[0])
        self.assertFalse(farm.goal_reached(P(full="elixir"), {"gold_fill": 1.0}, 3)[0])

    def test_unknown_state_never_reaches(self):
        self.assertFalse(farm.goal_reached(P(), None, 50)[0])
        self.assertFalse(farm.goal_reached(P(mode="target", gold=5), {"gold": None}, 50)[0])

    def test_target(self):
        p = P(mode="target", gold=10, elixir=20)
        self.assertFalse(farm.goal_reached(p, {"gold": 11, "elixir": 19}, 1)[0])
        self.assertTrue(farm.goal_reached(p, {"gold": 11, "elixir": 20}, 1)[0])


class LoopTest(unittest.TestCase):
    def run_loop(self, p, results, states=None, stop_after=None, walls=None, minutes_per=1):
        results = list(results)
        states = list(states) if states else None
        t = [0.0]
        calls = {"attacks": 0}
        reports = []

        def attack():
            calls["attacks"] += 1
            t[0] += minutes_per * 60
            r = results.pop(0) if results else True
            if isinstance(r, Exception):
                raise r
            return r

        def read_state():
            return states.pop(0) if states else {"gold_fill": 0.5, "elixir_fill": 0.5}

        def stop():
            return stop_after is not None and calls["attacks"] >= stop_after

        out = farm.run_loop(p, attack, read_state, stop, lambda **kw: reports.append(kw),
                            walls, clock=lambda: t[0])
        return out, calls["attacks"], reports

    def test_attack_count(self):
        (status, msg), n, _ = self.run_loop(P(mode="attacks", attacks=3), [True, False, True, True])
        self.assertEqual((status, n), ("done", 4))

    def test_until_full(self):
        states = [{"gold_fill": 0.5, "elixir_fill": 0.5}, {"gold_fill": 0.9, "elixir_fill": 0.9},
                  {"gold_fill": 1.0, "elixir_fill": 0.99}]
        (status, msg), n, _ = self.run_loop(P(full="both"), [True, True], states)
        self.assertEqual((status, n), ("done", 2))
        self.assertIn("storage full", msg)

    def test_stop_between_attacks(self):
        (status, _), n, _ = self.run_loop(P(), [True] * 10, stop_after=2)
        self.assertEqual((status, n), ("stopped", 2))

    def test_budgets(self):
        (status, msg), n, _ = self.run_loop(P(max_attacks=4), [True] * 10)
        self.assertEqual((status, n), ("done", 4))
        (status, msg), n, _ = self.run_loop(P(max_minutes=10), [True] * 50, minutes_per=3)
        self.assertEqual((status, n), ("done", 4))
        self.assertIn("time budget", msg)

    def test_failures_in_a_row_end_the_run(self):
        (status, _), n, _ = self.run_loop(P(), [False] * 10)
        self.assertEqual((status, n), ("failed", farm.MAX_FAILS_IN_A_ROW))

    def test_exceptions_count_as_failures(self):
        (status, _), n, reports = self.run_loop(P(mode="attacks", attacks=2),
                                                [RuntimeError("boom"), True, True])
        self.assertEqual((status, n), ("done", 3))
        self.assertTrue(any("boom" in r.get("message", "") for r in reports))

    def test_walls_every_n_attacks(self):
        done = []
        (status, _), n, reports = self.run_loop(P(mode="attacks", attacks=4, walls=True, wall_every=2),
                                                [True] * 4, walls=lambda: done.append(1) or 3)
        self.assertEqual(len(done), 2)
        self.assertEqual(reports[-1]["walls"], 6)


if __name__ == "__main__":
    unittest.main()
