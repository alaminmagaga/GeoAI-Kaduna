import json
import re
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")


WORKSPACE = Path(__file__).resolve().parent
NOTEBOOK = WORKSPACE / "GeoAI_Kaduna_Publication_Grade_GHSL_Workflow_Colab.ipynb"
LOCAL_OUTPUT = WORKSPACE / "kaduna_publication_outputs"


def transform_source(source: str) -> str:
    source = source.replace("/content/kaduna_publication_outputs", str(LOCAL_OUTPUT).replace("\\", "/"))
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("!"):
            continue
        if stripped == "ee.Authenticate()":
            continue
        if stripped == "from google.colab import files":
            continue
        if stripped.startswith("files.download("):
            print(f"Skipping Colab download call: {stripped}", flush=True)
            lines.append("    pass" if line.startswith((" ", "\t")) else "pass")
            continue
        lines.append(line)

    transformed = "\n".join(lines)
    # Suppress bare image display expressions in local script execution.
    transformed = re.sub(r"\n\s*Image\.open\([^\n]+\)\s*$", "\n", transformed)
    return transformed


def main() -> int:
    LOCAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))

    namespace = {
        "__name__": "__main__",
        "__file__": str(NOTEBOOK),
    }

    code_cells = [
        (index, cell)
        for index, cell in enumerate(notebook["cells"])
        if cell.get("cell_type") == "code"
    ]

    for order, (index, cell) in enumerate(code_cells, start=1):
        source = "".join(cell.get("source", []))
        first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")
        code = transform_source(source)

        if not code.strip():
            print(f"[{order}/{len(code_cells)}] Empty after transform cell {index}: {first_line[:100]}", flush=True)
            continue

        print(f"[{order}/{len(code_cells)}] Running cell {index}: {first_line[:100]}", flush=True)
        try:
            exec(compile(code, f"{NOTEBOOK.name}:cell-{index}", "exec"), namespace)
        except Exception:
            print(f"Failed in notebook cell {index}: {first_line}", file=sys.stderr)
            traceback.print_exc()
            return 1

    print(f"Finished. Outputs are in: {LOCAL_OUTPUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
