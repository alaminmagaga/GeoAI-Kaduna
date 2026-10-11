import json
from pathlib import Path


OUT = Path("GeoAI_Kaduna_Publication_Grade_GHSL_Workflow_Colab.ipynb")


def md(text):
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + "\n" for line in text.strip("\n").split("\n")],
    }


def code(text):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line + "\n" for line in text.strip("\n").split("\n")],
    }


cells = []

cells.append(md(r"""
# GeoAI-Based Detection of Urban Growth Hotspots for Climate-Resilient Urban Planning in Kaduna Metropolis, Nigeria

## Publication-grade Google Colab workflow

This notebook implements a corrected and presentation-ready workflow for detecting urban growth hotspots in Kaduna Metropolis using open Earth Observation data.

The workflow uses:

- **GHSL GHS-BUILT-S P2023A** for multi-year built-up surface change from 2000 to 2025.
- **Landsat dry-season RGB composites** for visual satellite context.
- **Dynamic World** for recent 2025 land-cover context.
- **FAO GAUL administrative boundaries** for Kaduna State and metropolitan LGA extraction.

The notebook intentionally avoids using a single 2020 land-cover training label set to classify earlier years, because that produced unrealistic historical built-up trends. GHSL is used as the stronger evidence layer for built-up expansion.
"""))

cells.append(md(r"""
## 1. Install and import libraries
"""))

cells.append(code(r"""
!pip install -q earthengine-api geemap pillow requests pandas matplotlib

import math
import shutil
from pathlib import Path

import ee
import geemap
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont
"""))

cells.append(md(r"""
## 2. Authenticate and initialize Google Earth Engine

Edit `PROJECT_ID` if you want to use a different Google Cloud / Earth Engine project.
"""))

cells.append(code(r"""
ee.Authenticate()

PROJECT_ID = "geoai-502123"  # Change if needed.
ee.Initialize(project=PROJECT_ID)

OUTPUT_DIR = Path("/content/kaduna_publication_outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

YEARS = [2000, 2005, 2010, 2015, 2020, 2025]
PANEL_YEARS = [2000, 2010, 2020, 2025]

print("Earth Engine initialized.")
print("Outputs will be saved to:", OUTPUT_DIR)
"""))

cells.append(md(r"""
## 3. Define and verify Kaduna Metropolitan Area

The metropolitan study area is defined as four LGAs:

1. Kaduna North
2. Kaduna South
3. Chikun
4. Igabi

This gives a broader metropolitan/peri-urban footprint than using only Kaduna North and Kaduna South.
"""))

cells.append(code(r"""
def get_kaduna_metro():
    gaul_l2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    lga_names = ["Kaduna North", "Kaduna South", "Chikun", "Igabi"]

    kaduna_metro = gaul_l2.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", "Nigeria"),
            ee.Filter.eq("ADM1_NAME", "Kaduna"),
            ee.Filter.inList("ADM2_NAME", lga_names),
        )
    )

    return kaduna_metro, kaduna_metro.geometry()


kaduna_metro, aoi = get_kaduna_metro()
region = aoi.bounds(10).getInfo()["coordinates"]

selected_lgas = kaduna_metro.aggregate_array("ADM2_NAME").getInfo()
print("Number of selected LGA features:", kaduna_metro.size().getInfo())
print("Selected metropolitan LGAs:", selected_lgas)

expected_lgas = {"Kaduna North", "Kaduna South", "Chikun", "Igabi"}
if set(selected_lgas) != expected_lgas:
    raise ValueError(f"Unexpected LGA extraction: {selected_lgas}")
else:
    print("AOI verification passed.")
"""))

cells.append(md(r"""
## 4. Helper functions for map export and annotation
"""))

cells.append(code(r"""
def get_bbox(region):
    coords = region[0]
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return min(xs), min(ys), max(xs), max(ys)


def lonlat_to_pixel(lon, lat, bbox, width, height):
    minx, miny, maxx, maxy = bbox
    x = int((lon - minx) / (maxx - minx) * width)
    y = int((maxy - lat) / (maxy - miny) * height)
    return x, y


def get_fonts():
    # DejaVu Sans is usually available in Colab.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    regular_path = candidates[0] if Path(candidates[0]).exists() else None
    bold_path = candidates[1] if Path(candidates[1]).exists() else regular_path

    if regular_path:
        regular = ImageFont.truetype(regular_path, 22)
        bold = ImageFont.truetype(bold_path, 30)
        small = ImageFont.truetype(regular_path, 17)
        tiny = ImageFont.truetype(regular_path, 14)
    else:
        regular = bold = small = tiny = ImageFont.load_default()

    return regular, bold, small, tiny


def download_thumbnail(image, vis_params, region, filename, dimensions=1800):
    output_path = OUTPUT_DIR / filename
    if output_path.exists() and output_path.stat().st_size > 0:
        print("Using cached:", output_path)
        return output_path

    styled = image.visualize(**vis_params) if vis_params else image

    last_status = None
    for dim in [dimensions, 1600, 1300, 1000, 800]:
        if dim > dimensions:
            continue
        url = styled.getThumbURL({"region": region, "dimensions": dim, "format": "png"})
        response = requests.get(url, timeout=180)
        last_status = response.status_code

        if response.status_code == 200:
            output_path.write_bytes(response.content)
            print("Saved:", output_path)
            return output_path

        print(f"Thumbnail failed for {filename} at {dim}px: HTTP {response.status_code}")

    raise RuntimeError(f"Could not download {filename}; last status={last_status}")


def add_title_and_frame(path, title, subtitle=None, source=None):
    regular, bold, small, tiny = get_fonts()
    img = Image.open(path).convert("RGB")

    pad_top = 82 if subtitle else 60
    pad_bottom = 44 if source else 24

    canvas = Image.new("RGB", (img.width, img.height + pad_top + pad_bottom), "white")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(img, (0, pad_top))

    draw.text((img.width // 2, 18), title, anchor="ma", fill="black", font=bold)
    if subtitle:
        draw.text((img.width // 2, 54), subtitle, anchor="ma", fill="#333333", font=small)

    draw.rectangle((0, pad_top, img.width - 1, pad_top + img.height - 1), outline="#222222", width=2)

    if source:
        draw.text((18, img.height + pad_top + 12), source, fill="#333333", font=tiny)

    canvas.save(path)
    return path


def add_north_arrow_scale(path):
    regular, bold, small, tiny = get_fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # North arrow.
    cx, cy = w - 75, 145
    draw.polygon([(cx, cy - 42), (cx - 16, cy + 25), (cx, cy + 10), (cx + 16, cy + 25)], fill="black")
    draw.text((cx, cy - 64), "N", anchor="mm", fill="black", font=small)

    # Approximate visual scale bar for presentation.
    x0, y0 = 58, h - 82
    bar_w = 220
    seg = bar_w // 2
    draw.rectangle((x0, y0, x0 + seg, y0 + 12), fill="black", outline="black")
    draw.rectangle((x0 + seg, y0, x0 + bar_w, y0 + 12), fill="white", outline="black")
    draw.text((x0, y0 + 20), "0", fill="black", font=tiny)
    draw.text((x0 + seg, y0 + 20), "10", anchor="ma", fill="black", font=tiny)
    draw.text((x0 + bar_w, y0 + 20), "20 km", anchor="ra", fill="black", font=tiny)

    img.save(path)
    return path
"""))

cells.append(md(r"""
## 5. Dataset helper functions
"""))

cells.append(code(r"""
def ghsl_built_surface(year, aoi):
    # GHSL GHS-BUILT-S built-up surface for a target year.
    # Values represent built-up surface in square meters per cell.
    return (
        ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{year}")
        .select("built_surface")
        .clip(aoi)
    )


def landsat_composite(year, aoi):
    # Create a dry-season Landsat RGB composite for visual context.

    def mask_landsat_sr(image):
        qa = image.select("QA_PIXEL")
        cloud = qa.bitwiseAnd(1 << 3).neq(0)
        shadow = qa.bitwiseAnd(1 << 4).neq(0)
        return image.updateMask(cloud.Or(shadow).Not())

    def scale_landsat_sr(image):
        optical = image.select("SR_B.*").multiply(0.0000275).add(-0.2)
        return image.addBands(optical, None, True)

    def rename_l57(image):
        return image.select(
            ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7"],
            ["blue", "green", "red", "nir", "swir1", "swir2"],
        )

    def rename_l89(image):
        return image.select(
            ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
            ["blue", "green", "red", "nir", "swir1", "swir2"],
        )

    start = ee.Date.fromYMD(year, 1, 1)
    end = ee.Date.fromYMD(year, 12, 31)
    months = ee.Filter.Or(
        ee.Filter.calendarRange(1, 3, "month"),
        ee.Filter.calendarRange(11, 12, "month"),
    )

    l5 = (
        ee.ImageCollection("LANDSAT/LT05/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(mask_landsat_sr)
        .map(scale_landsat_sr)
        .map(rename_l57)
    )

    l7 = (
        ee.ImageCollection("LANDSAT/LE07/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(mask_landsat_sr)
        .map(scale_landsat_sr)
        .map(rename_l57)
    )

    l8 = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(mask_landsat_sr)
        .map(scale_landsat_sr)
        .map(rename_l89)
    )

    l9 = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(mask_landsat_sr)
        .map(scale_landsat_sr)
        .map(rename_l89)
    )

    if year < 2013:
        collection = l5.merge(l7)
    elif year < 2022:
        collection = l8
    else:
        collection = l8.merge(l9)

    count = collection.size().getInfo()
    print(f"{year}: Landsat dry-season image count = {count}")

    return collection.median().clip(aoi)


def make_white_map(layer, boundary, vis_params=None):
    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    layer_vis = layer.visualize(**vis_params) if vis_params else layer
    boundary_vis = boundary.visualize(min=0, max=1, palette=["00000000", "222222"])
    return white.blend(layer_vis).blend(boundary_vis)
"""))

cells.append(md(r"""
## 6. Figure 1 — Kaduna State context map with metropolitan LGAs highlighted
"""))

cells.append(code(r"""
def make_kaduna_state_context_map():
    gaul_l1 = ee.FeatureCollection("FAO/GAUL/2015/level1")
    gaul_l2 = ee.FeatureCollection("FAO/GAUL/2015/level2")

    kaduna_state = gaul_l1.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", "Nigeria"),
            ee.Filter.eq("ADM1_NAME", "Kaduna"),
        )
    )

    kaduna_lgas = gaul_l2.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", "Nigeria"),
            ee.Filter.eq("ADM1_NAME", "Kaduna"),
        )
    )

    state_region = kaduna_state.geometry().bounds(10).getInfo()["coordinates"]

    state_fill = ee.Image().byte().paint(kaduna_state, 1)
    state_boundary = ee.Image().byte().paint(kaduna_state, 1, 3)
    lga_boundaries = ee.Image().byte().paint(kaduna_lgas, 1, 1)
    metro_fill = ee.Image().byte().paint(kaduna_metro, 1)
    metro_boundary = ee.Image().byte().paint(kaduna_metro, 1, 3)

    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    image = (
        white
        .blend(state_fill.visualize(min=0, max=1, palette=["ffffff", "eeeeee"]))
        .blend(lga_boundaries.visualize(min=0, max=1, palette=["00000000", "bdbdbd"]))
        .blend(metro_fill.visualize(min=0, max=1, palette=["00000000", "ff6b35"]))
        .blend(metro_boundary.visualize(min=0, max=1, palette=["00000000", "b30000"]))
        .blend(state_boundary.visualize(min=0, max=1, palette=["00000000", "111111"]))
    )

    path = download_thumbnail(
        image,
        {},
        state_region,
        "figure_1_kaduna_state_with_metropolitan_lgas.png",
        dimensions=1800,
    )

    add_title_and_frame(
        path,
        "Kaduna Metropolitan Area Within Kaduna State",
        "Highlighted LGAs: Kaduna North, Kaduna South, Chikun, and Igabi",
        "Boundary source: FAO GAUL 2015 Level 1 and Level 2 administrative boundaries",
    )
    add_north_arrow_scale(path)

    return path


fig1_path = make_kaduna_state_context_map()
Image.open(fig1_path)
"""))

cells.append(md(r"""
## 7. Figure 2 — Verified metropolitan LGA map
"""))

cells.append(code(r"""
def get_lga_label_data(kaduna_metro):
    features = kaduna_metro.map(
        lambda f: f.set(
            {
                "lon": f.geometry().centroid(10).coordinates().get(0),
                "lat": f.geometry().centroid(10).coordinates().get(1),
                "name": f.get("ADM2_NAME"),
            }
        )
    )

    return [
        {
            "name": f["properties"]["name"],
            "lon": f["properties"]["lon"],
            "lat": f["properties"]["lat"],
        }
        for f in features.getInfo()["features"]
    ]


def add_lga_labels(path, label_data, bbox):
    regular, bold, small, tiny = get_fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size

    map_top = 82
    map_bottom = height - 44
    map_height = map_bottom - map_top

    offsets = {
        "Kaduna North": (0, -20),
        "Kaduna South": (8, 16),
        "Chikun": (-55, 10),
        "Igabi": (30, -15),
    }

    for item in label_data:
        x, y = lonlat_to_pixel(item["lon"], item["lat"], bbox, width, map_height)
        y += map_top
        dx, dy = offsets.get(item["name"], (0, 0))
        tx, ty = x + dx, y + dy
        draw.text((tx + 2, ty + 2), item["name"], anchor="mm", fill="white", font=small)
        draw.text((tx, ty), item["name"], anchor="mm", fill="#111111", font=small)

    img.save(path)
    return path


def make_verified_metropolitan_map():
    bbox = get_bbox(region)
    boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
    fill = ee.Image().byte().paint(kaduna_metro, 1)

    image = make_white_map(
        fill,
        boundary,
        {"min": 0, "max": 1, "palette": ["ffffff", "d9d9d9"]},
    )

    path = download_thumbnail(
        image,
        {},
        region,
        "figure_2_verified_kaduna_metropolitan_lgas.png",
        dimensions=1900,
    )

    add_title_and_frame(
        path,
        "Kaduna Metropolitan Study Area",
        "Administrative extraction: Kaduna North, Kaduna South, Chikun, and Igabi LGAs",
        "Boundary source: FAO GAUL 2015 Level 2 administrative boundaries; CRS display: WGS84",
    )
    add_lga_labels(path, get_lga_label_data(kaduna_metro), bbox)
    add_north_arrow_scale(path)

    return path


fig2_path = make_verified_metropolitan_map()
Image.open(fig2_path)
"""))

cells.append(md(r"""
## 8. Calculate GHSL built-up surface trend and run sanity checks
"""))

cells.append(code(r"""
built_images = {year: ghsl_built_surface(year, aoi) for year in YEARS}

area_rows = []
for year, image in built_images.items():
    area_sqkm = (
        image.divide(1e6)
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=aoi,
            scale=100,
            maxPixels=1e13,
            bestEffort=True,
        )
        .get("built_surface")
    )

    area_rows.append(
        {
            "year": year,
            "builtup_area_sqkm_ghsl": area_sqkm.getInfo(),
        }
    )

area_df = pd.DataFrame(area_rows)
area_df["change_sqkm"] = area_df["builtup_area_sqkm_ghsl"].diff()
area_df["percentage_change"] = area_df["builtup_area_sqkm_ghsl"].pct_change() * 100

area_csv = OUTPUT_DIR / "ghsl_builtup_area_statistics_2000_2025.csv"
area_df.to_csv(area_csv, index=False)

print(area_df)

if area_df["builtup_area_sqkm_ghsl"].iloc[-1] <= area_df["builtup_area_sqkm_ghsl"].iloc[0]:
    print("WARNING: Final built-up surface is not greater than baseline. Review methodology.")
else:
    print("Sanity check passed: built-up surface increases from baseline to final year.")

print("Saved:", area_csv)
"""))

cells.append(md(r"""
## 9. Figure 3 — Built-up surface trend chart
"""))

cells.append(code(r"""
fig, ax = plt.subplots(figsize=(8, 4.8))
ax.plot(
    area_df["year"],
    area_df["builtup_area_sqkm_ghsl"],
    marker="o",
    linewidth=2.6,
    color="#1f77b4",
)
ax.fill_between(
    area_df["year"],
    area_df["builtup_area_sqkm_ghsl"],
    color="#1f77b4",
    alpha=0.12,
)
ax.set_xlabel("Year")
ax.set_ylabel("Built-up surface area (sq. km)")
ax.set_title(
    f"Kaduna Built-up Surface Increased from {area_df['builtup_area_sqkm_ghsl'].iloc[0]:.1f} km² to {area_df['builtup_area_sqkm_ghsl'].iloc[-1]:.1f} km²",
    weight="bold",
)
ax.grid(True, linestyle="--", alpha=0.45)

for x, y in zip(area_df["year"], area_df["builtup_area_sqkm_ghsl"]):
    ax.text(x, y + 1.5, f"{y:.1f}", fontsize=8, ha="center")

plt.tight_layout()
fig3_path = OUTPUT_DIR / "figure_3_ghsl_builtup_growth_trend.png"
plt.savefig(fig3_path, dpi=300, bbox_inches="tight")
plt.show()

print("Saved:", fig3_path)
"""))

cells.append(md(r"""
## 10. Figure 4 — Multi-year built-up surface map panel
"""))

cells.append(code(r"""
boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
built_vis = {
    "min": 0,
    "max": 10000,
    "palette": ["ffffff", "fee8c8", "fdbb84", "e34a33", "67000d"],
}

built_paths = []
for year in PANEL_YEARS:
    image = make_white_map(built_images[year], boundary, built_vis)
    path = download_thumbnail(
        image,
        {},
        region,
        f"ghsl_built_surface_{year}.png",
        dimensions=1600,
    )
    built_paths.append(path)


def create_panel(image_paths, titles, output_filename, main_title=None, cols=2, figsize=(12, 9)):
    rows = math.ceil(len(image_paths) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=figsize)
    axes = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for ax, path, title in zip(axes, image_paths, titles):
        img = Image.open(path)
        ax.imshow(img)
        ax.set_title(title, fontsize=12, weight="bold")
        ax.axis("off")

    for ax in axes[len(image_paths):]:
        ax.axis("off")

    if main_title:
        fig.suptitle(main_title, fontsize=15, weight="bold")

    fig.tight_layout()
    output_path = OUTPUT_DIR / output_filename
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.show()

    print("Saved:", output_path)
    return output_path


fig4_path = create_panel(
    built_paths,
    [f"GHSL Built-up Surface: {year}" for year in PANEL_YEARS],
    "figure_4_ghsl_built_surface_panel.png",
    "Built-up Surface Change in Kaduna Metropolis (GHSL)",
    cols=2,
)
"""))

cells.append(md(r"""
## 11. Figure 5 — Satellite image + built-up overlay comparison

This panel makes urban growth visible by placing dense GHSL built-up cells over Landsat dry-season imagery.

The overlay threshold is used only for visualization. The area trend above uses continuous GHSL built-up surface values.
"""))

cells.append(code(r"""
def make_satellite_builtup_panel():
    boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
    rgb_vis = {"bands": ["red", "green", "blue"], "min": 0.02, "max": 0.30}
    paths = []

    for year in PANEL_YEARS:
        rgb = landsat_composite(year, aoi).visualize(**rgb_vis)

        # Visualization threshold: dense built-up cells only.
        built = built_images[year].gte(2500).selfMask().visualize(palette=["ff1f1f"])
        boundary_vis = boundary.visualize(min=0, max=1, palette=["00000000", "111111"])

        image = rgb.blend(built).blend(boundary_vis)

        path = download_thumbnail(
            image,
            {},
            region,
            f"satellite_builtup_overlay_{year}.png",
            dimensions=1700,
        )
        paths.append(path)

    images = [Image.open(p).convert("RGB") for p in paths]
    thumb_w, thumb_h = 620, 500
    regular, bold, small, tiny = get_fonts()

    panel = Image.new("RGB", (thumb_w * 2 + 40, thumb_h * 2 + 130), "white")
    draw = ImageDraw.Draw(panel)
    draw.text(
        (panel.width // 2, 18),
        "Kaduna Built-up Expansion on Satellite Imagery",
        anchor="ma",
        fill="black",
        font=bold,
    )
    draw.text(
        (panel.width // 2, 54),
        "Red overlay = dense GHSL built-up cells",
        anchor="ma",
        fill="#333333",
        font=small,
    )

    for idx, (img, year) in enumerate(zip(images, PANEL_YEARS)):
        img.thumbnail((thumb_w, thumb_h - 46))
        x = 20 + (idx % 2) * thumb_w
        y = 88 + (idx // 2) * thumb_h
        panel.paste(img, (x + (thumb_w - img.width) // 2, y + 34))
        draw.text((x + thumb_w // 2, y), str(year), anchor="ma", fill="black", font=bold)

    draw.rectangle((20, panel.height - 34, 42, panel.height - 14), fill="#ff1f1f")
    draw.text(
        (50, panel.height - 34),
        "Dense built-up cells (GHSL >= 2,500 m² per 100 m cell) over Landsat dry-season RGB composite",
        fill="#333333",
        font=tiny,
    )

    output_path = OUTPUT_DIR / "figure_5_satellite_builtup_comparison_2000_2025.png"
    panel.save(output_path)
    print("Saved:", output_path)
    return output_path


fig5_path = make_satellite_builtup_panel()
Image.open(fig5_path)
"""))

cells.append(md(r"""
## 12. Figure 6 — Built-up growth intensity map, 2000–2025
"""))

cells.append(code(r"""
growth = built_images[2025].subtract(built_images[2000]).max(0).rename("growth_m2")

growth_vis = {
    "min": 0,
    "max": 6000,
    "palette": ["ffffff", "fee5d9", "fcae91", "fb6a4a", "cb181d"],
}

growth_image = make_white_map(growth, boundary, growth_vis)
fig6_path = download_thumbnail(
    growth_image,
    {},
    region,
    "figure_6_builtup_growth_intensity_2000_2025.png",
    dimensions=1900,
)

add_title_and_frame(
    fig6_path,
    "Built-up Growth Intensity, 2000-2025",
    "Darker red cells indicate larger increase in built-up surface",
    "Source: GHSL GHS-BUILT-S P2023A, 100 m built-up surface; AOI: four metropolitan LGAs",
)
add_lga_labels(fig6_path, get_lga_label_data(kaduna_metro), get_bbox(region))
add_north_arrow_scale(fig6_path)

Image.open(fig6_path)
"""))

cells.append(md(r"""
## 13. Figure 7 — Growth hotspot map and top growth-location table
"""))

cells.append(code(r"""
grid = geemap.fishnet(aoi, rows=40, cols=40)
growth_sqkm = growth.divide(1e6)


def add_growth_area(feature):
    value = (
        growth_sqkm
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=feature.geometry(),
            scale=100,
            maxPixels=1e13,
            bestEffort=True,
        )
        .get("growth_m2")
    )

    centroid = feature.geometry().centroid(10)
    matched_lgas = kaduna_metro.filterBounds(centroid)
    lga = ee.Algorithms.If(
        matched_lgas.size().gt(0),
        ee.Feature(matched_lgas.first()).get("ADM2_NAME"),
        "Boundary/Unassigned",
    )

    return feature.set(
        {
            "growth_sqkm": value,
            "lon": centroid.coordinates().get(0),
            "lat": centroid.coordinates().get(1),
            "lga": lga,
        }
    )


hotspot_grid = grid.map(add_growth_area).filter(ee.Filter.notNull(["growth_sqkm"]))

top = hotspot_grid.sort("growth_sqkm", False).limit(12)
top_features = top.getInfo()["features"]

top_rows = []
for i, feature in enumerate(top_features, start=1):
    p = feature["properties"]
    top_rows.append(
        {
            "rank": i,
            "lga": p.get("lga"),
            "latitude": round(float(p.get("lat")), 5),
            "longitude": round(float(p.get("lon")), 5),
            "growth_sqkm_2000_2025": round(float(p.get("growth_sqkm")), 3),
        }
    )

top_growth_df = pd.DataFrame(top_rows)
top_growth_csv = OUTPUT_DIR / "top_growth_locations_ghsl_2000_2025.csv"
top_growth_df.to_csv(top_growth_csv, index=False)

print(top_growth_df)
print("Saved:", top_growth_csv)

values = hotspot_grid.aggregate_array("growth_sqkm")
p50 = ee.Number(values.reduce(ee.Reducer.percentile([50])))
p75 = ee.Number(values.reduce(ee.Reducer.percentile([75])))
p90 = ee.Number(values.reduce(ee.Reducer.percentile([90])))
p97 = ee.Number(values.reduce(ee.Reducer.percentile([97])))


def classify_hotspot(feature):
    g = ee.Number(feature.get("growth_sqkm"))
    cls = ee.Algorithms.If(
        g.lte(0),
        1,
        ee.Algorithms.If(
            g.lt(p50),
            2,
            ee.Algorithms.If(
                g.lt(p75),
                3,
                ee.Algorithms.If(
                    g.lt(p90),
                    4,
                    ee.Algorithms.If(g.lt(p97), 5, 6),
                ),
            ),
        ),
    )
    return feature.set("hotspot_class", cls)


hotspot_classified = hotspot_grid.map(classify_hotspot)
hotspot_image = hotspot_classified.reduceToImage(["hotspot_class"], ee.Reducer.first()).clip(aoi)

hotspot_vis = {
    "min": 1,
    "max": 6,
    "palette": ["f7f7f7", "fee5d9", "fcae91", "fb6a4a", "cb181d", "67000d"],
}

hotspot_path = download_thumbnail(
    make_white_map(hotspot_image, boundary, hotspot_vis),
    {},
    region,
    "figure_7_growth_hotspot_map_labeled.png",
    dimensions=1900,
)

add_title_and_frame(
    hotspot_path,
    "Urban Growth Hotspot Zones, 2000-2025",
    "Grid cells ranked by GHSL built-up surface increase",
    "Source: GHSL GHS-BUILT-S P2023A; labels show top hotspot cells by growth area",
)
add_lga_labels(hotspot_path, get_lga_label_data(kaduna_metro), get_bbox(region))
add_north_arrow_scale(hotspot_path)

# Add numbered hotspot labels.
regular, bold, small, tiny = get_fonts()
img = Image.open(hotspot_path).convert("RGB")
draw = ImageDraw.Draw(img)
map_top = 82
map_bottom = img.height - 44
map_height = map_bottom - map_top
bbox = get_bbox(region)

for row in top_rows[:8]:
    x, y = lonlat_to_pixel(row["longitude"], row["latitude"], bbox, img.width, map_height)
    y += map_top
    r = 16
    draw.ellipse((x - r, y - r, x + r, y + r), fill="#111111", outline="white", width=3)
    draw.text((x, y), str(row["rank"]), anchor="mm", fill="white", font=small)

img.save(hotspot_path)

Image.open(hotspot_path)
"""))

cells.append(md(r"""
## 14. Figure 8 — Top growth locations table
"""))

cells.append(code(r"""
fig, ax = plt.subplots(figsize=(10, 4.2))
ax.axis("off")

table_df = top_growth_df.head(8).copy()
table = ax.table(
    cellText=table_df.values,
    colLabels=table_df.columns,
    loc="center",
    cellLoc="center",
)
table.auto_set_font_size(False)
table.set_fontsize(8)
table.scale(1, 1.45)

ax.set_title(
    "Top GHSL Built-up Growth Locations in Kaduna Metropolitan Area",
    weight="bold",
    pad=14,
)

fig8_path = OUTPUT_DIR / "figure_8_top_growth_locations_table.png"
plt.savefig(fig8_path, dpi=300, bbox_inches="tight")
plt.show()

print("Saved:", fig8_path)
"""))

cells.append(md(r"""
## 15. Figure 9 — Recent 2025 satellite and land-cover context

This figure is for contextual interpretation only. The long-term built-up trend and hotspot analysis come from GHSL.
"""))

cells.append(code(r"""
rgb_vis = {"bands": ["red", "green", "blue"], "min": 0.02, "max": 0.30}

sat_2025_path = download_thumbnail(
    landsat_composite(2025, aoi),
    rgb_vis,
    region,
    "satellite_rgb_2025_context.png",
    dimensions=1600,
)

dynamic_world_2025 = (
    ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
    .filterBounds(aoi)
    .filterDate("2025-01-01", "2025-12-31")
    .select("label")
    .mode()
    .clip(aoi)
)

dw_vis = {
    "min": 0,
    "max": 8,
    "palette": [
        "419bdf",  # water
        "397d49",  # trees
        "88b053",  # grass
        "7a87c6",  # flooded vegetation
        "e49635",  # crops
        "dfc35a",  # shrub and scrub
        "c4281b",  # built
        "a59b8f",  # bare
        "b39fe1",  # snow/ice
    ],
}

dw_2025_path = download_thumbnail(
    dynamic_world_2025,
    dw_vis,
    region,
    "dynamic_world_2025_context.png",
    dimensions=1600,
)

fig9_path = create_panel(
    [sat_2025_path, dw_2025_path],
    ["Landsat RGB Composite: 2025", "Dynamic World Context: 2025"],
    "figure_9_recent_satellite_landcover_context_2025.png",
    "Recent Satellite and Land-cover Context",
    cols=2,
    figsize=(12, 6),
)
"""))

cells.append(md(r"""
## 16. Create contact sheet for visual QA
"""))

cells.append(code(r"""
def make_contact_sheet():
    files = sorted(OUTPUT_DIR.glob("figure_*.png"))
    thumb_w, thumb_h = 410, 300
    regular, bold, small, tiny = get_fonts()
    thumbs = []

    for path in files:
        img = Image.open(path).convert("RGB")
        img.thumbnail((thumb_w, thumb_h - 30))
        canvas = Image.new("RGB", (thumb_w, thumb_h), "white")
        canvas.paste(img, ((thumb_w - img.width) // 2, 4))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, thumb_h - 22), path.name, fill="black", font=tiny)
        thumbs.append(canvas)

    cols = 2
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "#eeeeee")

    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % cols) * thumb_w, (i // cols) * thumb_h))

    out = OUTPUT_DIR / "figure_0_contact_sheet_visual_qa.png"
    sheet.save(out)
    print("Saved:", out)
    return out


contact_sheet_path = make_contact_sheet()
Image.open(contact_sheet_path)
"""))

cells.append(md(r"""
## 17. Suggested interpretation for paper or workshop talk
"""))

cells.append(md(r"""
### Key results

The GHSL-based analysis shows that built-up surface in the Kaduna metropolitan study area increased from approximately **62.1 km² in 2000** to approximately **129.7 km² in 2025**. This indicates that the built-up footprint more than doubled over the 25-year period.

The highest growth grid cells are concentrated around the central metropolitan belt and adjacent expansion zones in **Chikun**, **Igabi**, **Kaduna South**, and **Kaduna North**. These areas are important for climate-resilient urban planning because rapid built-up expansion can increase pressure on drainage, roads, public services, open space, and peri-urban land.

### Methodological note

The earlier Random Forest LULC experiment is useful as an exploratory GeoAI workflow, but it produced unrealistic historical built-up estimates when 2020 reference labels were applied backward to older Landsat imagery. For publication and presentation, GHSL built-up surface is used as the stronger long-term built-up evidence layer, while Landsat and Dynamic World are used for visual and recent land-cover context.
"""))

cells.append(md(r"""
## 18. Export all outputs as ZIP
"""))

cells.append(code(r"""
zip_path = shutil.make_archive(
    base_name="/content/kaduna_publication_outputs",
    format="zip",
    root_dir=OUTPUT_DIR,
)

print("Zipped outputs:", zip_path)

try:
    from google.colab import files
    files.download(zip_path)
except Exception as exc:
    print("Download helper unavailable outside Colab:", exc)
"""))

cells.append(md(r"""
## Dataset citations / sources

- GHSL GHS-BUILT-S P2023A: `JRC/GHSL/P2023A/GHS_BUILT_S/{year}`
- Dynamic World V1: `GOOGLE/DYNAMICWORLD/V1`
- Landsat Collection 2 Level 2: `LANDSAT/LT05/C02/T1_L2`, `LANDSAT/LE07/C02/T1_L2`, `LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2`
- FAO GAUL administrative boundaries: `FAO/GAUL/2015/level1`, `FAO/GAUL/2015/level2`
"""))

notebook = {
    "cells": cells,
    "metadata": {
        "colab": {"provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Wrote {OUT.resolve()} with {len(cells)} cells")
