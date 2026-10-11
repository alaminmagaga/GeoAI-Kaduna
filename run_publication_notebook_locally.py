import json
import re
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")


WORKSPACE = Path(__file__).resolve().parent
NOTEBOOK = WORKSPACE / "GeoAI_Kaduna_Urban_Growth_Hotspots_Publication_Grade.ipynb"
OUTPUT_DIR = WORKSPACE / "kaduna_geoai_figures"


def transform_source(source: str) -> str:
    source = source.replace("/content/kaduna_geoai_figures", str(OUTPUT_DIR).replace("\\", "/"))
    source = source.replace("/content/kaduna_geoai_figures.zip", str(OUTPUT_DIR.with_suffix(".zip")).replace("\\", "/"))
    source = source.replace(
        "ee.Feature(feature).geometry().centroid().buffer(buffer_meters).bounds()",
        "ee.Feature(feature).geometry().centroid(10).buffer(buffer_meters, 10).bounds(10)",
    )

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
            continue
        lines.append(line)

    transformed = "\n".join(lines)
    transformed = re.sub(r"\n\s*Map\s*$", "\n", transformed)
    return transformed


def should_skip(source: str) -> bool:
    skip_markers = [
        "ee.batch.Export.table.toDrive",
        "ee.batch.Export.image.toDrive",
        "from google.colab import files",
        "files.download(",
    ]
    return any(marker in source for marker in skip_markers)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

    thumbnail_cache_wrapped = False

    for order, (index, cell) in enumerate(code_cells, start=1):
        source = "".join(cell.get("source", []))
        first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")

        if should_skip(source):
            print(f"[{order}/{len(code_cells)}] Skipping notebook cell {index}: {first_line[:100]}", flush=True)
            continue

        code = transform_source(source)
        if not code.strip():
            print(f"[{order}/{len(code_cells)}] Empty after transform cell {index}: {first_line[:100]}", flush=True)
            continue

        print(f"[{order}/{len(code_cells)}] Running notebook cell {index}: {first_line[:100]}", flush=True)
        try:
            exec(compile(code, f"{NOTEBOOK.name}:cell-{index}", "exec"), namespace)
        except Exception:
            print(f"Failed in notebook cell {index}: {first_line}", file=sys.stderr)
            traceback.print_exc()
            return 1

        if "download_ee_thumbnail" in namespace and not thumbnail_cache_wrapped:
            original_download = namespace["download_ee_thumbnail"]

            def cached_download_ee_thumbnail(image, vis_params, region, filename, dimensions=1800):
                output_path = OUTPUT_DIR / filename
                if output_path.exists() and output_path.stat().st_size > 0:
                    print(f"Using cached: {output_path}", flush=True)
                    return output_path
                retry_dimensions = [dimensions]
                for fallback_dimensions in [1500, 1200, 900, 700]:
                    if fallback_dimensions < dimensions and fallback_dimensions not in retry_dimensions:
                        retry_dimensions.append(fallback_dimensions)

                last_error = None
                for retry_dimension in retry_dimensions:
                    try:
                        if retry_dimension != dimensions:
                            print(
                                f"Retrying {filename} at dimensions={retry_dimension}",
                                flush=True,
                            )
                        return original_download(image, vis_params, region, filename, retry_dimension)
                    except Exception as exc:
                        last_error = exc
                        print(
                            f"Thumbnail attempt failed for {filename} at dimensions={retry_dimension}: {exc}",
                            flush=True,
                        )
                raise last_error

            namespace["download_ee_thumbnail"] = cached_download_ee_thumbnail
            thumbnail_cache_wrapped = True

    print(f"Finished. Outputs are in: {OUTPUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
