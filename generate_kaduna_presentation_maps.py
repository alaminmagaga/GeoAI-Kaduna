from pathlib import Path
import math
import shutil

import ee
import geemap
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont


PROJECT_ID = "geoai-502123"
OUTPUT_DIR = Path("kaduna_presentation_maps")
YEARS = [2000, 2005, 2010, 2015, 2020, 2025]
PANEL_YEARS = [2000, 2010, 2020, 2025]


def init():
    ee.Initialize(project=PROJECT_ID)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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


def fonts():
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    regular = ImageFont.truetype(candidates[0], 22) if Path(candidates[0]).exists() else ImageFont.load_default()
    bold = ImageFont.truetype(candidates[1], 28) if Path(candidates[1]).exists() else regular
    small = ImageFont.truetype(candidates[0], 17) if Path(candidates[0]).exists() else regular
    tiny = ImageFont.truetype(candidates[0], 14) if Path(candidates[0]).exists() else regular
    return regular, bold, small, tiny


def download_thumbnail(image, vis_params, region, filename, dimensions=1800):
    output_path = OUTPUT_DIR / filename
    if output_path.exists() and output_path.stat().st_size > 0:
        print(f"Using cached: {output_path}", flush=True)
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
            print(f"Saved: {output_path}", flush=True)
            return output_path
        print(f"Thumbnail failed for {filename} at {dim}px: HTTP {response.status_code}", flush=True)
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
    return ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{year}").select("built_surface").clip(aoi)


def make_white_map(layer, boundary, vis_params=None):
    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    layer_vis = layer.visualize(**vis_params) if vis_params else layer
    boundary_vis = boundary.visualize(min=0, max=1, palette=["00000000", "222222"])
    return white.blend(layer_vis).blend(boundary_vis)


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
        {"name": f["properties"]["name"], "lon": f["properties"]["lon"], "lat": f["properties"]["lat"]}
        for f in features.getInfo()["features"]
    ]


def add_title_and_frame(path, title, subtitle=None, source=None):
    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    pad_top = 78 if subtitle else 56
    pad_bottom = 42 if source else 22
    canvas = Image.new("RGB", (img.width, img.height + pad_top + pad_bottom), "white")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(img, (0, pad_top))
    draw.text((img.width // 2, 16), title, anchor="ma", fill="black", font=bold)
    if subtitle:
        draw.text((img.width // 2, 50), subtitle, anchor="ma", fill="#333333", font=small)
    draw.rectangle((0, pad_top, img.width - 1, pad_top + img.height - 1), outline="#222222", width=2)
    if source:
        draw.text((18, img.height + pad_top + 12), source, fill="#333333", font=tiny)
    canvas.save(path)
    return path


def add_lga_labels(path, label_data, bbox):
    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size
    # Account for title padding if already added.
    map_top = 78
    map_bottom = height - 42
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
        text = item["name"]
        tx, ty = x + dx, y + dy
        draw.text((tx + 2, ty + 2), text, anchor="mm", fill="white", font=small)
        draw.text((tx, ty), text, anchor="mm", fill="#111111", font=small)
    img.save(path)
    return path


def add_north_arrow_scale(path):
    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    # North arrow.
    cx, cy = w - 75, 135
    draw.polygon([(cx, cy - 42), (cx - 16, cy + 25), (cx, cy + 10), (cx + 16, cy + 25)], fill="black")
    draw.text((cx, cy - 64), "N", anchor="mm", fill="black", font=small)
    # Simple visual scale bar, approximate for presentation.
    x0, y0 = 58, h - 78
    bar_w = 220
    seg = bar_w // 2
    draw.rectangle((x0, y0, x0 + seg, y0 + 12), fill="black", outline="black")
    draw.rectangle((x0 + seg, y0, x0 + bar_w, y0 + 12), fill="white", outline="black")
    draw.text((x0, y0 + 20), "0", fill="black", font=tiny)
    draw.text((x0 + seg, y0 + 20), "10", anchor="ma", fill="black", font=tiny)
    draw.text((x0 + bar_w, y0 + 20), "20 km", anchor="ra", fill="black", font=tiny)
    img.save(path)
    return path


def make_study_area_map(kaduna_metro, aoi, region, bbox):
    boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
    fill = ee.Image().byte().paint(kaduna_metro, 1)
    image = make_white_map(fill, boundary, {"min": 0, "max": 1, "palette": ["ffffff", "d9d9d9"]})
    path = download_thumbnail(image, {}, region, "01_verified_kaduna_metropolitan_lgas.png", 1900)
    add_title_and_frame(
        path,
        "Kaduna Metropolitan Study Area",
        "Administrative extraction: Kaduna North, Kaduna South, Chikun, and Igabi LGAs",
        "Boundary source: FAO GAUL 2015 Level 2 administrative boundaries; CRS display: WGS84",
    )
    add_lga_labels(path, get_lga_label_data(kaduna_metro), bbox)
    add_north_arrow_scale(path)
    return path


def make_satellite_builtup_panel(kaduna_metro, aoi, region):
    boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
    paths = []
    rgb_vis = {"bands": ["red", "green", "blue"], "min": 0.02, "max": 0.30}
    for year in PANEL_YEARS:
        rgb = landsat_composite(year, aoi).visualize(**rgb_vis)
        # Use a threshold so tiny fractional built-up estimates do not overwhelm
        # the satellite basemap. The full area trend still uses continuous GHSL.
        built = ghsl_built_surface(year, aoi).gte(2500).selfMask().visualize(palette=["ff1f1f"])
        boundary_vis = boundary.visualize(min=0, max=1, palette=["00000000", "111111"])
        image = rgb.blend(built).blend(boundary_vis)
        paths.append(download_thumbnail(image, {}, region, f"satellite_builtup_overlay_{year}.png", 1700))

    images = [Image.open(p).convert("RGB") for p in paths]
    thumb_w, thumb_h = 620, 500
    regular, bold, small, tiny = fonts()
    panel = Image.new("RGB", (thumb_w * 2 + 40, thumb_h * 2 + 130), "white")
    draw = ImageDraw.Draw(panel)
    draw.text((panel.width // 2, 18), "Kaduna Built-up Expansion on Satellite Imagery", anchor="ma", fill="black", font=bold)
    draw.text((panel.width // 2, 54), "Red overlay = GHSL built-up surface presence", anchor="ma", fill="#333333", font=small)
    for idx, (img, year) in enumerate(zip(images, PANEL_YEARS)):
        img.thumbnail((thumb_w, thumb_h - 46))
        x = 20 + (idx % 2) * thumb_w
        y = 88 + (idx // 2) * thumb_h
        panel.paste(img, (x + (thumb_w - img.width) // 2, y + 34))
        draw.text((x + thumb_w // 2, y), str(year), anchor="ma", fill="black", font=bold)
    draw.rectangle((20, panel.height - 34, 42, panel.height - 14), fill="#ff1f1f")
    draw.text((50, panel.height - 34), "Dense built-up cells (GHSL >= 2,500 m² per 100 m cell) over Landsat dry-season RGB composite", fill="#333333", font=tiny)
    out = OUTPUT_DIR / "02_satellite_builtup_comparison_2000_2025.png"
    panel.save(out)
    print(f"Saved: {out}", flush=True)
    return out


def make_growth_maps_and_table(kaduna_metro, aoi, region, bbox):
    boundary = ee.Image().byte().paint(kaduna_metro, 1, 2)
    growth = ghsl_built_surface(2025, aoi).subtract(ghsl_built_surface(2000, aoi)).max(0).rename("growth_m2")
    growth_vis = {"min": 0, "max": 6000, "palette": ["ffffff", "fee5d9", "fcae91", "fb6a4a", "cb181d"]}
    growth_image = make_white_map(growth, boundary, growth_vis)
    growth_path = download_thumbnail(growth_image, {}, region, "03_growth_intensity_2000_2025.png", 1900)
    add_title_and_frame(
        growth_path,
        "Built-up Growth Intensity, 2000-2025",
        "Darker red cells indicate larger increase in built-up surface",
        "Source: GHSL GHS-BUILT-S P2023A, 100 m built-up surface; AOI: four metropolitan LGAs",
    )
    add_lga_labels(growth_path, get_lga_label_data(kaduna_metro), bbox)
    add_north_arrow_scale(growth_path)

    grid = geemap.fishnet(aoi, rows=40, cols=40)
    growth_sqkm = growth.divide(1e6)

    def add_growth_area(feature):
        value = growth_sqkm.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=feature.geometry(),
            scale=100,
            maxPixels=1e13,
            bestEffort=True,
        ).get("growth_m2")
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
    rows = []
    for i, feature in enumerate(top_features, start=1):
        p = feature["properties"]
        rows.append(
            {
                "rank": i,
                "lga": p.get("lga"),
                "latitude": round(float(p.get("lat")), 5),
                "longitude": round(float(p.get("lon")), 5),
                "growth_sqkm_2000_2025": round(float(p.get("growth_sqkm")), 3),
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "top_growth_locations_ghsl_2000_2025.csv", index=False)

    values = hotspot_grid.aggregate_array("growth_sqkm")
    p50 = ee.Number(values.reduce(ee.Reducer.percentile([50])))
    p75 = ee.Number(values.reduce(ee.Reducer.percentile([75])))
    p90 = ee.Number(values.reduce(ee.Reducer.percentile([90])))
    p97 = ee.Number(values.reduce(ee.Reducer.percentile([97])))

    def classify(feature):
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

    hotspot_image = hotspot_grid.map(classify).reduceToImage(["hotspot_class"], ee.Reducer.first()).clip(aoi)
    hotspot_vis = {"min": 1, "max": 6, "palette": ["f7f7f7", "fee5d9", "fcae91", "fb6a4a", "cb181d", "67000d"]}
    hotspot_path = download_thumbnail(make_white_map(hotspot_image, boundary, hotspot_vis), {}, region, "04_growth_hotspot_map_labeled.png", 1900)
    add_title_and_frame(
        hotspot_path,
        "Urban Growth Hotspot Zones, 2000-2025",
        "Grid cells ranked by GHSL built-up surface increase",
        "Source: GHSL GHS-BUILT-S P2023A; labels show top hotspot cells by growth area",
    )
    add_lga_labels(hotspot_path, get_lga_label_data(kaduna_metro), bbox)
    add_north_arrow_scale(hotspot_path)

    img = Image.open(hotspot_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    regular, bold, small, tiny = fonts()
    map_top = 78
    map_bottom = img.height - 42
    map_height = map_bottom - map_top
    for row in rows[:8]:
        x, y = lonlat_to_pixel(row["longitude"], row["latitude"], bbox, img.width, map_height)
        y += map_top
        r = 16
        draw.ellipse((x - r, y - r, x + r, y + r), fill="#111111", outline="white", width=3)
        draw.text((x, y), str(row["rank"]), anchor="mm", fill="white", font=small)
    img.save(hotspot_path)

    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.axis("off")
    table_df = df.head(8).copy()
    table = ax.table(cellText=table_df.values, colLabels=table_df.columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.45)
    ax.set_title("Top GHSL Built-up Growth Locations in Kaduna Metropolitan Area", weight="bold", pad=14)
    table_path = OUTPUT_DIR / "05_top_growth_locations_table.png"
    fig.savefig(table_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return growth_path, hotspot_path, table_path


def make_area_trend():
    csv_path = Path("kaduna_geoai_figures_corrected/corrected_ghsl_builtup_area_statistics.csv")
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(df["year"], df["builtup_area_sqkm_ghsl"], marker="o", linewidth=2.6, color="#1f77b4")
    ax.fill_between(df["year"], df["builtup_area_sqkm_ghsl"], color="#1f77b4", alpha=0.12)
    ax.set_xlabel("Year")
    ax.set_ylabel("Built-up surface area (sq. km)")
    ax.set_title("Kaduna Built-up Surface Increased from 62.1 km² to 129.7 km²", weight="bold")
    ax.grid(True, linestyle="--", alpha=0.45)
    for x, y in zip(df["year"], df["builtup_area_sqkm_ghsl"]):
        ax.text(x, y + 1.5, f"{y:.1f}", fontsize=8, ha="center")
    fig.tight_layout()
    out = OUTPUT_DIR / "06_ghsl_builtup_growth_trend.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}", flush=True)
    return out


def make_contact_sheet():
    files = sorted(OUTPUT_DIR.glob("0*.png"))
    thumb_w, thumb_h = 410, 300
    regular, bold, small, tiny = fonts()
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
    out = OUTPUT_DIR / "00_contact_sheet_presentation_maps.png"
    sheet.save(out)
    print(f"Saved: {out}", flush=True)


def make_kaduna_state_context_map(kaduna_metro, region, bbox):
    gaul_l1 = ee.FeatureCollection("FAO/GAUL/2015/level1")
    gaul_l2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    kaduna_state = gaul_l1.filter(
        ee.Filter.And(ee.Filter.eq("ADM0_NAME", "Nigeria"), ee.Filter.eq("ADM1_NAME", "Kaduna"))
    )
    kaduna_lgas = gaul_l2.filter(
        ee.Filter.And(ee.Filter.eq("ADM0_NAME", "Nigeria"), ee.Filter.eq("ADM1_NAME", "Kaduna"))
    )
    state_region = kaduna_state.geometry().bounds(10).getInfo()["coordinates"]

    state_fill = ee.Image().byte().paint(kaduna_state, 1)
    state_boundary = ee.Image().byte().paint(kaduna_state, 1, 3)
    lga_boundaries = ee.Image().byte().paint(kaduna_lgas, 1, 1)
    metro_fill = ee.Image().byte().paint(kaduna_metro, 1)
    metro_boundary = ee.Image().byte().paint(kaduna_metro, 1, 3)

    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    state_layer = state_fill.visualize(min=0, max=1, palette=["ffffff", "eeeeee"])
    lga_layer = lga_boundaries.visualize(min=0, max=1, palette=["00000000", "bdbdbd"])
    metro_layer = metro_fill.visualize(min=0, max=1, palette=["00000000", "ff6b35"])
    metro_outline = metro_boundary.visualize(min=0, max=1, palette=["00000000", "b30000"])
    state_outline = state_boundary.visualize(min=0, max=1, palette=["00000000", "111111"])
    image = white.blend(state_layer).blend(lga_layer).blend(metro_layer).blend(metro_outline).blend(state_outline)

    path = download_thumbnail(image, {}, state_region, "00_kaduna_state_with_metropolitan_inset.png", 1800)
    add_title_and_frame(
        path,
        "Kaduna Metropolitan Area Within Kaduna State",
        "Highlighted LGAs: Kaduna North, Kaduna South, Chikun, and Igabi",
        "Boundary source: FAO GAUL 2015 Level 1 and Level 2 administrative boundaries",
    )

    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    state_bbox = get_bbox(state_region)
    map_top = 78
    map_bottom = img.height - 42
    map_height = map_bottom - map_top

    # Label metropolitan LGAs using the state-map coordinate frame.
    for item in get_lga_label_data(kaduna_metro):
        x, y = lonlat_to_pixel(item["lon"], item["lat"], state_bbox, img.width, map_height)
        y += map_top
        draw.text((x + 2, y + 2), item["name"], anchor="mm", fill="white", font=tiny)
        draw.text((x, y), item["name"], anchor="mm", fill="#111111", font=tiny)

    # Draw a small legend.
    lx, ly = 34, img.height - 128
    draw.rectangle((lx, ly, lx + 24, ly + 18), fill="#ff6b35", outline="#b30000")
    draw.text((lx + 34, ly - 1), "Kaduna metropolitan LGAs", fill="#111111", font=tiny)
    draw.rectangle((lx, ly + 30, lx + 24, ly + 48), fill="#eeeeee", outline="#777777")
    draw.text((lx + 34, ly + 29), "Other Kaduna State LGAs", fill="#111111", font=tiny)
    img.save(path)
    add_north_arrow_scale(path)
    return path


def main():
    init()
    kaduna_metro, aoi = get_kaduna_metro()
    region = aoi.bounds(10).getInfo()["coordinates"]
    bbox = get_bbox(region)
    count = kaduna_metro.size().getInfo()
    names = kaduna_metro.aggregate_array("ADM2_NAME").getInfo()
    print(f"Extracted {count} metropolitan LGA features: {names}", flush=True)

    make_kaduna_state_context_map(kaduna_metro, region, bbox)
    make_study_area_map(kaduna_metro, aoi, region, bbox)
    make_satellite_builtup_panel(kaduna_metro, aoi, region)
    make_growth_maps_and_table(kaduna_metro, aoi, region, bbox)
    make_area_trend()
    make_contact_sheet()
    shutil.make_archive(str(OUTPUT_DIR), "zip", OUTPUT_DIR)
    print(f"Finished presentation maps: {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
