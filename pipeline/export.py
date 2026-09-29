"""pipeline/export.py — write ReproScore results to CSV or PDF.

Input: a flat dict of scores (one repo). Missing keys and None values
are allowed; they are written as empty (CSV) or "N/A" (PDF).
"""
import csv
import os
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FIELDS = [
    "repo", "commit", "rubric", "timestamp",
    "rrs", "score_E", "score_A", "score_D", "score_C", "score_S",
    "penalty_env", "penalty_data",
    "ros", "ros_I", "ros_X", "ros_delta", "ros_N", "ros_E",
    "alpha", "rcs",
]


def _stamp(result):
    r = dict(result)
    r.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return r


def _fmt(v):
    if v is None or v == "":
        return "N/A"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def to_csv(results, path):
    """Write one row per repo. `results` is a dict or a list of dicts.
    Appends if the file exists (header written only once)."""
    if isinstance(results, dict):
        results = [results]
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in results:
            r = _stamp(r)
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    return path


def _table(rows):
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    return t


def to_pdf(result, path):
    """Write a one-page report for one repo."""
    r = _stamp(result)
    st = getSampleStyleSheet()
    no_exec = r.get("ros") in (None, "")

    story = [
        Paragraph(f"ReproScore report: {_fmt(r.get('repo'))}", st["Title"]),
        Paragraph(
            f"Commit: {_fmt(r.get('commit'))} &nbsp; Rubric: {_fmt(r.get('rubric'))} "
            f"&nbsp; Generated: {r['timestamp']}", st["Normal"]),
        Spacer(1, 12),
        Paragraph("Scores", st["Heading2"]),
        _table([["RRS", "ROS", "RCS", "alpha"],
                [_fmt(r.get("rrs")), _fmt(r.get("ros")), _fmt(r.get("rcs")), _fmt(r.get("alpha"))]]),
        Spacer(1, 12),
        Paragraph("RRS categories", st["Heading2"]),
        _table([["E", "A", "D", "C", "S", "Env penalty", "Data penalty"],
                [_fmt(r.get(k)) for k in
                 ("score_E", "score_A", "score_D", "score_C", "score_S",
                  "penalty_env", "penalty_data")]]),
        Spacer(1, 12),
        Paragraph("ROS components", st["Heading2"]),
        _table([["I", "X", "Δ", "N", "E'"],
                [_fmt(r.get(k)) for k in
                 ("ros_I", "ros_X", "ros_delta", "ros_N", "ros_E")]]),
        Spacer(1, 12),
        Paragraph("Notes", st["Heading2"]),
        Paragraph("T (test pass rate) is not produced by the pipeline; ROS is "
                  "normalised over the available probes (divided by 0.95).", st["Normal"]),
    ]
    if no_exec:
        story.append(Paragraph(
            "No execution run: ROS is N/A and RCS equals RRS (alpha = 0).", st["Normal"]))

    SimpleDocTemplate(path, pagesize=A4, title="ReproScore report").build(story)
    return path
