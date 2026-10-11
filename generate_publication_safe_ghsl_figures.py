from pathlib import Path
import math
import shutil

import ee
import geemap
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont


PROJECT_ID = "geoai-502123"
OUTPUT_DIR = Path("kaduna_geoai_figures_corrected")
YEARS = [2000, 2005, 2010, 2015, 2020, 2025]
PANEL_YEARS = [2000, 2010, 2020, 2025]


def init_ee():
    ee.Initialize(project=PROJECT_ID)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_aoi():
    gaul_l2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    kaduna_metro = gaul_l2.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", "Nigeria"),
            ee.Filter.eq("ADM1_NAME", "Kaduna"),
            ee.Filter.inList("ADM2_NAME", ["Kaduna North", "Kaduna South", "Chikun", "Igabi"]),
        )
    )
    return kaduna_metro, kaduna_metro.geometry()


def download_thumbnail(image, vis_params, region, filename, dimensions=1800):
    output_path = OUTPUT_DIR / filename
    if output_path.exists() and output_path.stat().st_size > 0:
        print(f"Using cached: {output_path}", flush=True)
        return output_path

    styled = image.visualize(**vis_params)
    last_status = None
    for dim in [dimensions, 1500, 1200, 900, 700]:
        if dim > dimensions:
            continue
        try:
            url = styled.getThumbURL({"region": region, "dimensions": dim, "format": "png"})
            response = requests.get(url, timeout=180)
            last_status = response.status_code
            if response.status_code == 200:
                output_path.write_bytes(response.content)
                print(f"Saved: {output_path}", flush=True)
                return output_path
            print(f"Thumbnail failed for {filename} at {dim}px: HTTP {response.status_code}", flush=True)
        except Exception as exc:
            print(f"Thumbnail failed for {filename} at {dim}px: {exc}", flush=True)
    raise RuntimeError(f"Could not download {filename}; last status={last_status}")


def landsat_composite(year, aoi):
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

    return collection.median().clip(aoi)


def ghsl_built_surface(year, aoi):
    # GHS-BUILT-S stores built-up surface in square meters per grid cell.
    return (
        ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{year}")
        .select("built_surface")
        .clip(aoi)
    )


def map_on_white(image, vis_params, boundary):
    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    layer = image.visualize(**vis_params)
    outline = boundary.visualize(min=0, max=1, palette=["00000000", "222222"])
    return white.blend(layer).blend(outline)


def create_panel(image_paths, titles, filename, main_title=None, cols=2, figsize=(12, 9)):
    rows = math.ceil(len(image_paths) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=figsize)
    axes = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for ax, path, title in zip(axes, image_paths, titles):
        img = Image.open(path)
        ax.imshow(img)
        ax.set_title(title, fontsize=12, weight="bold")
        ax.axis("off")
    for ax in axes[len(image_paths) :]:
        ax.axis("off")
    if main_title:
        fig.suptitle(main_title, fontsize=15, weight="bold")
    fig.tight_layout()
    out = OUTPUT_DIR / filename
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved panel: {out}", flush=True)
    return out


def make_contact_sheet():
    files = sorted(OUTPUT_DIR.glob("corrected_figure_*.png"))
    if not files:
        return None

    thumb_w, thumb_h = 380, 280
    font = ImageFont.load_default()
    thumbs = []
    for path in files:
        img = Image.open(path).convert("RGB")
        img.thumbnail((thumb_w, thumb_h - 28))
        canvas = Image.new("RGB", (thumb_w, thumb_h), "white")
        canvas.paste(img, ((thumb_w - img.width) // 2, 4))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, thumb_h - 22), path.name, fill="black", font=font)
        thumbs.append(canvas)

    cols = 3
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "#eeeeee")
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % cols) * thumb_w, (i // cols) * thumb_h))

    out = OUTPUT_DIR / "corrected_contact_sheet.png"
    sheet.save(out)
    print(f"Saved contact sheet: {out}", flush=True)
    return out


def main():
    init_ee()
    kaduna_metro, aoi = get_aoi()
    region = aoi.bounds(10).getInfo()["coordinates"]

    boundary = ee.Image().byte().paint(kaduna_metro, 1, 3)
    aoi_fill = ee.Image().byte().paint(kaduna_metro, 1)
    study_area = (
        ee.Image.constant(0)
        .visualize(min=0, max=1, palette=["ffffff"])
        .blend(aoi_fill.visualize(min=0, max=1, palette=["ffffff", "d9d9d9"]))
        .blend(boundary.visualize(min=0, max=1, palette=["00000000", "111111"]))
    )
    download_thumbnail(
        study_area,
        {},
        region,
        "corrected_figure_1_study_area_boundary.png",
        dimensions=1600,
    )

    built_images = {year: ghsl_built_surface(year, aoi) for year in YEARS}
    area_rows = []
    for year, image in built_images.items():
        area_sqkm = image.divide(1e6).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=aoi,
            scale=100,
            maxPixels=1e13,
            bestEffort=True,
        ).get("built_surface")
        area_rows.append({"year": year, "builtup_area_sqkm_ghsl": area_sqkm.getInfo()})

    area_df = pd.DataFrame(area_rows)
    area_df["change_sqkm"] = area_df["builtup_area_sqkm_ghsl"].diff()
    area_df["percentage_change"] = area_df["builtup_area_sqkm_ghsl"].pct_change() * 100
    area_df.to_csv(OUTPUT_DIR / "corrected_ghsl_builtup_area_statistics.csv", index=False)
    print(area_df, flush=True)

    built_vis = {
        "min": 0,
        "max": 10000,
        "palette": ["ffffff", "fee8c8", "fdbb84", "e34a33", "67000d"],
    }
    built_paths = []
    for year in PANEL_YEARS:
        built_paths.append(
            download_thumbnail(
                built_images[year],
                built_vis,
                region,
                f"ghsl_built_surface_{year}.png",
                dimensions=1600,
            )
        )
    create_panel(
        built_paths,
        [f"GHSL Built-up Surface: {year}" for year in PANEL_YEARS],
        "corrected_figure_2_ghsl_built_surface_panel.png",
        "Built-up Surface Change in Kaduna Metropolis (GHSL)",
        cols=2,
    )

    baseline = built_images[2000]
    final = built_images[2025]
    growth = final.subtract(baseline).max(0).rename("built_surface_growth").clip(aoi)
    growth_vis = {
        "min": 0,
        "max": 6000,
        "palette": ["ffffff", "fee5d9", "fcae91", "fb6a4a", "cb181d"],
    }
    download_thumbnail(
        map_on_white(growth, growth_vis, boundary),
        {},
        region,
        "corrected_figure_3_ghsl_builtup_growth_2000_2025.png",
        dimensions=1700,
    )

    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(area_df["year"], area_df["builtup_area_sqkm_ghsl"], marker="o", linewidth=2)
    ax.set_xlabel("Year")
    ax.set_ylabel("Built-up surface area (sq. km, GHSL)")
    ax.set_title("GHSL Built-up Surface Trend in Kaduna Metropolis", weight="bold")
    ax.grid(True, linestyle="--", alpha=0.45)
    for x, y in zip(area_df["year"], area_df["builtup_area_sqkm_ghsl"]):
        ax.text(x, y, f"{y:.1f}", fontsize=8, ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "corrected_figure_4_ghsl_builtup_area_trend.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    grid = geemap.fishnet(aoi, rows=40, cols=40)
    growth_area = growth.divide(1e6)

    def add_growth_area(feature):
        value = growth_area.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=feature.geometry(),
            scale=100,
            maxPixels=1e13,
            bestEffort=True,
        ).get("built_surface_growth")
        return feature.set("growth_sqkm", value)

    hotspot_grid = grid.map(add_growth_area).filter(ee.Filter.notNull(["growth_sqkm"]))
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
                ee.Algorithms.If(g.lt(p75), 3, ee.Algorithms.If(g.lt(p90), 4, ee.Algorithms.If(g.lt(p97), 5, 6))),
            ),
        )
        return feature.set("hotspot_class", cls)

    hotspot_grid = hotspot_grid.map(classify_hotspot)
    hotspot_image = hotspot_grid.reduceToImage(["hotspot_class"], ee.Reducer.first()).clip(aoi)
    hotspot_vis = {
        "min": 1,
        "max": 6,
        "palette": ["f7f7f7", "fee5d9", "fcae91", "fb6a4a", "cb181d", "67000d"],
    }
    download_thumbnail(
        map_on_white(hotspot_image, hotspot_vis, boundary),
        {},
        region,
        "corrected_figure_5_ghsl_growth_hotspot_map.png",
        dimensions=1700,
    )

    labels = ["No growth", "Low", "Moderate", "High", "Very high", "Top hotspot"]
    colors = ["#f7f7f7", "#fee5d9", "#fcae91", "#fb6a4a", "#cb181d", "#67000d"]
    patches = [mpatches.Patch(color=c, label=l) for c, l in zip(colors, labels)]
    fig, ax = plt.subplots(figsize=(9, 1.3))
    ax.legend(handles=patches, loc="center", ncol=3, frameon=False)
    ax.axis("off")
    fig.savefig(OUTPUT_DIR / "corrected_hotspot_legend.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    rgb_vis = {"bands": ["red", "green", "blue"], "min": 0.02, "max": 0.30}
    sat_2025 = download_thumbnail(
        landsat_composite(2025, aoi),
        rgb_vis,
        region,
        "corrected_satellite_rgb_2025.png",
        dimensions=1600,
    )
    dw = (
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
            "419bdf",
            "397d49",
            "88b053",
            "7a87c6",
            "e49635",
            "dfc35a",
            "c4281b",
            "a59b8f",
            "b39fe1",
        ],
    }
    dw_2025 = download_thumbnail(
        dw,
        dw_vis,
        region,
        "corrected_dynamic_world_2025.png",
        dimensions=1600,
    )
    create_panel(
        [sat_2025, dw_2025],
        ["Landsat RGB Composite: 2025", "Dynamic World Context: 2025"],
        "corrected_figure_6_recent_landcover_context_2025.png",
        "Recent Satellite and Land-cover Context",
        cols=2,
        figsize=(12, 6),
    )

    make_contact_sheet()
    shutil.make_archive(str(OUTPUT_DIR), "zip", OUTPUT_DIR)
    print(f"Finished corrected figures: {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
