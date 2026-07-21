"""
src/scoring/rubric.py
=====================
Community rubric loader and validator.
Rubrics are versioned YAML configurations that override default category
weights and gate parameters, enabling domain-specific scoring.
Run standalone:
    from src.scoring.rubric import load_rubric, Rubric
    rubric = load_rubric("config/rubrics/default.yaml")
    print(rubric.categories)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_DEFAULTS: Dict[str, Any] = {
    "name": "default",
    "version": "1.0",
    "categories": {
        "E": {"weight": 0.30, "tau": 40, "k": 1.5},
        "A": {"weight": 0.25, "tau": 30, "k": 1.5},
        "D": {"weight": 0.20, "tau": 20, "k": 1.2},
        "C": {"weight": 0.15, "tau": 25, "k": 1.2},
        "S": {"weight": 0.10, "tau": 30, "k": 1.2},
    },
    "penalties": {
        "environment_hard_threshold": 10,
        "data_hard_threshold": 10,
        "environment_hard_penalty": 20,
        "data_hard_penalty": 15,
        "seed_threshold": 50,
        "seed_penalty": 10,
    },
    "ros_components": {
        "I":     {"weight": 0.30},
        "X":     {"weight": 0.25},
        "delta": {"weight": 0.20},
        "N":     {"weight": 0.10},
        "E":     {"weight": 0.10},
        "T":     {"weight": 0.05},
    },
    "rcs": {
        "alpha_max": 0.70,
        "alpha_min": 0.10,
    },
}

# Repo root, resolved from this file:
# scoring -> src -> reproscore -> pipeline -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class Rubric:
    name: str
    version: str
    categories: Dict[str, Dict[str, float]]
    penalties: Dict[str, float]
    ros_components: Dict[str, Dict[str, float]]
    rcs: Dict[str, float]

    def validate(self):
        cat_sum = sum(v["weight"] for v in self.categories.values())
        if abs(cat_sum - 1.0) > 0.01:
            raise ValueError(
                f"Category weights must sum to 1.0 +/- 0.01, got {cat_sum:.4f}"
            )
        ros_sum = sum(v["weight"] for v in self.ros_components.values())
        if abs(ros_sum - 1.0) > 0.01:
            raise ValueError(
                f"ROS component weights must sum to 1.0 +/- 0.01, got {ros_sum:.4f}"
            )


def _candidate_paths() -> List[Path]:
    """Search order for the default rubric. Absolute candidates come first so
    the rubric is found regardless of the current working directory."""
    return [
        _REPO_ROOT / "config" / "rubrics" / "default.yaml",
        _REPO_ROOT / "config" / "default_rubric.yaml",   # legacy layout
        Path("config/rubrics/default.yaml"),
        Path("config/default_rubric.yaml"),              # legacy layout
        Path("default_rubric.yaml"),
    ]


def load_rubric(path: Optional[str | Path] = None) -> Rubric:
    data = dict(_DEFAULTS)
    if path is None:
        for c in _candidate_paths():
            if c.exists():
                path = c
                break
    if path and _YAML_AVAILABLE:
        try:
            with open(path, "r") as f:
                loaded = yaml.safe_load(f)
            if loaded:
                data = loaded
        except Exception as e:
            import warnings
            warnings.warn(f"Could not load rubric from {path}: {e}. Using defaults.")
    rubric = Rubric(
        name=data.get("name", "default"),
        version=str(data.get("version", "1.0")),
        categories=data.get("categories", _DEFAULTS["categories"]),
        penalties=data.get("penalties", _DEFAULTS["penalties"]),
        ros_components=data.get("ros_components", _DEFAULTS["ros_components"]),
        rcs=data.get("rcs", _DEFAULTS["rcs"]),
    )
    rubric.validate()
    return rubric
