"""
determinism.py -- Phase 2 output-determinism metric for the bridge.

Reuses the VALIDATED diff + nondeterminism logic from
execution/analysis/nbprocess/ so reproducibility_score matches the papermill
pipeline. Committed-vs-reexecuted; one run is enough.
"""

import nbformat

from ..analysis.nbprocess.diff import diff_notebooks_safe, extract_cell_ops, get_ops
from ..analysis.nbprocess.nondeterminism import detect_nondeterminism

IGNORE_FIELDS = {"execution_count", "metadata"}


def compute_determinism(original_nb, executed_nb):
    # nbdime needs NotebookNode outputs; the executor builds plain dicts, so coerce.
    original_nb = nbformat.from_dict(original_nb)
    executed_nb = nbformat.from_dict(executed_nb)

    code_cell_indices = [
        i for i, c in enumerate(executed_nb.cells) if c.cell_type == "code"
    ]
    total_code_cells = len(code_cell_indices)

    nondeterministic_cells = [
        i for i in code_cell_indices
        if detect_nondeterminism(executed_nb.cells[i].source)
    ]

    diff = diff_notebooks_safe(original_nb, executed_nb)
    different_cell_indices = set()
    for cell_op in extract_cell_ops(diff):
        if cell_op.get("op") != "patch":
            continue
        cell_index = cell_op.get("key")
        if cell_index not in code_cell_indices:
            continue
        for field_op in get_ops(cell_op.get("diff")):
            field = field_op.get("key")
            if field in IGNORE_FIELDS:
                continue
            if field in ("source", "outputs"):
                different_cell_indices.add(cell_index)

    same_cells = sorted(set(code_cell_indices) - different_cell_indices)
    different_cells_list = sorted(different_cell_indices)

    reproducibility_score = round(
        len(same_cells) / total_code_cells if total_code_cells else 1.0, 3
    )

    return {
        "total_code_cells": total_code_cells,
        "identical_cells_count": len(same_cells),
        "different_cells_count": len(different_cells_list),
        "nondeterministic_cells_count": len(nondeterministic_cells),
        "identical_cells": ",".join(map(str, same_cells)),
        "different_cells": ",".join(map(str, different_cells_list)),
        "nondeterministic_cells": ",".join(map(str, nondeterministic_cells)),
        "reproducibility_score": reproducibility_score,
    }


def insert_metrics(con, run_id, notebook_execution_id, repository_id, notebook_id, metrics):
    con.execute(
        """INSERT OR REPLACE INTO notebook_reproducibility_metrics
               (repository_run_id, notebook_execution_id, repository_id, notebook_id,
                total_code_cells, identical_cells_count, different_cells_count,
                nondeterministic_cells_count, identical_cells, different_cells,
                nondeterministic_cells, reproducibility_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, notebook_execution_id, repository_id, notebook_id,
         metrics["total_code_cells"], metrics["identical_cells_count"],
         metrics["different_cells_count"], metrics["nondeterministic_cells_count"],
         metrics["identical_cells"], metrics["different_cells"],
         metrics["nondeterministic_cells"], metrics["reproducibility_score"]),
    )
