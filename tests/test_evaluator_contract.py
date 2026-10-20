"""
OptiCut3D — Formal Evaluator Contract & Anti-Cheat Regression Suite
Covers invalid subcases (probe-evasion runtime exceptions, NaN, +Inf, -Inf,
constant/degenerate outputs, AST violations) plus finite-score controls.
"""

import unittest
from src.benchmark_engine import GeometricBenchmarkEngine


class TestEvaluatorContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = GeometricBenchmarkEngine(seed=42, num_batches=4)

    def test_01_probe_evasion_runtime_exceptions_rejected(self):
        probe_fills = "(0.05, 0.25, 0.45, 0.60, 0.15, 0.75, 0.35, 0.85)"
        subcases = [
            f"""
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    if round(bin_fill_ratio, 2) in {probe_fills}:
        return -(z * 10.0 + x + y) + bin_fill_ratio
    return 1.0 / 0.0
""",
            f"""
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    if round(bin_fill_ratio, 2) in {probe_fills}:
        return -(z * 10.0 + x + y) + bin_fill_ratio
    return math.log(-1.0)
""",
            f"""
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    if round(bin_fill_ratio, 2) in {probe_fills}:
        return -(z * 10.0 + x + y) + bin_fill_ratio
    return None
""",
        ]
        for idx, code in enumerate(subcases, 1):
            with self.subTest(subcase=idx):
                res = self.engine.evaluate_candidate(code, max_batches=2)
                self.assertFalse(res["valid"], f"Subcase {idx} must be rejected")

    def test_02_non_finite_and_degenerate_scores_rejected(self):
        probe_fills = "(0.05, 0.25, 0.45, 0.60, 0.15, 0.75, 0.35, 0.85)"
        subcases = [
            """
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    return float('nan')
""",
            """
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    return float('inf')
""",
            f"""
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    if round(bin_fill_ratio, 2) in {probe_fills}:
        return -(z * 10.0 + x + y) + bin_fill_ratio
    return float('nan')
""",
            f"""
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    if round(bin_fill_ratio, 2) in {probe_fills}:
        return -(z * 10.0 + x + y) + bin_fill_ratio
    return float('inf')
""",
            """
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    return 1.0
""",
        ]
        for idx, code in enumerate(subcases, 1):
            with self.subTest(subcase=idx):
                res = self.engine.evaluate_candidate(code, max_batches=2)
                self.assertFalse(res["valid"], f"Subcase {idx} must be rejected")

    def test_03_valid_finite_score_control_passes(self):
        valid_code = """
def score_placement_3d(item_l, item_w, item_h, space_l, space_w, space_h, x, y, z, bin_fill_ratio):
    res_l = space_l - item_l
    res_w = space_w - item_w
    res_h = space_h - item_h
    return -z * 12.0 - (res_l + res_w + res_h) + bin_fill_ratio * 25.0
"""
        res = self.engine.evaluate_candidate(valid_code, max_batches=2)
        self.assertTrue(res["valid"], f"Valid control failed: {res.get('error')}")


if __name__ == "__main__":
    unittest.main()
