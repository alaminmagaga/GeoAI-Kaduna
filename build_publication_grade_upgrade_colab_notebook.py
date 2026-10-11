import json
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
SCRIPT = WORKSPACE / "generate_publication_grade_upgrade.py"
NOTEBOOK = WORKSPACE / "GeoAI_Kaduna_Publication_Grade_Upgrade_Colab.ipynb"


def cell(cell_type, source):
    return {
        "cell_type": cell_type,
        "metadata": {},
        "source": source.splitlines(keepends=True),
        "outputs": [],
        "execution_count": None if cell_type == "code" else None,
    }


def main():
    script_text = SCRIPT.read_text(encoding="utf-8")
    script_text = script_text.replace('PROJECT_ID = "geoai-502123"', 'PROJECT_ID = "geoai-502123"  # Change if your Earth Engine project ID is different')

    cells = [
        cell(
            "markdown",
            """# Kaduna Publication-Grade GeoAI Urban Growth Workflow

This notebook generates publication-grade outputs for:

**GeoAI-Based Detection of Urban Growth Hotspots for Climate-Resilient Urban Planning in Kaduna Metropolis, Nigeria**

It uses GHSL GHS-BUILT-S P2023A as the primary built-up evidence layer for 2000-2025, FAO GAUL boundaries for Kaduna State and the metropolitan LGAs, Landsat imagery for visual context, and OSM roads/water features for spatial driver interpretation.

The hotspot outputs are **ranked high-growth grid cells / growth intensity hotspots**, not statistically significant hotspots unless a formal spatial statistic such as Getis-Ord Gi* is added.
""",
        ),
        cell(
            "code",
            """# Install dependencies. Runtime restart is usually not required.
!pip -q install earthengine-api geemap geopandas shapely pyproj osmnx mapclassify rasterio requests pillow pandas matplotlib
""",
        ),
        cell(
            "code",
            """import ee

# Authenticate the first time you run this notebook in Colab.
ee.Authenticate()

# Change project if needed.
PROJECT_ID = "geoai-502123"
ee.Initialize(project=PROJECT_ID)
print("Earth Engine initialized:", PROJECT_ID)
""",
        ),
        cell(
            "markdown",
            """## Run The Publication Workflow

The code below creates `kaduna_publication_grade_outputs` with figures, tables, GeoJSON files, QGIS-ready rasters, and a ZIP package.
""",
        ),
        cell("code", script_text),
        cell(
            "code",
            """# Execute the workflow.
main()
""",
        ),
        cell(
            "code",
            """# Preview the final contact sheet in Colab.
from IPython.display import Image, display
display(Image("kaduna_publication_grade_outputs/figures/figure_0_contact_sheet.png"))
""",
        ),
        cell(
            "code",
            """# Download the final output package.
from google.colab import files
files.download("kaduna_publication_grade_outputs/kaduna_publication_figures.zip")
""",
        ),
    ]

    nb = {
        "cells": cells,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    NOTEBOOK.write_text(json.dumps(nb, indent=2), encoding="utf-8")
    print(NOTEBOOK)


if __name__ == "__main__":
    main()
