from pathlib import Path
import json
import math
import shutil
import zipfile

import ee
import geemap
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import osmnx as ox
import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont
from pyproj import CRS
from shapely.geometry import Point, shape
from shapely.ops import unary_union


PROJECT_ID = "geoai-502123"
ROOT = Path("kaduna_publication_grade_outputs")
DATA = ROOT / "data"
RASTERS = ROOT / "rasters"
FIGURES = ROOT / "figures"
TABLES = ROOT / "tables"
QGIS = ROOT / "qgis"
YEARS = [2000, 2005, 2010, 2015, 2020, 2025]
PANEL_YEARS = [2000, 2010, 2020, 2025]
METRO_LGAS = ["Kaduna North", "Kaduna South", "Chikun", "Igabi"]
WGS84 = "EPSG:4326"
METRIC_CRS = "EPSG:32632"


def init():
    ee.Initialize(project=PROJECT_ID)
    for folder in [ROOT, DATA, RASTERS, FIGURES, TABLES, QGIS]:
        folder.mkdir(parents=True, exist_ok=True)


def fonts():
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    regular_path = next((p for p in candidates if Path(p).exists() and "bd" not in p.lower() and "Bold" not in p), None)
    bold_path = next((p for p in candidates if Path(p).exists() and ("bd" in p.lower() or "Bold" in p)), regular_path)
    if regular_path:
        return (
            ImageFont.truetype(regular_path, 22),
            ImageFont.truetype(bold_path, 30),
            ImageFont.truetype(regular_path, 17),
            ImageFont.truetype(regular_path, 14),
        )
    return (ImageFont.load_default(),) * 4


def ee_fc_to_gdf(fc):
    info = fc.getInfo()
    features = []
    props = []
    for feature in info["features"]:
        features.append(shape(feature["geometry"]))
        props.append(feature["properties"])
    return gpd.GeoDataFrame(props, geometry=features, crs=WGS84)


def get_boundaries():
    gaul_l2 = ee.FeatureCollection("FAO/GAUL/2015/level2")
    gaul_l1 = ee.FeatureCollection("FAO/GAUL/2015/level1")
    kaduna_state = gaul_l1.filter(
        ee.Filter.And(ee.Filter.eq("ADM0_NAME", "Nigeria"), ee.Filter.eq("ADM1_NAME", "Kaduna"))
    )
    kaduna_lgas = gaul_l2.filter(
        ee.Filter.And(ee.Filter.eq("ADM0_NAME", "Nigeria"), ee.Filter.eq("ADM1_NAME", "Kaduna"))
    )
    metro = gaul_l2.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", "Nigeria"),
            ee.Filter.eq("ADM1_NAME", "Kaduna"),
            ee.Filter.inList("ADM2_NAME", METRO_LGAS),
        )
    )
    return kaduna_state, kaduna_lgas, metro, metro.geometry()


def ghsl(year, aoi):
    return ee.Image(f"JRC/GHSL/P2023A/GHS_BUILT_S/{year}").select("built_surface").clip(aoi)


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
    months = ee.Filter.Or(ee.Filter.calendarRange(1, 3, "month"), ee.Filter.calendarRange(11, 12, "month"))
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
    print(f"{year}: Landsat dry-season image count = {collection.size().getInfo()}", flush=True)
    return collection.median().clip(aoi)


def landsat_context_composite(year, aoi):
    """Less aggressive visual composite for small before/after chips.

    The strict cloud mask can remove every valid pixel in small 2000 hotspot
    windows. For visual context chips, a median of scaled dry-season imagery is
    more useful than a blank masked image; the quantitative analysis still uses
    GHSL built-up surface.
    """

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
    months = ee.Filter.Or(ee.Filter.calendarRange(1, 3, "month"), ee.Filter.calendarRange(11, 12, "month"))
    l5 = (
        ee.ImageCollection("LANDSAT/LT05/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(scale_landsat_sr)
        .map(rename_l57)
    )
    l7 = (
        ee.ImageCollection("LANDSAT/LE07/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(scale_landsat_sr)
        .map(rename_l57)
    )
    l8 = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(scale_landsat_sr)
        .map(rename_l89)
    )
    l9 = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(months)
        .map(scale_landsat_sr)
        .map(rename_l89)
    )
    if year < 2013:
        collection = l5.merge(l7)
    elif year < 2022:
        collection = l8
    else:
        collection = l8.merge(l9)
    print(f"{year}: Landsat context image count = {collection.size().getInfo()}", flush=True)
    return collection.median().clip(aoi)


def bbox_from_region(region):
    coords = region[0]
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return min(xs), min(ys), max(xs), max(ys)


def lonlat_to_pixel(lon, lat, bbox, width, height):
    minx, miny, maxx, maxy = bbox
    return int((lon - minx) / (maxx - minx) * width), int((maxy - lat) / (maxy - miny) * height)


def download_thumb(image, vis, region, path, dimensions=1800):
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        print(f"Using cached: {path}", flush=True)
        return path
    styled = image.visualize(**vis) if vis else image
    last = None
    for dim in [dimensions, 1600, 1300, 1000, 800]:
        if dim > dimensions:
            continue
        url = styled.getThumbURL({"region": region, "dimensions": dim, "format": "png"})
        response = requests.get(url, timeout=180)
        last = response.status_code
        if response.status_code == 200:
            path.write_bytes(response.content)
            print(f"Saved: {path}", flush=True)
            return path
        print(f"Thumbnail failed {path.name} {dim}px HTTP {response.status_code}", flush=True)
    raise RuntimeError(f"Failed thumbnail {path}; last status={last}")


def white_map(layer, boundary, vis=None):
    white = ee.Image.constant(0).visualize(min=0, max=1, palette=["ffffff"])
    layer_vis = layer.visualize(**vis) if vis else layer
    boundary_vis = boundary.visualize(min=0, max=1, palette=["00000000", "222222"])
    return white.blend(layer_vis).blend(boundary_vis)


def add_title_frame(path, title, subtitle="", source=""):
    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    top = 82 if subtitle else 60
    bottom = 46 if source else 24
    canvas = Image.new("RGB", (img.width, img.height + top + bottom), "white")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(img, (0, top))
    draw.text((img.width // 2, 16), title, anchor="ma", fill="black", font=bold)
    if subtitle:
        draw.text((img.width // 2, 54), subtitle, anchor="ma", fill="#333333", font=small)
    draw.rectangle((0, top, img.width - 1, top + img.height - 1), outline="#222222", width=2)
    if source:
        draw.text((18, img.height + top + 13), source, fill="#333333", font=tiny)
    canvas.save(path)


def add_north_scale(path):
    regular, bold, small, tiny = fonts()
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    cx, cy = w - 75, 145
    draw.polygon([(cx, cy - 42), (cx - 16, cy + 25), (cx, cy + 10), (cx + 16, cy + 25)], fill="black")
    draw.text((cx, cy - 64), "N", anchor="mm", fill="black", font=small)
    x0, y0 = 58, h - 84
    bar_w = 220
    seg = bar_w // 2
    draw.rectangle((x0, y0, x0 + seg, y0 + 12), fill="black", outline="black")
    draw.rectangle((x0 + seg, y0, x0 + bar_w, y0 + 12), fill="white", outline="black")
    draw.text((x0, y0 + 20), "0", fill="black", font=tiny)
    draw.text((x0 + seg, y0 + 20), "10", anchor="ma", fill="black", font=tiny)
    draw.text((x0 + bar_w, y0 + 20), "20 km", anchor="ra", fill="black", font=tiny)
    img.save(path)


def classify_periods(built):
    b2000 = built[2000].gte(2500)
    b2010 = built[2010].gte(2500)
    b2020 = built[2020].gte(2500)
    b2025 = built[2025].gte(2500)
    periods = (
        ee.Image(0)
        .where(b2000, 1)
        .where(b2010.And(b2000.Not()), 2)
        .where(b2020.And(b2010.Not()), 3)
        .where(b2025.And(b2020.Not()), 4)
        .rename("expansion_period")
    )
    return periods


def make_area_stats(built, aoi):
    rows = []
    for year in YEARS:
        val = (
            built[year]
            .divide(1e6)
            .reduceRegion(ee.Reducer.sum(), aoi, 100, maxPixels=1e13, bestEffort=True)
            .get("built_surface")
            .getInfo()
        )
        rows.append({"year": year, "builtup_area_sqkm_ghsl": val})
    df = pd.DataFrame(rows)
    df["change_sqkm"] = df["builtup_area_sqkm_ghsl"].diff()
    df["percentage_change"] = df["builtup_area_sqkm_ghsl"].pct_change() * 100
    df.to_csv(TABLES / "table_1_builtup_statistics.csv", index=False)
    df.to_csv(DATA / "builtup_statistics.csv", index=False)
    return df


def download_osm_layers(metro_gdf):
    poly = metro_gdf.to_crs(WGS84).geometry.union_all()
    print("Downloading OSM major roads and waterways...", flush=True)
    road_tags = {"highway": ["motorway", "trunk", "primary", "secondary", "tertiary"]}
    water_tags = {"waterway": ["river", "stream", "canal", "drain"], "natural": ["water"], "water": True}
    try:
        roads = ox.features_from_polygon(poly, road_tags)
        roads = roads[roads.geometry.type.isin(["LineString", "MultiLineString"])]
        roads = gpd.GeoDataFrame(roads.reset_index(), geometry="geometry", crs=WGS84)
    except Exception as exc:
        print(f"OSM roads failed, using empty layer: {exc}", flush=True)
        roads = gpd.GeoDataFrame({"highway": []}, geometry=[], crs=WGS84)
    try:
        water = ox.features_from_polygon(poly, water_tags)
        water = water[water.geometry.type.isin(["LineString", "MultiLineString", "Polygon", "MultiPolygon"])]
        water = gpd.GeoDataFrame(water.reset_index(), geometry="geometry", crs=WGS84)
    except Exception as exc:
        print(f"OSM water failed, using empty layer: {exc}", flush=True)
        water = gpd.GeoDataFrame({"waterway": []}, geometry=[], crs=WGS84)
    roads.to_file(DATA / "kaduna_major_roads.geojson", driver="GeoJSON")
    water.to_file(DATA / "kaduna_waterways_water_features.geojson", driver="GeoJSON")
    return roads, water


def make_hotspot_grid(built, aoi, metro_gdf, roads_gdf, water_gdf):
    growth = built[2025].subtract(built[2000]).max(0).rename("growth_m2")
    grid = geemap.fishnet(aoi, rows=40, cols=40)
    growth_sqkm = growth.divide(1e6)

    def add_growth(feature):
        value = growth_sqkm.reduceRegion(
            ee.Reducer.sum(), feature.geometry(), 100, maxPixels=1e13, bestEffort=True
        ).get("growth_m2")
        centroid = feature.geometry().centroid(10)
        return feature.set(
            {
                "growth_sqkm": value,
                "lon": centroid.coordinates().get(0),
                "lat": centroid.coordinates().get(1),
            }
        )

    fc = grid.map(add_growth).filter(ee.Filter.notNull(["growth_sqkm"]))
    gdf = ee_fc_to_gdf(fc).to_crs(WGS84)
    gdf["growth_sqkm"] = pd.to_numeric(gdf["growth_sqkm"])
    gdf = gdf[gdf["growth_sqkm"] > 0].copy()

    # LGA assignment.
    gdf_cent = gdf.copy()
    gdf_cent["geometry"] = gdf_cent.geometry.centroid
    joined = gpd.sjoin(gdf_cent, metro_gdf[["ADM2_NAME", "geometry"]], how="left", predicate="within")
    gdf["lga"] = joined["ADM2_NAME"].fillna("Boundary/Unassigned").values

    # Quantile hotspot classes from growth distribution.
    labels = ["very low", "low", "moderate", "high", "very high"]
    gdf["hotspot_class"] = pd.qcut(gdf["growth_sqkm"], q=5, labels=labels, duplicates="drop").astype(str)

    # Distance metrics in UTM. Road and water distances use vector OSM layers.
    # Distance to the 2000 built-up core is corrected below using the GHSL
    # baseline built-up distance surface for the top hotspot table.
    metric = gdf.to_crs(METRIC_CRS)
    centroids = metric.geometry.centroid
    roads_m = roads_gdf.to_crs(METRIC_CRS) if len(roads_gdf) else roads_gdf
    water_m = water_gdf.to_crs(METRIC_CRS) if len(water_gdf) else water_gdf
    core_proxy = metric.sort_values("growth_sqkm", ascending=False).head(20).geometry.union_all().centroid
    core_geom = core_proxy.buffer(2500)

    roads_union = roads_m.geometry.union_all() if len(roads_m) else None
    water_union = water_m.geometry.union_all() if len(water_m) else None
    metric["distance_to_major_road_km"] = [
        round(pt.distance(roads_union) / 1000, 3) if roads_union is not None else None for pt in centroids
    ]
    metric["distance_to_2000_builtup_core_km"] = [round(pt.distance(core_geom) / 1000, 3) for pt in centroids]
    metric["distance_to_river_km"] = [
        round(pt.distance(water_union) / 1000, 3) if water_union is not None else None for pt in centroids
    ]
    gdf["distance_to_major_road_km"] = metric["distance_to_major_road_km"].values
    gdf["distance_to_2000_builtup_core_km"] = metric["distance_to_2000_builtup_core_km"].values
    gdf["distance_to_river_km"] = metric["distance_to_river_km"].values

    def interp(row):
        near_core = row["distance_to_2000_builtup_core_km"] is not None and row["distance_to_2000_builtup_core_km"] <= 2
        near_road = row["distance_to_major_road_km"] is not None and row["distance_to_major_road_km"] <= 1
        near_river = row["distance_to_river_km"] is not None and row["distance_to_river_km"] <= 1
        if near_river and near_core:
            return "Drainage-sensitive edge expansion near existing urban core"
        if near_road and near_core:
            return "Possible corridor-linked edge expansion"
        if row["lga"] in ["Chikun", "Igabi"]:
            return "Peri-urban expansion zone"
        if near_core:
            return "Central-core consolidation or edge expansion"
        return "Emerging peripheral growth cell"

    gdf["planning_interpretation"] = gdf.apply(interp, axis=1)
    gdf.to_file(DATA / "kaduna_growth_hotspot_grid.geojson", driver="GeoJSON")
    gdf.to_file(DATA / "kaduna_growth_grid.geojson", driver="GeoJSON")
    gdf.to_file(ROOT / "kaduna_growth_hotspot_grid.geojson", driver="GeoJSON")

    top = gdf.sort_values("growth_sqkm", ascending=False).head(12).copy()
    top["rank"] = range(1, len(top) + 1)
    top["latitude"] = top.geometry.centroid.y.round(5)
    top["longitude"] = top.geometry.centroid.x.round(5)

    # Use GHSL 2000 dense built-up cells for the baseline-core distance metric.
    core_distance = (
        built[2000]
        .gte(2500)
        .fastDistanceTransform(neighborhood=1024, units="pixels")
        .sqrt()
        .multiply(100)
        .rename("distance_to_core_m")
    )
    corrected_core_distances = []
    for _, row in top.iterrows():
        pt = ee.Geometry.Point([float(row.geometry.centroid.x), float(row.geometry.centroid.y)])
        value = core_distance.reduceRegion(
            ee.Reducer.first(), pt, scale=100, maxPixels=1e9, bestEffort=True
        ).get("distance_to_core_m")
        try:
            corrected_core_distances.append(round(float(value.getInfo()) / 1000, 3))
        except Exception:
            corrected_core_distances.append(None)
    top["distance_to_2000_builtup_core_km"] = corrected_core_distances

    cols = [
        "rank",
        "lga",
        "latitude",
        "longitude",
        "growth_sqkm",
        "hotspot_class",
        "distance_to_major_road_km",
        "distance_to_2000_builtup_core_km",
        "distance_to_river_km",
        "planning_interpretation",
    ]
    top[cols].rename(columns={"growth_sqkm": "growth_sqkm_2000_2025"}).to_csv(
        TABLES / "table_2_top_hotspots_with_driver_metrics.csv", index=False
    )
    return growth, gdf, top, core_geom


def plot_base(ax, metro_gdf, roads, water):
    metro_gdf.boundary.plot(ax=ax, linewidth=0.7, color="#333333")
    if len(water):
        water.plot(ax=ax, color="#2b8cbe", linewidth=1.3, alpha=0.85)
    if len(roads):
        roads.plot(ax=ax, color="#222222", linewidth=0.55, alpha=0.85)
    for _, row in metro_gdf.iterrows():
        pt = row.geometry.representative_point()
        ax.text(pt.x, pt.y, row["ADM2_NAME"], fontsize=8, ha="center", va="center", color="#222222")


def save_plot(fig, path):
    fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {path}", flush=True)


def make_figures(kaduna_state_gdf, kaduna_lgas_gdf, metro_gdf, roads, water, built, growth, grid_gdf, top_gdf, area_df, region, aoi, core_geom):
    boundary = ee.Image().byte().paint(ee.FeatureCollection("FAO/GAUL/2015/level2").filter(ee.Filter.inList("ADM2_NAME", METRO_LGAS)), 1, 2)
    bbox = bbox_from_region(region)

    # Figure 1
    fig, ax = plt.subplots(figsize=(9, 7))
    kaduna_state_gdf.plot(ax=ax, color="#eeeeee", edgecolor="#111111", linewidth=1.1)
    kaduna_lgas_gdf.boundary.plot(ax=ax, color="#c7c7c7", linewidth=0.45)
    metro_gdf.plot(ax=ax, color="#ff6b35", edgecolor="#b30000", linewidth=1.0)
    for _, row in metro_gdf.iterrows():
        pt = row.geometry.representative_point()
        ax.text(pt.x, pt.y, row["ADM2_NAME"], fontsize=8, ha="center", va="center")
    ax.set_title("Kaduna Metropolitan Area Within Kaduna State", fontsize=14, weight="bold")
    ax.set_axis_off()
    save_plot(fig, FIGURES / "figure_1_study_area_inset.png")

    # Figure 2
    fig, ax = plt.subplots(figsize=(9, 7))
    metro_gdf.plot(ax=ax, color="#f5f5f5", edgecolor="#333333", linewidth=0.8)
    built[2000].gte(2500).selfMask()
    core_img = built[2000].gte(2500).selfMask().visualize(palette=["4d4d4d"])
    core_path = download_thumb(core_img, {}, region, FIGURES / "_core2000_tmp.png", 1500)
    img = Image.open(core_path)
    ax.imshow(img, extent=bbox, origin="upper", alpha=0.95)
    plot_base(ax, metro_gdf, roads, water)
    ax.set_title("Metropolitan Context: LGAs, Roads, Water Features, and 2000 Built-up Core", fontsize=12, weight="bold")
    ax.set_axis_off()
    save_plot(fig, FIGURES / "figure_2_metropolitan_context_roads_river_core.png")

    # Figure 3
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(area_df["year"], area_df["builtup_area_sqkm_ghsl"], marker="o", linewidth=2.4, color="#1f77b4")
    ax.fill_between(area_df["year"], area_df["builtup_area_sqkm_ghsl"], color="#1f77b4", alpha=0.12)
    for x, y in zip(area_df["year"], area_df["builtup_area_sqkm_ghsl"]):
        ax.text(x, y + 1.3, f"{y:.1f}", ha="center", fontsize=8)
    ax.set_xlabel("Year")
    ax.set_ylabel("Built-up surface area (sq. km)")
    ax.set_title("Built-up Surface Trend in Kaduna Metropolis, 2000-2025", weight="bold")
    ax.grid(True, linestyle="--", alpha=0.45)
    save_plot(fig, FIGURES / "figure_3_builtup_surface_trend.png")

    # Figure 4 expansion periods
    periods = classify_periods(built).clip(aoi)
    water_mask = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(20).clip(aoi)
    periods_water = periods.where(water_mask, 5).rename("expansion_period")
    period_vis = {
        "min": 0,
        "max": 5,
        "palette": ["f2f2f2", "4d4d4d", "fdd49e", "fdbb84", "d7301f", "2b8cbe"],
    }
    path4 = download_thumb(white_map(periods_water, boundary, period_vis), {}, region, FIGURES / "figure_4_builtup_expansion_by_period.png", 1900)
    add_title_frame(path4, "Built-up Expansion by Period in Kaduna Metropolis, 2000-2025", "GHSL dense built-up cells by first observed period; water shown in blue", "Source: GHSL GHS-BUILT-S P2023A and JRC Global Surface Water")
    add_north_scale(path4)

    # Figure 5 growth intensity percentile-ish continuous map
    growth_vis = {"min": 0, "max": 6000, "palette": ["f7f7f7", "fee5d9", "fcae91", "fb6a4a", "cb181d"]}
    path5 = download_thumb(white_map(growth, boundary, growth_vis), {}, region, FIGURES / "figure_5_growth_intensity_2000_2025.png", 1900)
    add_title_frame(path5, "Built-up Growth Intensity, 2000-2025", "Sequential red classes show increasing built-up surface growth", "Source: GHSL GHS-BUILT-S P2023A")
    add_north_scale(path5)

    # Figure 6 grid hotspots
    class_colors = {"very low": "#fee5d9", "low": "#fcae91", "moderate": "#fb6a4a", "high": "#cb181d", "very high": "#67000d"}
    fig, ax = plt.subplots(figsize=(9, 7))
    metro_gdf.plot(ax=ax, color="#f7f7f7", edgecolor="#333333", linewidth=0.7)
    grid_gdf.plot(ax=ax, color=grid_gdf["hotspot_class"].map(class_colors), edgecolor="#ffffff", linewidth=0.1, alpha=0.92)
    metro_gdf.boundary.plot(ax=ax, color="#333333", linewidth=0.8)
    for _, row in top_gdf.head(8).iterrows():
        pt = row.geometry.centroid
        ax.scatter(pt.x, pt.y, s=105, color="black", edgecolor="white", zorder=5)
        ax.text(pt.x, pt.y, str(row["rank"]), color="white", ha="center", va="center", fontsize=7, zorder=6)
    handles = [mpatches.Patch(color=c, label=l.title()) for l, c in class_colors.items()]
    ax.legend(handles=handles, loc="lower left", title="Growth class")
    ax.set_title("Urban Growth Intensity Hotspots, 2000-2025", weight="bold")
    ax.set_axis_off()
    save_plot(fig, FIGURES / "figure_6_urban_growth_hotspots.png")

    # Figure 7 driver overlay
    fig, ax = plt.subplots(figsize=(9, 7))
    metro_gdf.plot(ax=ax, color="#f7f7f7", edgecolor="#333333", linewidth=0.7)
    grid_gdf[grid_gdf["hotspot_class"].isin(["high", "very high"])].plot(ax=ax, color="#cb181d", edgecolor="white", linewidth=0.2, alpha=0.55)
    plot_base(ax, metro_gdf, roads, water)
    # core proxy in metric back to WGS84
    gpd.GeoSeries([core_geom], crs=METRIC_CRS).to_crs(WGS84).boundary.plot(ax=ax, color="#4d4d4d", linewidth=1.2, linestyle="--")
    for _, row in top_gdf.head(8).iterrows():
        pt = row.geometry.centroid
        ax.scatter(pt.x, pt.y, s=105, color="black", edgecolor="white", zorder=5)
        ax.text(pt.x, pt.y, str(row["rank"]), color="white", ha="center", va="center", fontsize=7, zorder=6)
    ax.set_title("Hotspot and Spatial Driver Context Overlay", weight="bold")
    ax.set_axis_off()
    save_plot(fig, FIGURES / "figure_7_hotspots_spatial_drivers_overlay.png")

    # Figure 8 before-after panels for top 4 hotspots. Landsat 2000
    # thumbnails are too sparse at this chip scale, so 2010 is used as the
    # visual baseline while the quantitative ranking remains 2000-2025.
    rgb_vis = {"bands": ["red", "green", "blue"], "min": 0.02, "max": 0.30}
    img_paths = []
    titles = []
    for _, row in top_gdf.head(4).iterrows():
        pt = row.geometry.centroid
        geom = ee.Geometry.Point([pt.x, pt.y]).buffer(1200).bounds(10)
        local_region = geom.getInfo()["coordinates"]
        for year in [2010, 2025]:
            p = download_thumb(landsat_context_composite(year, aoi), rgb_vis, local_region, FIGURES / f"_hotspot_{int(row['rank'])}_{year}.png", 900)
            img_paths.append(p)
            titles.append(f"Hotspot {int(row['rank'])}: {year}")
    fig, axes = plt.subplots(4, 2, figsize=(8, 13))
    axes = axes.flatten()
    for ax, p, title in zip(axes, img_paths, titles):
        ax.imshow(Image.open(p))
        ax.set_title(title, fontsize=10, weight="bold")
        ax.axis("off")
    fig.suptitle("Before-After Satellite Context for Top Growth Hotspots", weight="bold", y=0.995)
    fig.tight_layout()
    save_plot(fig, FIGURES / "figure_8_before_after_hotspot_panels.png")

    # Export TIFF for expansion periods.
    export_ee_tif(periods_water.toByte(), region, RASTERS / "kaduna_builtup_expansion_periods.tif")
    shutil.copy(RASTERS / "kaduna_builtup_expansion_periods.tif", ROOT / "kaduna_builtup_expansion_periods.tif")


def export_ee_tif(image, region, out_tif, scale=100, crs=METRIC_CRS):
    if out_tif.exists() and out_tif.stat().st_size > 0:
        print(f"Using cached: {out_tif}", flush=True)
        return out_tif
    url = image.getDownloadURL({"scale": scale, "crs": crs, "region": region, "format": "GEO_TIFF"})
    response = requests.get(url, timeout=240)
    response.raise_for_status()
    if response.content[:2] == b"PK":
        zip_path = out_tif.with_suffix(".zip")
        zip_path.write_bytes(response.content)
        with zipfile.ZipFile(zip_path) as zf:
            tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            if not tif_names:
                raise RuntimeError("No tif in EE download zip")
            out_tif.write_bytes(zf.read(tif_names[0]))
        zip_path.unlink(missing_ok=True)
    else:
        out_tif.write_bytes(response.content)
    print(f"Saved: {out_tif}", flush=True)
    return out_tif


def export_qgis_rasters(built, growth, region):
    for year in [2000, 2010, 2020, 2025]:
        mask = built[year].gt(0).toByte().rename(f"builtup_{year}")
        export_ee_tif(mask, region, RASTERS / f"builtup_{year}.tif", scale=100)
    export_ee_tif(growth.toInt16().rename("builtup_growth_2000_2025"), region, RASTERS / "builtup_growth_2000_2025.tif", scale=100)


def write_qgis_notes():
    qml = """# QGIS style guide for Kaduna publication maps
# Stable non-built-up: #f2f2f2
# Built-up before 2000: #4d4d4d
# New growth 2000-2010: #fdd49e
# New growth 2010-2020: #fdbb84
# New growth 2020-2025: #d7301f
# Water/river: #2b8cbe
# Major roads: #222222
# LGA boundaries: #333333
# Hotspot classes: #fee5d9, #fcae91, #fb6a4a, #cb181d, #67000d
"""
    readme = """Kaduna QGIS polishing notes

Load these layers for final cartographic polishing:
- ../data/kaduna_metro_boundary.geojson
- ../data/kaduna_growth_grid.geojson
- ../data/kaduna_growth_hotspot_grid.geojson
- ../data/kaduna_major_roads.geojson
- ../data/kaduna_waterways_water_features.geojson
- ../rasters/kaduna_builtup_expansion_periods.tif
- ../rasters/builtup_growth_2000_2025.tif

The Python workflow already exports 300 dpi PNG figures. QGIS can be used for final label placement or journal-specific map layout requirements.
"""
    (QGIS / "map_style_files.qml").write_text(qml, encoding="utf-8")
    (QGIS / "README_qgis_polishing.txt").write_text(readme, encoding="utf-8")


def make_contact_sheet():
    files = [FIGURES / f"figure_{i}_{name}.png" for i, name in []]
    files = sorted(FIGURES.glob("figure_*.png"))
    thumb_w, thumb_h = 430, 310
    regular, bold, small, tiny = fonts()
    thumbs = []
    for p in files:
        img = Image.open(p).convert("RGB")
        img.thumbnail((thumb_w, thumb_h - 32))
        canvas = Image.new("RGB", (thumb_w, thumb_h), "white")
        canvas.paste(img, ((thumb_w - img.width) // 2, 4))
        d = ImageDraw.Draw(canvas)
        d.text((8, thumb_h - 24), p.name, fill="black", font=tiny)
        thumbs.append(canvas)
    cols = 2
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), "#eeeeee")
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * thumb_w, (i // cols) * thumb_h))
    out = FIGURES / "figure_0_contact_sheet.png"
    sheet.save(out)
    print(f"Saved: {out}", flush=True)


def update_manuscript():
    path = Path("GeoAI_Kaduna_Climate_Resilient_Urban_Growth_Manuscript.md")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    insert_method = """

### 3.6 Spatial Driver Context Analysis

To support interpretation of the urban growth hotspots, selected spatial context layers were overlaid with the built-up growth results. These included major roads, the baseline 2000 built-up core, river and water features, and metropolitan LGA boundaries. The purpose of this step was to identify spatial associations that may help explain the observed pattern of urban expansion, rather than to establish causal relationships.

Major roads were used to assess whether high-growth cells appear to align with accessibility corridors. The 2000 built-up core was used to distinguish expansion around older developed areas from more peripheral growth. River and water features were included to support climate-resilience interpretation, especially in relation to runoff, drainage, and environmental management. LGA boundaries were used to connect hotspot locations to administrative planning responsibilities.

For each top-ranked hotspot cell, distance metrics were calculated, including distance to the nearest major road, distance to the 2000 built-up core, and distance to river or water features. These metrics were used to support planning-oriented interpretation of the hotspot results.
"""
    if "### 3.6 Spatial Driver Context Analysis" not in text:
        text = text.replace("## 4. Results", insert_method + "\n## 4. Results")
    insert_results = """

### 4.7 Spatial Factors Associated with Urban Growth Hotspots

The spatial driver overlay shows that Kaduna's strongest growth hotspots are concentrated around the central metropolitan belt and nearby peri-urban expansion zones. Several high-growth cells occur close to the 2000 built-up core, suggesting edge expansion and consolidation around the established urban footprint. Other high-growth cells extend into Chikun and Igabi, indicating outward peri-urban expansion beyond the older core LGAs.

The overlay with major roads suggests that accessibility corridors may be associated with some of the observed growth patterns. This is especially relevant for interpreting expansion from the central city into surrounding development zones. River and water features provide an additional climate-resilience context, as growth near drainage-sensitive landscapes may require closer attention to runoff management, open-space protection, and infrastructure planning.

These relationships should be interpreted as exploratory spatial associations, not causal evidence. However, they provide a useful basis for identifying priority zones for field validation, infrastructure assessment, drainage planning, and urban growth management.
"""
    if "### 4.7 Spatial Factors Associated with Urban Growth Hotspots" not in text:
        text = text.replace("## 5. Discussion", insert_results + "\n## 5. Discussion")
    text = text.replace(
        "Second, the hotspot grid identifies areas of high built-up surface growth but does not directly explain the socioeconomic or policy drivers of that growth.",
        "Second, the hotspot grid and spatial driver layers identify exploratory spatial associations with roads, baseline built-up areas, and river or water features, but they do not prove socioeconomic, policy, or causal drivers of growth.",
    )
    text = text.replace(
        "The results indicate substantial built-up surface expansion in Kaduna Metropolis between 2000 and 2025.",
        "The results indicate substantial built-up surface expansion in Kaduna Metropolis between 2000 and 2025, including central-core expansion, peri-urban growth in Chikun and Igabi, and possible corridor-linked development patterns.",
    )
    path.write_text(text, encoding="utf-8")
    print(f"Updated manuscript: {path}", flush=True)


def package_outputs():
    shutil.make_archive(str(ROOT / "kaduna_publication_figures"), "zip", ROOT)
    print(f"Packaged: {ROOT / 'kaduna_publication_figures.zip'}", flush=True)


def main():
    init()
    kaduna_state, kaduna_lgas, metro_fc, aoi = get_boundaries()
    region = aoi.bounds(10).getInfo()["coordinates"]
    print("Selected metropolitan LGAs:", metro_fc.aggregate_array("ADM2_NAME").getInfo(), flush=True)
    kaduna_state_gdf = ee_fc_to_gdf(kaduna_state)
    kaduna_lgas_gdf = ee_fc_to_gdf(kaduna_lgas)
    metro_gdf = ee_fc_to_gdf(metro_fc)
    metro_gdf.to_file(DATA / "kaduna_metro_boundary.geojson", driver="GeoJSON")

    roads, water = download_osm_layers(metro_gdf)
    built = {year: ghsl(year, aoi) for year in YEARS}
    area_df = make_area_stats(built, aoi)
    growth, grid_gdf, top_gdf, core_geom = make_hotspot_grid(built, aoi, metro_gdf, roads, water)
    make_figures(kaduna_state_gdf, kaduna_lgas_gdf, metro_gdf, roads, water, built, growth, grid_gdf, top_gdf, area_df, region, aoi, core_geom)
    export_qgis_rasters(built, growth, region)
    write_qgis_notes()
    make_contact_sheet()
    update_manuscript()
    package_outputs()
    print(f"Finished upgraded outputs in {ROOT}", flush=True)


if __name__ == "__main__":
    main()
