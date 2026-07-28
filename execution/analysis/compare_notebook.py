# import argparse
# import json
# from pathlib import Path

# from scripts.nbprocess.loader import load_notebook
# from scripts.nbprocess.diff import diff_notebooks_safe
# from scripts.nbprocess.summary import build_detailed_summary, extract_error_from_notebook
# from scripts.nbprocess.filesystem import ensure_parent_dir

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("notebook_a")
#     parser.add_argument("notebook_b")
#     parser.add_argument("NOTEBOOK_PATH")
#     parser.add_argument("REPO_ID")
#     parser.add_argument("--json", required=True)

#     args = parser.parse_args()

#     nb1 = load_notebook(args.notebook_a)
#     nb2 = load_notebook(args.notebook_b)

#     diff = diff_notebooks_safe(nb1, nb2)
#     nb_errors = extract_error_from_notebook(nb2)

#     summary = build_detailed_summary(
#         diff,
#         nb2,
#         args.NOTEBOOK_PATH,
#         args.REPO_ID
#     )

#     report = {
#         "summary": summary,
#         "raw_diff": diff,
#         "nb_errors": nb_errors,
#     }

#     ensure_parent_dir(args.json)
#     Path(args.json).write_text(json.dumps(report, indent=2, default=str))

# if __name__ == "__main__":
#     main()

# import argparse
# import json
# from pathlib import Path

# from scripts.nbprocess.loader import load_notebook
# from scripts.nbprocess.diff import diff_notebooks_safe
# from scripts.nbprocess.summary import build_detailed_summary, extract_error_from_notebook
# from scripts.nbprocess.filesystem import ensure_parent_dir

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("notebook_a")
#     parser.add_argument("notebook_b")
#     parser.add_argument("NOTEBOOK_PATH")
#     parser.add_argument("REPO_ID")
#     parser.add_argument("--json", required=True)

#     args = parser.parse_args()

#     nb1 = load_notebook(args.notebook_a)
#     nb2 = load_notebook(args.notebook_b)

#     diff = diff_notebooks_safe(nb1, nb2)
#     nb_errors = extract_error_from_notebook(nb2)

#     summary = build_detailed_summary(
#         diff,
#         nb2,
#         args.NOTEBOOK_PATH,
#         args.REPO_ID
#     )

#     report = {
#         "summary": summary,
#         "raw_diff": diff,
#         "nb_errors": nb_errors,
#     }

#     ensure_parent_dir(args.json)
#     Path(args.json).write_text(json.dumps(report, indent=2, default=str))

# if __name__ == "__main__":
#     main()
import argparse
import json
from pathlib import Path
from analysis.nbprocess.loader import load_notebook
from analysis.nbprocess.diff import diff_notebooks_safe
from analysis.nbprocess.summary import build_detailed_summary, extract_error_from_notebook
from analysis.nbprocess.filesystem import ensure_parent_dir

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook_a")
    parser.add_argument("notebook_b")
    parser.add_argument("NOTEBOOK_PATH")
    parser.add_argument("REPO_ID")
    parser.add_argument("--json", required=True)
    args = parser.parse_args()
    nb1 = load_notebook(args.notebook_a)
    nb2 = load_notebook(args.notebook_b)
    diff = diff_notebooks_safe(nb1, nb2)
    nb_errors = extract_error_from_notebook(nb2)
    summary = build_detailed_summary(
        diff,
        nb2,
        args.NOTEBOOK_PATH,
        args.REPO_ID
    )
    report = {
        "summary": summary,
        "raw_diff": diff,
        "nb_errors": nb_errors,
    }
    ensure_parent_dir(args.json)
    Path(args.json).write_text(json.dumps(report, indent=2, default=str))

if __name__ == "__main__":
    main()
