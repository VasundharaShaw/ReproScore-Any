"""
src/scoring/rcs.py
==================
Reproducibility Composite Score (RCS).
RCS blends RRS (static readiness) and ROS (execution outcome) via a
coverage weight α proportional to the fraction of execution evidence
collected. Collapses to RRS when no ROS is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .rubric import Rubric, load_rubric


@dataclass
class RCSResult:
    rcs: float
    rrs: float
    ros: Optional[float]
    alpha: float
    alpha_max: float
    coverage_level: str


class RCSScorer:
    def __init__(self, rubric: Optional[Rubric] = None):
        self.rubric = rubric or load_rubric()

    def score(self, rrs: float, ros: Optional[float],
              coverage_weight_sum: float = 0.0) -> RCSResult:
        alpha_max = self.rubric.rcs["alpha_max"]
        alpha_min = self.rubric.rcs["alpha_min"]

        if ros is None or coverage_weight_sum == 0.0:
            return RCSResult(rcs=round(rrs, 2), rrs=round(rrs, 2), ros=None,
                             alpha=0.0, alpha_max=alpha_max,
                             coverage_level="No execution data")

        alpha = min(coverage_weight_sum, 1.0) * alpha_max
        alpha = max(alpha, alpha_min)
        rcs = max(0.0, min(100.0, (1.0 - alpha) * rrs + alpha * ros))

        return RCSResult(
            rcs=round(rcs, 2), rrs=round(rrs, 2), ros=round(ros, 2),
            alpha=round(alpha, 4), alpha_max=alpha_max,
            coverage_level=_coverage_level(coverage_weight_sum),
        )


def _coverage_level(w: float) -> str:
    if w <= 0:        return "No execution data"
    elif w <= 0.10:   return "Notebooks only"
    elif w <= 0.65:   return "Install + execution"
    elif w <= 0.80:   return "All except determinism"
    else:             return "Full ROS"
