"""
OptiCut3D-Lab - Geometric Benchmark Engine & Deterministic Judge (Argon Hardened)
Evaluates 3D Packing and Cutting Stock heuristics on 200 reproducible batches in RAM.
Compares candidate heuristics against 3 global standards:
1. Shelf-FFDH (First-Fit Decreasing Height)
2. Guillotine Best-Area-Fit (BAF)
3. Extreme-Point Contact-Heuristic

Argon Mathematical Hardening:
- Pure NumPy vectorized 3D AABB collision checks and gravitational support integration.
- Strict Gravitational Support Law: >= 60.0% (0.60) base support area on underlying faces.
- Anti-Overfitting / Anti-Cheat AST & multi-state probe validation.
"""

import ast
import math
import time
import random
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional, Callable


@dataclass(slots=True)
class Item:
    id: int
    l: float
    w: float
    h: float
    volume: float

    @classmethod
    def create(cls, id_: int, l: float, w: float, h: float) -> "Item":
        return cls(id=id_, l=float(l), w=float(w), h=float(h), volume=float(l * w * h))


@dataclass(slots=True)
class PlacedItem:
    item_id: int
    x: float
    y: float
    z: float
    l: float
    w: float
    h: float
    volume: float


@dataclass
class Batch:
    batch_id: int
    batch_type: str  # 'ecommerce', 'cutting_panel', 'pallet'
    bin_L: float
    bin_W: float
    bin_H: float
    bin_volume: float
    items: List[Item]


def get_orthogonal_rotations(l: float, w: float, h: float) -> List[Tuple[float, float, float]]:
    """Returns unique orthogonal 90-degree rotations (up to 6 orientations) in deterministic order."""
    seen = set()
    rots = []
    for r in [(l, w, h), (l, h, w), (w, l, h), (w, h, l), (h, l, w), (h, w, l)]:
        if r not in seen:
            seen.add(r)
            rots.append(r)
    return rots


def check_collision_3d(
    x1: float, y1: float, z1: float, l1: float, w1: float, h1: float,
    x2: float, y2: float, z2: float, l2: float, w2: float, h2: float,
    eps: float = 1e-4
) -> bool:
    """Returns True if two 3D cuboids collide with positive overlap volume."""
    return not (
        x1 + l1 <= x2 + eps or x2 + l2 <= x1 + eps or
        y1 + w1 <= y2 + eps or y2 + w2 <= y1 + eps or
        z1 + h1 <= z2 + eps or z2 + h2 <= z1 + eps
    )


def check_collision_3d_vectorized(
    px: float, py: float, pz: float, il: float, iw: float, ih: float,
    placed_arr: np.ndarray, n_placed: int, eps: float = 1e-4
) -> bool:
    """
    Pure NumPy vectorized 3D AABB (Axis-Aligned Bounding Box) intersection check.
    placed_arr has shape (max_items, 6) with columns [x, y, z, l, w, h].
    """
    if n_placed == 0:
        return False
    p = placed_arr[:n_placed]
    sep_x = (px + il <= p[:, 0] + eps) | (p[:, 0] + p[:, 3] <= px + eps)
    sep_y = (py + iw <= p[:, 1] + eps) | (p[:, 1] + p[:, 4] <= py + eps)
    sep_z = (pz + ih <= p[:, 2] + eps) | (p[:, 2] + p[:, 5] <= pz + eps)
    return bool(np.any(~(sep_x | sep_y | sep_z)))


def check_gravitational_stability(
    x: float, y: float, z: float, l: float, w: float,
    placed_items: List[PlacedItem],
    required_ratio: float = 0.60,
    eps: float = 1e-3
) -> bool:
    """
    Checks if item placed at (x,y,z) is supported from below:
    - If z == 0: perfectly supported by bin floor.
    - If z > 0: at least required_ratio (60%) of base area must rest on top faces of placed items.
    """
    if z <= eps:
        return True

    base_area = l * w
    if base_area <= 0.0:
        return False
    supported_area = 0.0

    for p in placed_items:
        if abs((p.z + p.h) - z) <= eps:
            ox1 = max(x, p.x)
            ox2 = min(x + l, p.x + p.l)
            oy1 = max(y, p.y)
            oy2 = min(y + w, p.y + p.w)
            if ox2 > ox1 and oy2 > oy1:
                supported_area += (ox2 - ox1) * (oy2 - oy1)

    return (supported_area / base_area) >= required_ratio


def check_gravitational_stability_vectorized(
    px: float, py: float, pz: float, il: float, iw: float,
    placed_arr: np.ndarray, n_placed: int,
    required_ratio: float = 0.60,
    eps: float = 1e-3
) -> bool:
    """
    Pure NumPy vectorized gravitational support verification:
    Requires >= 60% (0.60) of the cuboid's bottom face area (il * iw) to rest directly
    on top faces of underlying placed items at height z == p_z + p_h.
    """
    if pz <= eps:
        return True
    if n_placed == 0:
        return False

    p = placed_arr[:n_placed]
    top_z = p[:, 2] + p[:, 5]
    mask = np.abs(top_z - pz) <= eps
    if not np.any(mask):
        return False

    sup = p[mask]
    ox = np.maximum(0.0, np.minimum(px + il, sup[:, 0] + sup[:, 3]) - np.maximum(px, sup[:, 0]))
    oy = np.maximum(0.0, np.minimum(py + iw, sup[:, 1] + sup[:, 4]) - np.maximum(py, sup[:, 1]))
    supported_area = float(np.sum(ox * oy))
    return (supported_area / (il * iw)) >= required_ratio


def _audit_candidate_ast_and_probes(candidate_code: str, score_func: Callable) -> Optional[str]:
    """
    Argon Anti-Cheat & Anti-Overfitting Security Gate:
    1. AST inspection: blocks imports, globals, nonlocals, attribute/subscript mutations, mutable default args.
    2. Multi-state probe check: verifies determinism and rejects constant/degenerate functions.
    """
    try:
        tree = ast.parse(candidate_code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return f"Forbidden AST construct: {type(node).__name__}"
        if isinstance(node, ast.FunctionDef) and node.name == "score_placement_3d":
            if node.args.defaults or node.args.kw_defaults:
                return "Mutable or default arguments are prohibited in score_placement_3d"
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, (ast.Subscript, ast.Attribute)):
                    return "In-place mutation of subscript or attribute is forbidden"

    # Probe 8 distinct 3D geometric states
    probes = [
        (20.0, 15.0, 10.0, 60.0, 40.0, 40.0, 0.0, 0.0, 0.0, 0.05),
        (20.0, 15.0, 10.0, 40.0, 25.0, 30.0, 20.0, 15.0, 10.0, 0.25),
        (30.0, 20.0, 15.0, 32.0, 22.0, 20.0, 28.0, 18.0, 0.0, 0.45),
        (12.0, 10.0, 8.0, 50.0, 40.0, 30.0, 0.0, 0.0, 20.0, 0.60),
        (45.0, 30.0, 25.0, 120.0, 80.0, 100.0, 0.0, 40.0, 0.0, 0.15),
        (25.0, 25.0, 20.0, 60.0, 40.0, 40.0, 35.0, 15.0, 20.0, 0.75),
        (80.0, 40.0, 2.0, 240.0, 120.0, 10.0, 0.0, 0.0, 4.0, 0.35),
        (15.0, 12.0, 6.0, 16.0, 14.0, 10.0, 44.0, 26.0, 30.0, 0.85),
    ]
    outputs = []
    for p in probes:
        val1 = float(score_func(*p))
        val2 = float(score_func(*p))
        if math.isnan(val1) or math.isinf(val1):
            return "NaN or Inf detected on geometric probe"
        if abs(val1 - val2) > 1e-9:
            return "Non-deterministic behavior detected on identical inputs"
        outputs.append(val1)

    if float(np.std(outputs)) < 1e-6:
        return "Constant or degenerate scoring function rejected (Anti-Overfitting Gate)"

    return None


class GeometricBenchmarkEngine:
    def __init__(self, seed: int = 42, num_batches: int = 200):
        self.seed = seed
        self.num_batches = num_batches
        self.batches: List[Batch] = self._generate_batches()
        self.baseline_cache: Optional[Dict[str, Any]] = None
        self._per_batch_baseline_cache: Optional[Dict[int, Tuple[int, int, int]]] = None

    def _generate_batches(self) -> List[Batch]:
        """Generates 200 deterministic, reproducible industry batches in RAM."""
        rng = random.Random(self.seed)
        batches: List[Batch] = []

        # 1. 70 E-commerce parcel orders (boxes: 60x40x40 or 50x40x30, parcels 10-35cm)
        for i in range(70):
            bin_L, bin_W, bin_H = (60.0, 40.0, 40.0) if i % 2 == 0 else (50.0, 40.0, 30.0)
            count = rng.randint(15, 28)
            items = []
            for j in range(count):
                il = round(rng.uniform(10.0, 32.0), 1)
                iw = round(rng.uniform(8.0, 25.0), 1)
                ih = round(rng.uniform(5.0, 20.0), 1)
                items.append(Item.create(j, il, iw, ih))
            batches.append(Batch(
                batch_id=len(batches) + 1,
                batch_type="ecommerce",
                bin_L=bin_L, bin_W=bin_W, bin_H=bin_H,
                bin_volume=bin_L * bin_W * bin_H,
                items=items
            ))

        # 2. 70 Industrial cutting panel orders (sheets 240x120x10, slabs 20-160cm x 15-90cm x 1-4cm)
        for i in range(70):
            bin_L, bin_W, bin_H = 240.0, 120.0, 10.0
            count = rng.randint(12, 24)
            items = []
            for j in range(count):
                il = round(rng.uniform(25.0, 140.0), 1)
                iw = round(rng.uniform(15.0, 75.0), 1)
                ih = round(rng.uniform(1.0, 4.0), 1)
                items.append(Item.create(j, il, iw, ih))
            batches.append(Batch(
                batch_id=len(batches) + 1,
                batch_type="cutting_panel",
                bin_L=bin_L, bin_W=bin_W, bin_H=bin_H,
                bin_volume=bin_L * bin_W * bin_H,
                items=items
            ))

        # 3. 60 Pallet loading orders (Euro-pallet 120x80x100, cartons 20-55cm)
        for i in range(60):
            bin_L, bin_W, bin_H = 120.0, 80.0, 100.0
            count = rng.randint(15, 30)
            items = []
            for j in range(count):
                il = round(rng.uniform(20.0, 50.0), 1)
                iw = round(rng.uniform(18.0, 40.0), 1)
                ih = round(rng.uniform(15.0, 35.0), 1)
                items.append(Item.create(j, il, iw, ih))
            batches.append(Batch(
                batch_id=len(batches) + 1,
                batch_type="pallet",
                bin_L=bin_L, bin_W=bin_W, bin_H=bin_H,
                bin_volume=bin_L * bin_W * bin_H,
                items=items
            ))

        return batches

    # =========================================================================
    # BASELINE 1: Shelf-FFDH (First-Fit Decreasing Height)
    # =========================================================================
    def solve_shelf_ffdh(self, batch: Batch) -> Tuple[int, float]:
        sorted_items = sorted(batch.items, key=lambda x: (x.h, x.l * x.w), reverse=True)
        L, W, H = batch.bin_L, batch.bin_W, batch.bin_H

        bins: List[List[PlacedItem]] = [[]]
        bin_shelves: List[List[Dict[str, float]]] = [[{
            "z": 0.0,
            "h": sorted_items[0].h if sorted_items else 0.0,
            "x": 0.0,
            "y": 0.0,
            "row_w": 0.0
        }]]

        for item in sorted_items:
            placed = False
            il, iw, ih = item.l, item.w, item.h
            if il > iw:
                il, iw = iw, il

            for b_idx in range(len(bins)):
                shelves = bin_shelves[b_idx]
                for shelf in shelves:
                    if ih <= shelf["h"] + 1e-4:
                        if shelf["x"] + il <= L and shelf["y"] + iw <= W:
                            bins[b_idx].append(PlacedItem(item.id, shelf["x"], shelf["y"], shelf["z"], il, iw, ih, item.volume))
                            shelf["x"] += il
                            shelf["row_w"] = max(shelf["row_w"], iw)
                            placed = True
                            break
                        elif shelf["y"] + shelf["row_w"] + iw <= W and il <= L:
                            shelf["y"] += shelf["row_w"]
                            shelf["x"] = 0.0
                            shelf["row_w"] = iw
                            bins[b_idx].append(PlacedItem(item.id, shelf["x"], shelf["y"], shelf["z"], il, iw, ih, item.volume))
                            shelf["x"] += il
                            placed = True
                            break
                if placed:
                    break

                last_shelf = shelves[-1]
                new_shelf_z = last_shelf["z"] + last_shelf["h"]
                if new_shelf_z + ih <= H and il <= L and iw <= W:
                    new_shelf = {
                        "z": new_shelf_z,
                        "h": ih,
                        "x": il,
                        "y": 0.0,
                        "row_w": iw
                    }
                    shelves.append(new_shelf)
                    bins[b_idx].append(PlacedItem(item.id, 0.0, 0.0, new_shelf_z, il, iw, ih, item.volume))
                    placed = True
                    break

            if not placed:
                new_bins_placed: List[PlacedItem] = [PlacedItem(item.id, 0.0, 0.0, 0.0, il, iw, ih, item.volume)]
                bins.append(new_bins_placed)
                bin_shelves.append([{
                    "z": 0.0,
                    "h": ih,
                    "x": il,
                    "y": 0.0,
                    "row_w": iw
                }])

        total_bins = len(bins)
        items_vol = sum(it.volume for it in batch.items)
        vol_util = items_vol / (total_bins * batch.bin_volume)
        return total_bins, vol_util

    # =========================================================================
    # BASELINE 2: Guillotine Best-Area-Fit (BAF)
    # =========================================================================
    def solve_guillotine_baf(self, batch: Batch) -> Tuple[int, float]:
        sorted_items = sorted(batch.items, key=lambda x: x.volume, reverse=True)
        L, W, H = batch.bin_L, batch.bin_W, batch.bin_H

        bin_free_blocks: List[List[Tuple[float, float, float, float, float, float]]] = [
            [(0.0, 0.0, 0.0, L, W, H)]
        ]

        for item in sorted_items:
            best_bin = -1
            best_block_idx = -1
            best_rot = None
            min_waste_area = float("inf")

            rotations = get_orthogonal_rotations(item.l, item.w, item.h)

            for b_idx, free_blocks in enumerate(bin_free_blocks):
                for blk_idx, (bx, by, bz, bl, bw, bh) in enumerate(free_blocks):
                    for (il, iw, ih) in rotations:
                        if il <= bl and iw <= bw and ih <= bh:
                            area_waste = (bl * bw) - (il * iw)
                            if area_waste < min_waste_area:
                                min_waste_area = area_waste
                                best_bin = b_idx
                                best_block_idx = blk_idx
                                best_rot = (il, iw, ih)

                if best_bin != -1:
                    break

            if best_bin != -1 and best_rot is not None:
                il, iw, ih = best_rot
                bx, by, bz, bl, bw, bh = bin_free_blocks[best_bin].pop(best_block_idx)

                if bl - il > 1e-3:
                    bin_free_blocks[best_bin].append((bx + il, by, bz, bl - il, iw, ih))
                if bw - iw > 1e-3:
                    bin_free_blocks[best_bin].append((bx, by + iw, bz, bl, bw - iw, ih))
                if bh - ih > 1e-3:
                    bin_free_blocks[best_bin].append((bx, by, bz + ih, bl, bw, bh - ih))
            else:
                il, iw, ih = item.l, item.w, item.h
                for (rl, rw, rh) in rotations:
                    if rl <= L and rw <= W and rh <= H:
                        il, iw, ih = rl, rw, rh
                        break

                new_blocks = []
                if L - il > 1e-3:
                    new_blocks.append((il, 0.0, 0.0, L - il, iw, ih))
                if W - iw > 1e-3:
                    new_blocks.append((0.0, iw, 0.0, L, W - iw, ih))
                if H - ih > 1e-3:
                    new_blocks.append((0.0, 0.0, ih, L, W, H - ih))
                bin_free_blocks.append(new_blocks)

        total_bins = len(bin_free_blocks)
        items_vol = sum(it.volume for it in batch.items)
        vol_util = items_vol / (total_bins * batch.bin_volume)
        return total_bins, vol_util

    # =========================================================================
    # BASELINE 3: Extreme-Point Contact Heuristic (Vectorized NumPy)
    # =========================================================================
    def solve_extreme_point_contact(self, batch: Batch) -> Tuple[int, float]:
        sorted_items = sorted(batch.items, key=lambda x: x.volume, reverse=True)
        L, W, H = batch.bin_L, batch.bin_W, batch.bin_H
        max_items = len(sorted_items)

        bins_arr: List[np.ndarray] = [np.empty((max_items, 6), dtype=np.float64)]
        bins_counts: List[int] = [0]
        bins_points: List[List[Tuple[float, float, float]]] = [[(0.0, 0.0, 0.0)]]

        for item in sorted_items:
            best_bin = -1
            best_point = None
            best_rot = None
            best_score = -float("inf")

            rotations = get_orthogonal_rotations(item.l, item.w, item.h)

            for b_idx in range(len(bins_arr)):
                p_arr = bins_arr[b_idx]
                n_placed = bins_counts[b_idx]
                points = bins_points[b_idx]

                for (px, py, pz) in points:
                    for (il, iw, ih) in rotations:
                        if px + il > L + 1e-4 or py + iw > W + 1e-4 or pz + ih > H + 1e-4:
                            continue

                        if check_collision_3d_vectorized(px, py, pz, il, iw, ih, p_arr, n_placed):
                            continue

                        if not check_gravitational_stability_vectorized(px, py, pz, il, iw, p_arr, n_placed, required_ratio=0.60):
                            continue

                        contact_area = 0.0
                        if px <= 1e-3: contact_area += iw * ih
                        if py <= 1e-3: contact_area += il * ih
                        if pz <= 1e-3: contact_area += il * iw
                        if abs(px + il - L) <= 1e-3: contact_area += iw * ih
                        if abs(py + iw - W) <= 1e-3: contact_area += il * ih

                        if n_placed > 0:
                            p = p_arr[:n_placed]
                            # Vectorized contact surface calculation
                            cx_mask = (np.abs(px - (p[:, 0] + p[:, 3])) <= 1e-3) | (np.abs((px + il) - p[:, 0]) <= 1e-3)
                            if np.any(cx_mask):
                                pcx = p[cx_mask]
                                oy = np.maximum(0.0, np.minimum(py + iw, pcx[:, 1] + pcx[:, 4]) - np.maximum(py, pcx[:, 1]))
                                oz = np.maximum(0.0, np.minimum(pz + ih, pcx[:, 2] + pcx[:, 5]) - np.maximum(pz, pcx[:, 2]))
                                contact_area += float(np.sum(oy * oz))

                            cy_mask = (np.abs(py - (p[:, 1] + p[:, 4])) <= 1e-3) | (np.abs((py + iw) - p[:, 1]) <= 1e-3)
                            if np.any(cy_mask):
                                pcy = p[cy_mask]
                                ox = np.maximum(0.0, np.minimum(px + il, pcy[:, 0] + pcy[:, 3]) - np.maximum(px, pcy[:, 0]))
                                oz = np.maximum(0.0, np.minimum(pz + ih, pcy[:, 2] + pcy[:, 5]) - np.maximum(pz, pcy[:, 2]))
                                contact_area += float(np.sum(ox * oz))

                            cz_mask = np.abs(pz - (p[:, 2] + p[:, 5])) <= 1e-3
                            if np.any(cz_mask):
                                pcz = p[cz_mask]
                                ox = np.maximum(0.0, np.minimum(px + il, pcz[:, 0] + pcz[:, 3]) - np.maximum(px, pcz[:, 0]))
                                oy = np.maximum(0.0, np.minimum(py + iw, pcz[:, 1] + pcz[:, 4]) - np.maximum(py, pcz[:, 1]))
                                contact_area += float(np.sum(ox * oy))

                        score = contact_area - (px * 1.0 + py * 1.0 + pz * 2.0)
                        if score > best_score:
                            best_score = score
                            best_bin = b_idx
                            best_point = (px, py, pz)
                            best_rot = (il, iw, ih)

                if best_bin != -1:
                    break

            if best_bin != -1 and best_point is not None and best_rot is not None:
                px, py, pz = best_point
                il, iw, ih = best_rot
                idx_slot = bins_counts[best_bin]
                bins_arr[best_bin][idx_slot] = (px, py, pz, il, iw, ih)
                bins_counts[best_bin] += 1

                if (px, py, pz) in bins_points[best_bin]:
                    bins_points[best_bin].remove((px, py, pz))
                bins_points[best_bin].extend([
                    (px + il, py, pz),
                    (px, py + iw, pz),
                    (px, py, pz + ih)
                ])
                bins_points[best_bin] = list({pt for pt in bins_points[best_bin] if pt[0] < L and pt[1] < W and pt[2] < H})
            else:
                il, iw, ih = item.l, item.w, item.h
                for (rl, rw, rh) in rotations:
                    if rl <= L and rw <= W and rh <= H:
                        il, iw, ih = rl, rw, rh
                        break
                new_arr = np.empty((max_items, 6), dtype=np.float64)
                new_arr[0] = (0.0, 0.0, 0.0, il, iw, ih)
                bins_arr.append(new_arr)
                bins_counts.append(1)
                bins_points.append([(il, 0.0, 0.0), (0.0, iw, 0.0), (0.0, 0.0, ih)])

        total_bins = len(bins_arr)
        items_vol = sum(it.volume for it in batch.items)
        vol_util = items_vol / (total_bins * batch.bin_volume)
        return total_bins, vol_util

    # =========================================================================
    # BASELINE RUNNER & CACHING
    # =========================================================================
    def compute_all_baselines(self) -> Dict[str, Any]:
        """Runs all 3 baselines across all 200 batches and caches results in RAM."""
        if self.baseline_cache is not None:
            return self.baseline_cache

        shelf_bins = 0
        guillotine_bins = 0
        extreme_bins = 0
        total_items_volume = 0.0
        total_bin_volume = 0.0

        batch_best_bins = []
        self._per_batch_baseline_cache = {}

        for batch in self.batches:
            s_b, _ = self.solve_shelf_ffdh(batch)
            g_b, _ = self.solve_guillotine_baf(batch)
            e_b, _ = self.solve_extreme_point_contact(batch)

            self._per_batch_baseline_cache[batch.batch_id] = (s_b, g_b, e_b)

            shelf_bins += s_b
            guillotine_bins += g_b
            extreme_bins += e_b

            best_b = min(s_b, g_b, e_b)
            batch_best_bins.append(best_b)

            total_items_volume += sum(it.volume for it in batch.items)
            total_bin_volume += batch.bin_volume

        n = len(self.batches)
        min_industry_bins = sum(batch_best_bins)

        self.baseline_cache = {
            "num_batches": n,
            "shelf_ffdh": {
                "total_bins": shelf_bins,
                "avg_bins": shelf_bins / n,
                "avg_utilization": total_items_volume / (shelf_bins * (total_bin_volume / n))
            },
            "guillotine_baf": {
                "total_bins": guillotine_bins,
                "avg_bins": guillotine_bins / n,
                "avg_utilization": total_items_volume / (guillotine_bins * (total_bin_volume / n))
            },
            "extreme_point_contact": {
                "total_bins": extreme_bins,
                "avg_bins": extreme_bins / n,
                "avg_utilization": total_items_volume / (extreme_bins * (total_bin_volume / n))
            },
            "combined_best_bins": min_industry_bins,
            "avg_combined_best_bins": min_industry_bins / n
        }
        return self.baseline_cache

    # =========================================================================
    # CANDIDATE HEURISTIC EVALUATION
    # =========================================================================
    def evaluate_candidate(
        self,
        candidate_code: str,
        max_batches: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Compiles and tests candidate `score_placement_3d` on the 200 benchmark batches.
        Enforces strict vectorized 3D AABB collision checks and >= 60% gravitational stability.
        """
        start_time = time.time()
        self.compute_all_baselines()

        safe_globals = {
            "math": math,
            "min": min,
            "max": max,
            "abs": abs,
            "pow": pow,
            "round": round,
        }
        local_scope: Dict[str, Any] = {}

        try:
            compiled = compile(candidate_code, "<candidate_formula>", "exec")
            exec(compiled, safe_globals, local_scope)
        except Exception as e:
            return {
                "valid": False,
                "disqualified": True,
                "error": f"Compilation Error: {str(e)}",
                "valid_rate": 0.0,
                "pct_saved_vs_baseline": -100.0,
                "score_avg_bins": 999.0,
                "score_avg_waste": 1.0
            }

        score_func = local_scope.get("score_placement_3d")
        if not callable(score_func):
            return {
                "valid": False,
                "disqualified": True,
                "error": "Function 'score_placement_3d' not found in candidate code",
                "valid_rate": 0.0,
                "pct_saved_vs_baseline": -100.0,
                "score_avg_bins": 999.0,
                "score_avg_waste": 1.0
            }

        # Step 2: AST Security & Anti-Overfitting Multi-Probe Audit
        try:
            audit_err = _audit_candidate_ast_and_probes(candidate_code, score_func)
            if audit_err:
                return {
                    "valid": False,
                    "disqualified": True,
                    "error": f"Security/Anti-Cheat Disqualification: {audit_err}",
                    "valid_rate": 0.0,
                    "pct_saved_vs_baseline": -100.0,
                    "score_avg_bins": 999.0,
                    "score_avg_waste": 1.0
                }
        except Exception as e:
            return {
                "valid": False,
                "disqualified": True,
                "error": f"Runtime Error on probe verification: {str(e)}",
                "valid_rate": 0.0,
                "pct_saved_vs_baseline": -100.0,
                "score_avg_bins": 999.0,
                "score_avg_waste": 1.0
            }

        # Step 3: Run benchmark batches using NumPy vectorized physics
        batches_to_run = self.batches[:max_batches] if max_batches else self.batches
        total_candidate_bins = 0
        total_items_volume = 0.0
        total_bins_volume = 0.0
        valid_batches = 0

        for batch in batches_to_run:
            try:
                bins_used, _ = self._pack_with_candidate(batch, score_func)
                total_candidate_bins += bins_used
                total_items_volume += sum(it.volume for it in batch.items)
                total_bins_volume += bins_used * batch.bin_volume
                valid_batches += 1
            except Exception as e:
                return {
                    "valid": False,
                    "disqualified": True,
                    "error": f"Batch #{batch.batch_id} packing failure: {str(e)}",
                    "valid_rate": valid_batches / len(batches_to_run),
                    "pct_saved_vs_baseline": -100.0,
                    "score_avg_bins": 999.0,
                    "score_avg_waste": 1.0
                }

        n = len(batches_to_run)
        avg_candidate_bins = total_candidate_bins / n
        avg_vol_efficiency = (total_items_volume / total_bins_volume) if total_bins_volume > 0 else 0.0
        avg_waste = 1.0 - avg_vol_efficiency

        # Fast cached lookup for baseline comparison
        shelf_total = 0
        guillotine_total = 0
        extreme_total = 0
        baseline_combined_bins = 0

        for b in batches_to_run:
            s_b, g_b, e_b = self._per_batch_baseline_cache[b.batch_id]
            shelf_total += s_b
            guillotine_total += g_b
            extreme_total += e_b
            baseline_combined_bins += min(s_b, g_b, e_b)

        avg_baseline_bins = baseline_combined_bins / n
        pct_saved_bins = ((baseline_combined_bins - total_candidate_bins) / baseline_combined_bins) * 100.0

        pct_saved_vs_shelf = ((shelf_total - total_candidate_bins) / shelf_total) * 100.0
        pct_saved_vs_guillotine = ((guillotine_total - total_candidate_bins) / guillotine_total) * 100.0
        pct_saved_vs_extreme = ((extreme_total - total_candidate_bins) / extreme_total) * 100.0

        beats_all = (
            total_candidate_bins <= shelf_total and
            total_candidate_bins <= guillotine_total and
            total_candidate_bins <= extreme_total and
            pct_saved_bins >= 0.0
        )

        exec_duration = time.time() - start_time

        return {
            "valid": True,
            "disqualified": False,
            "valid_rate": 1.0,
            "total_bins": total_candidate_bins,
            "score_avg_bins": avg_candidate_bins,
            "score_avg_waste": avg_waste,
            "avg_volume_efficiency": avg_vol_efficiency,
            "pct_saved_vs_baseline": round(pct_saved_bins, 2),
            "pct_saved_vs_shelf": round(pct_saved_vs_shelf, 2),
            "pct_saved_vs_guillotine": round(pct_saved_vs_guillotine, 2),
            "pct_saved_vs_extreme_point": round(pct_saved_vs_extreme, 2),
            "baseline_avg_bins": avg_baseline_bins,
            "beats_all_baselines": beats_all,
            "exec_time_sec": round(exec_duration, 2)
        }

    def _pack_with_candidate(
        self,
        batch: Batch,
        score_func: Callable
    ) -> Tuple[int, float]:
        """
        Executes candidate heuristic on a single batch using NumPy-vectorized 3D AABB
        collision verification and strict >= 60% gravitational base support enforcement.
        """
        sorted_items = sorted(batch.items, key=lambda x: (x.volume, x.l * x.w), reverse=True)
        L, W, H = batch.bin_L, batch.bin_W, batch.bin_H
        max_items = len(sorted_items)

        bins_arr: List[np.ndarray] = [np.empty((max_items, 6), dtype=np.float64)]
        bins_counts: List[int] = [0]
        bins_points: List[List[Tuple[float, float, float]]] = [[(0.0, 0.0, 0.0)]]
        bins_packed_volume: List[float] = [0.0]

        for item in sorted_items:
            best_bin = -1
            best_point = None
            best_rot = None
            best_score = -float("inf")

            rotations = get_orthogonal_rotations(item.l, item.w, item.h)

            for b_idx in range(len(bins_arr)):
                p_arr = bins_arr[b_idx]
                n_placed = bins_counts[b_idx]
                points = bins_points[b_idx]
                fill_ratio = bins_packed_volume[b_idx] / batch.bin_volume

                for (px, py, pz) in points:
                    for (il, iw, ih) in rotations:
                        if px + il > L + 1e-4 or py + iw > W + 1e-4 or pz + ih > H + 1e-4:
                            continue

                        # Vectorized 3D AABB collision check
                        if check_collision_3d_vectorized(px, py, pz, il, iw, ih, p_arr, n_placed):
                            continue

                        # Strict >= 60% gravitational base support check
                        if not check_gravitational_stability_vectorized(px, py, pz, il, iw, p_arr, n_placed, required_ratio=0.60):
                            continue

                        space_l = L - px
                        space_w = W - py
                        space_h = H - pz

                        score = float(score_func(
                            il, iw, ih,
                            space_l, space_w, space_h,
                            px, py, pz,
                            fill_ratio
                        ))
                        if not math.isfinite(score):
                            raise ValueError(f"Non-finite score ({score}) returned during 3D packing simulation.")

                        if score > best_score:
                            best_score = score
                            best_bin = b_idx
                            best_point = (px, py, pz)
                            best_rot = (il, iw, ih)

                if best_bin != -1 and best_score > -999900.0:
                    break

            if best_bin != -1 and best_point is not None and best_rot is not None:
                px, py, pz = best_point
                il, iw, ih = best_rot
                slot = bins_counts[best_bin]
                bins_arr[best_bin][slot] = (px, py, pz, il, iw, ih)
                bins_counts[best_bin] += 1
                bins_packed_volume[best_bin] += item.volume

                if (px, py, pz) in bins_points[best_bin]:
                    bins_points[best_bin].remove((px, py, pz))

                bins_points[best_bin].extend([
                    (px + il, py, pz),
                    (px, py + iw, pz),
                    (px, py, pz + ih)
                ])
                bins_points[best_bin] = list({pt for pt in bins_points[best_bin] if pt[0] < L and pt[1] < W and pt[2] < H})
            else:
                il, iw, ih = item.l, item.w, item.h
                for (rl, rw, rh) in rotations:
                    if rl <= L and rw <= W and rh <= H:
                        il, iw, ih = rl, rw, rh
                        break
                new_arr = np.empty((max_items, 6), dtype=np.float64)
                new_arr[0] = (0.0, 0.0, 0.0, il, iw, ih)
                bins_arr.append(new_arr)
                bins_counts.append(1)
                bins_packed_volume.append(item.volume)
                bins_points.append([(il, 0.0, 0.0), (0.0, iw, 0.0), (0.0, 0.0, ih)])

        total_bins = len(bins_arr)
        items_vol = sum(it.volume for it in batch.items)
        vol_util = items_vol / (total_bins * batch.bin_volume)
        return total_bins, vol_util
