from __future__ import annotations

import hashlib
import math
import re
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

matplotlib.use("Agg")


PROJECT_DIR = Path(__file__).resolve().parent
SANDBOX_DIR = PROJECT_DIR.parent
REPO_ROOT = SANDBOX_DIR.parent

SOURCE_PATH = PROJECT_DIR / "source.md"
RESULT_DIR = PROJECT_DIR / "result"
RESULT_IMAGES_DIR = RESULT_DIR / "images"
PREVIEW_PDF_PATH = RESULT_DIR / "preview.pdf"

ASSETS_DIR = REPO_ROOT / "assets" / "jl_projection"
POST_PATH = REPO_ROOT / "_posts" / "jl_projection.md"
FONT_DIR = Path("/usr/share/fonts/Adwaita")

PLOT_PATTERN = re.compile(r"\{\{\s*plot:(?P<name>[a-z0-9_]+)\s*\}\}")
PLOT_TAG_PATTERN = re.compile(r"<plot(?P<attrs>[^>]*)\s*/>")
FORMULA_TAG_PATTERN = re.compile(r"<formula(?P<attrs>[^>]*)>(?P<body>.*?)</formula>", re.DOTALL)
TAG_ATTR_PATTERN = re.compile(r'(?P<name>[a-z0-9_]+)="(?P<value>.*?)"')
IMAGE_PATTERN = re.compile(r"!\[(?P<alt>.*?)\]\((?P<src>.*?)\)")
FORMULA_IMAGE_FILENAMES: set[str] = set()


def random_projection(points: np.ndarray, target_dim: int, rng: np.random.Generator) -> np.ndarray:
    matrix = rng.normal(size=(points.shape[1], target_dim)) / math.sqrt(target_dim)
    return points @ matrix


def orthographic_projection_xy(points: np.ndarray) -> np.ndarray:
    return points[:, :2]


def rotate_x(points: np.ndarray, degrees: float) -> np.ndarray:
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    matrix = np.array(
        [
            [1, 0, 0],
            [0, c, -s],
            [0, s, c],
        ],
        dtype=float,
    )
    return points @ matrix.T


def rotate_z(points: np.ndarray, degrees: float) -> np.ndarray:
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    matrix = np.array(
        [
            [c, -s, 0],
            [s, c, 0],
            [0, 0, 1],
        ],
        dtype=float,
    )
    return points @ matrix.T


def rotate_y(points: np.ndarray, degrees: float) -> np.ndarray:
    radians = np.deg2rad(degrees)
    c = np.cos(radians)
    s = np.sin(radians)
    matrix = np.array(
        [
            [c, 0, s],
            [0, 1, 0],
            [-s, 0, c],
        ],
        dtype=float,
    )
    return points @ matrix.T


def nice_cube_orientation(points: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int]]:
    x_deg = 28
    y_deg = -22
    z_deg = 18
    rotated = rotate_z(rotate_y(rotate_x(points, x_deg), y_deg), z_deg)
    return rotated, (x_deg, y_deg, z_deg)


def pairwise_distances(points: np.ndarray) -> np.ndarray:
    diffs = points[:, None, :] - points[None, :, :]
    distances = np.linalg.norm(diffs, axis=-1)
    upper = np.triu_indices(len(points), k=1)
    return distances[upper]


def relative_distortion(original: np.ndarray, projected: np.ndarray) -> np.ndarray:
    baseline = np.where(original == 0, 1e-12, original)
    return np.abs(projected - original) / baseline


def mean_relative_distortion(original: np.ndarray, projected: np.ndarray) -> float:
    return float(np.mean(relative_distortion(original, projected)))


def point_labels() -> list[str]:
    return list("ABCDEFGH")


def cube_vertices() -> np.ndarray:
    return np.array(
        [
            [-1, -1, -1],
            [-1, -1, 1],
            [-1, 1, -1],
            [-1, 1, 1],
            [1, -1, -1],
            [1, -1, 1],
            [1, 1, -1],
            [1, 1, 1],
        ],
        dtype=float,
    )


def cube_edges() -> list[tuple[int, int]]:
    edges = []
    vertices = cube_vertices()
    for i in range(8):
        for j in range(i + 1, 8):
            if np.sum(np.abs(vertices[i] - vertices[j])) == 2:
                edges.append((i, j))
    return edges


def cube_pair_records() -> list[dict[str, float | str]]:
    points, _ = nice_cube_orientation(cube_vertices())
    projected = orthographic_projection_xy(points)
    labels = point_labels()
    records: list[dict[str, float | str]] = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            original_distance = float(np.linalg.norm(points[i] - points[j]))
            projected_distance = float(np.linalg.norm(projected[i] - projected[j]))
            distortion = abs(projected_distance - original_distance) / original_distance
            records.append(
                {
                    "pair": f"{labels[i]}{labels[j]}",
                    "pair_projected": f"{labels[i]}'{labels[j]}'",
                    "original_distance": original_distance,
                    "projected_distance": projected_distance,
                    "distortion": distortion,
                }
            )
    return records


def scaled_random_points(
    rng: np.random.Generator,
    source_dim: int,
    n_points: int,
    target_median_distance: float,
) -> np.ndarray:
    points = rng.normal(size=(n_points, source_dim))
    distances = pairwise_distances(points)
    current_median = float(np.median(distances))
    scale = target_median_distance / current_median if current_median > 0 else 1.0
    return points * scale


def plot_distance_scatter(
    output_path: Path,
    original_distances: np.ndarray,
    projected_distances: np.ndarray,
    *,
    title: str,
    subtitle: str,
) -> str:
    distortion = relative_distortion(original_distances, projected_distances)
    mean_distortion = float(np.mean(distortion))

    lo = float(min(original_distances.min(), projected_distances.min()))
    hi = float(max(original_distances.max(), projected_distances.max()))
    padding = (hi - lo) * 0.08 if hi > lo else 0.2

    fig, ax = plt.subplots(figsize=(5.8, 5.0))
    ax.scatter(
        original_distances,
        projected_distances,
        s=54,
        color="#d62828",
        alpha=0.82,
        edgecolors="white",
        linewidths=0.6,
    )
    ax.plot([lo - padding, hi + padding], [lo - padding, hi + padding], color="#1f2933", linewidth=1.5, alpha=0.9)
    ax.set_xlim(lo - padding, hi + padding)
    ax.set_ylim(lo - padding, hi + padding)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.26)
    ax.set_xlabel("Расстояние до проекции")
    ax.set_ylabel("После проекции")
    ax.set_title(title, fontsize=12)
    ax.text(
        0.03,
        0.97,
        subtitle + f"\nСреднее относительное искажение: {mean_distortion:.1%}",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9.5,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#dddddd", "alpha": 0.95},
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    return (
        f"{title}: точки показывают попарные расстояния до и после проекции, "
        f"подпись внутри графика показывает среднее относительное искажение ({mean_distortion:.1%})"
    )


def scatter_panel(
    ax: plt.Axes,
    original_distances: np.ndarray,
    projected_distances: np.ndarray,
    *,
    title: str,
    subtitle: str,
) -> float:
    distortion = relative_distortion(original_distances, projected_distances)
    mean_distortion = float(np.mean(distortion))

    lo = float(min(original_distances.min(), projected_distances.min()))
    hi = float(max(original_distances.max(), projected_distances.max()))
    padding = (hi - lo) * 0.08 if hi > lo else 0.2

    ax.scatter(
        original_distances,
        projected_distances,
        s=34,
        color="#d62828",
        alpha=0.82,
        edgecolors="white",
        linewidths=0.5,
    )
    ax.plot([lo - padding, hi + padding], [lo - padding, hi + padding], color="#1f2933", linewidth=1.3, alpha=0.9)
    ax.set_xlim(lo - padding, hi + padding)
    ax.set_ylim(lo - padding, hi + padding)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.22)
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("До", fontsize=9)
    ax.set_ylabel("После", fontsize=9)
    ax.text(
        0.03,
        0.97,
        subtitle + f"\nИскажение: {mean_distortion:.1%}",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.5,
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "white", "edgecolor": "#dddddd", "alpha": 0.94},
    )
    return mean_distortion


def plot_formula_card(
    output_path: Path,
    *,
    title: str | None = None,
    formula_lines: list[str],
    footer: str | None = None,
    width: float = 8.4,
    height: float = 2.2,
) -> str:
    fig = plt.figure(figsize=(width, height))
    fig.patch.set_facecolor("#ffffff")

    longest_line = max(len(normalize_formula_whitespace(line)) for line in formula_lines)
    formula_font_size = 13.2
    if longest_line >= 42:
        formula_font_size = 12.6
    if longest_line >= 56:
        formula_font_size = 11.8

    if title:
        title_y = 0.84 if footer else 0.8
        fig.text(
            0.5,
            title_y,
            title,
            ha="center",
            va="center",
            fontsize=11.5,
            color="#1f2933",
            fontweight="bold",
        )

    formula_top = 0.58 if title else 0.68
    formula_bottom = 0.28 if footer else 0.18
    if len(formula_lines) == 1:
        ys = [(formula_top + formula_bottom) / 2]
    else:
        ys = np.linspace(formula_top, formula_bottom, len(formula_lines))

    for y, formula in zip(ys, formula_lines):
        fig.text(
            0.5,
            y,
            formula,
            ha="center",
            va="center",
            fontsize=formula_font_size,
            color="#111111",
        )

    if footer:
        fig.text(
            0.5,
            0.12,
            footer,
            ha="center",
            va="center",
            fontsize=9.6,
            color="#4b5563",
        )

    fig.tight_layout(pad=0.03)
    fig.savefig(
        output_path,
        dpi=180,
        bbox_inches="tight",
        pad_inches=0.03,
        facecolor=fig.get_facecolor(),
    )
    plt.close(fig)
    return title or "Формула"


def parse_tag_attributes(raw_attrs: str) -> dict[str, str]:
    return {match.group("name"): match.group("value") for match in TAG_ATTR_PATTERN.finditer(raw_attrs)}


def normalize_formula_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def replace_function_call(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"\b{re.escape(name)}\(")
    parts: list[str] = []
    cursor = 0

    while match := pattern.search(text, cursor):
        start = match.start()
        parts.append(text[cursor:start])
        arg_start = match.end()
        depth = 1
        i = arg_start
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            raise ValueError(f"Unbalanced parentheses in formula: {text}")
        inner = replace_function_call(text[arg_start : i - 1], name, replacement)
        parts.append(f"{replacement}{{{inner}}}")
        cursor = i

    parts.append(text[cursor:])
    return "".join(parts)


def top_level_fraction(text: str) -> str:
    slash_positions: list[int] = []
    depth = 0
    for i, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "/" and depth == 0:
            slash_positions.append(i)

    if len(slash_positions) != 1:
        return text

    slash = slash_positions[0]
    left = text[:slash].rstrip()
    right = text[slash + 1 :].lstrip()
    if not left or not right:
        return text

    relation_markers = [r"\leq", r"\geq", "="]
    relation_end = 0
    for marker in relation_markers:
        marker_pos = left.rfind(marker)
        if marker_pos != -1:
            relation_end = max(relation_end, marker_pos + len(marker))

    if relation_end:
        prefix = left[:relation_end].rstrip()
        numerator = left[relation_end:].strip()
        if prefix and numerator:
            return rf"{prefix} \frac{{{numerator}}}{{{right}}}"

    return rf"\frac{{{left}}}{{{right}}}"


def ascii_formula_to_mathtext(formula: str) -> str:
    normalized = normalize_formula_whitespace(formula)
    if not normalized:
        raise ValueError("Empty formula line")
    if normalized.startswith("$") and normalized.endswith("$"):
        return normalized

    rendered = normalized
    rendered = replace_function_call(rendered, "sqrt", r"\sqrt")
    rendered = re.sub(r"\beps\b", r"\\varepsilon", rendered)
    rendered = re.sub(r"\blog\(", r"\\log(", rendered)
    rendered = rendered.replace("<=", r"\leq ")
    rendered = rendered.replace(">=", r"\geq ")
    rendered = rendered.replace("!=", r"\neq ")
    rendered = rendered.replace(" * ", r" \cdot ")
    if "/" in rendered and not rendered.startswith(r"\frac{"):
        rendered = top_level_fraction(rendered)
    return f"${rendered}$"


def formula_card_width(formula_lines: list[str]) -> float:
    longest_line = max(len(normalize_formula_whitespace(line)) for line in formula_lines)
    return min(7.2, max(4.2, 3.1 + longest_line * 0.03))


def formula_card_height(formula_lines: list[str], *, title: str | None, footer: str | None) -> float:
    height = 0.78 + 0.34 * len(formula_lines)
    if title:
        height += 0.34
    if footer:
        height += 0.26
    return max(1.15, height)


def formula_alt_text(formula_lines: list[str], attrs: dict[str, str]) -> str:
    if "alt" in attrs:
        return attrs["alt"]
    if "title" in attrs:
        return attrs["title"]
    preview = normalize_formula_whitespace(" ; ".join(formula_lines))
    if len(preview) <= 100:
        return preview
    return preview[:97].rstrip() + "..."


def render_formula_block(attrs: dict[str, str], body: str) -> tuple[str, str]:
    raw_lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not raw_lines:
        raise ValueError("Formula block cannot be empty")

    title = attrs.get("title")
    footer = attrs.get("footer")
    rendered_lines = [ascii_formula_to_mathtext(line) for line in raw_lines]
    width = float(attrs["width"]) if "width" in attrs else formula_card_width(raw_lines)
    height = float(attrs["height"]) if "height" in attrs else formula_card_height(raw_lines, title=title, footer=footer)

    name = attrs.get("name")
    if name:
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"Invalid formula name: {name}")
        filename = f"{name}.png"
    else:
        digest_source = "\n".join([repr(sorted(attrs.items())), *raw_lines]).encode("utf-8")
        filename = f"formula_{hashlib.sha1(digest_source).hexdigest()[:12]}.png"

    FORMULA_IMAGE_FILENAMES.add(filename)
    image_path = RESULT_IMAGES_DIR / filename
    plot_formula_card(
        image_path,
        title=title,
        formula_lines=rendered_lines,
        footer=footer,
        width=width,
        height=height,
    )
    shutil.copy2(image_path, ASSETS_DIR / filename)
    return filename, formula_alt_text(raw_lines, attrs)


def plot_projection_3d_to_2d(output_path: Path) -> str:
    points, angles = nice_cube_orientation(cube_vertices())
    points[:, 2] += 3.35
    projected = orthographic_projection_xy(points)
    edges = cube_edges()
    labels = point_labels()
    plane_z = 0.0

    fig = plt.figure(figsize=(11.2, 5.1))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.55, 0.95])
    ax3d = fig.add_subplot(gs[0, 0], projection="3d")
    ax2d = fig.add_subplot(gs[0, 1])

    plane_x = np.array([[-1.55, 1.55], [-1.55, 1.55]])
    plane_y = np.array([[-1.55, -1.55], [1.55, 1.55]])
    plane_z_grid = np.full_like(plane_x, plane_z)
    ax3d.plot_surface(
        plane_x,
        plane_y,
        plane_z_grid,
        alpha=0.12,
        color="#f4a261",
        edgecolor="none",
    )
    for grid_value in (-1.0, 0.0, 1.0):
        ax3d.plot([-1.55, 1.55], [grid_value, grid_value], [plane_z, plane_z], color="#666666", linewidth=0.7, alpha=0.22)
        ax3d.plot([grid_value, grid_value], [-1.55, 1.55], [plane_z, plane_z], color="#666666", linewidth=0.7, alpha=0.22)

    axis_span = 2.1
    axis_color = "#222222"
    ax3d.quiver(0, 0, 0, axis_span, 0, 0, color=axis_color, linewidth=1.0, alpha=0.78, arrow_length_ratio=0.08)
    ax3d.quiver(0, 0, 0, 0, axis_span, 0, color=axis_color, linewidth=1.0, alpha=0.78, arrow_length_ratio=0.08)
    ax3d.quiver(0, 0, 0, 0, 0, 2.85, color=axis_color, linewidth=1.0, alpha=0.78, arrow_length_ratio=0.08)
    ax3d.text(axis_span + 0.08, 0, 0, "x", fontsize=10)
    ax3d.text(0, axis_span + 0.08, 0, "y", fontsize=10)
    ax3d.text(0, 0, 2.78, "z", fontsize=10)

    for start, end in edges:
        ax3d.plot(
            points[[start, end], 0],
            points[[start, end], 1],
            points[[start, end], 2],
            color="#7aa6d8",
            linewidth=1.8,
        )
        ax2d.plot(
            projected[[start, end], 0],
            projected[[start, end], 1],
            color="#f2a1a1",
            linewidth=1.8,
        )

    for point in points:
        ax3d.plot(
            [point[0], point[0]],
            [point[1], point[1]],
            [point[2], plane_z],
            linestyle="--",
            color="#999999",
            linewidth=1.0,
            alpha=0.55,
        )

    projected_on_plane = np.column_stack([projected, np.full(len(projected), plane_z)])

    ax3d.scatter(points[:, 0], points[:, 1], points[:, 2], c="#1f77b4", s=44, depthshade=False)
    ax3d.scatter([0], [0], [0], c="black", s=36, depthshade=False)

    for start, end in edges:
        ax3d.plot(
            projected_on_plane[[start, end], 0],
            projected_on_plane[[start, end], 1],
            projected_on_plane[[start, end], 2],
            color="#d62728",
            linewidth=2.2,
            alpha=0.9,
        )

    ax3d.scatter(projected_on_plane[:, 0], projected_on_plane[:, 1], projected_on_plane[:, 2], c="#d62728", s=26, depthshade=False)
    for label, point in zip(labels, points):
        ax3d.text(point[0] + 0.07, point[1] + 0.05, point[2] + 0.04, label, fontsize=9, color="#0b3c68")
    for label, point in zip(labels, projected_on_plane):
        ax3d.text(point[0] + 0.06, point[1] - 0.08, point[2], f"{label}'", fontsize=9, color="#8c1d18")
    ax3d.set_title("Ортогональная проекция на нижнюю плоскость")
    xyz_points = np.vstack([points, projected_on_plane, [[0.0, 0.0, 0.0], [axis_span, 0.0, 0.0], [0.0, axis_span, 0.0]]])
    mins = xyz_points.min(axis=0)
    maxs = xyz_points.max(axis=0)
    center3d = (mins + maxs) / 2
    half_ranges = (maxs - mins) / 2
    radius3d = float(np.max(half_ranges) * 0.76)
    ax3d.set_xlim(center3d[0] - radius3d, center3d[0] + radius3d)
    ax3d.set_ylim(center3d[1] - radius3d, center3d[1] + radius3d)
    ax3d.set_zlim(center3d[2] - radius3d, center3d[2] + radius3d)
    ax3d.set_box_aspect((1, 1, 1))
    ax3d.set_proj_type("ortho")
    ax3d.view_init(elev=24, azim=-58)
    ax3d.set_axis_off()

    ax2d.scatter(projected[:, 0], projected[:, 1], c="#d62728", s=38)
    ax2d.plot([-0.08, 0.08], [0, 0], color="black", linewidth=2.0, zorder=5)
    ax2d.plot([0, 0], [-0.08, 0.08], color="black", linewidth=2.0, zorder=5)
    for start, end in edges:
        ax2d.plot(
            projected[[start, end], 0],
            projected[[start, end], 1],
            color="#f2a1a1",
            linewidth=1.8,
        )
    for label, point in zip(labels, projected):
        ax2d.annotate(
            f"{label}'",
            (point[0], point[1]),
            xytext=(6, 5),
            textcoords="offset points",
            fontsize=10,
            color="#8c1d18",
            weight="bold",
        )
    ax2d.set_title("Та же проекция как плоская картинка")
    ax2d.set_xlabel("x")
    ax2d.set_ylabel("y")
    center = projected.mean(axis=0)
    radius = np.max(np.abs(projected - center)) + 0.18
    ax2d.set_xlim(center[0] - radius, center[0] + radius)
    ax2d.set_ylim(center[1] - radius, center[1] + radius)
    ax2d.set_aspect("equal", adjustable="box")
    ax2d.grid(alpha=0.25)

    fig.suptitle("Начнём с куба, который ещё похож на куб", fontsize=13)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)

    return (
        "Куб в удобном ракурсе "
        f"(поворот по x: {angles[0]}°, по y: {angles[1]}°, по z: {angles[2]}°) "
        "с вершинами A-H и их проекциями A'-H'"
    )


def plot_cube_distance_shift(output_path: Path) -> str:
    points, _ = nice_cube_orientation(cube_vertices())
    projected = orthographic_projection_xy(points)
    labels = point_labels()
    n = len(labels)

    d3 = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    d2 = np.linalg.norm(projected[:, None, :] - projected[None, :, :], axis=-1)
    baseline = np.where(d3 == 0, 1.0, d3)
    distortion = np.abs(d2 - d3) / baseline

    cmap = LinearSegmentedColormap.from_list(
        "distortion_map",
        ["#ffffff", "#f6c7bc", "#d62828"],
    )
    vmax = float(max(np.max(distortion), 1e-6))

    display_distortion = distortion[:-1, 1:]
    row_labels = labels[:-1]
    col_labels = labels[1:]
    row_indices = np.arange(n - 1)
    col_indices = np.arange(1, n)
    mask = col_indices[None, :] <= row_indices[:, None]
    masked_distortion = np.ma.array(display_distortion, mask=mask)
    cmap = cmap.copy()
    cmap.set_bad(color="white")

    fig, ax = plt.subplots(figsize=(9.2, 7.6))
    image = ax.imshow(masked_distortion, cmap=cmap, vmin=0.0, vmax=vmax)

    ax.set_xticks(np.arange(n - 1), labels=col_labels, fontsize=11)
    ax.set_yticks(np.arange(n - 1), labels=row_labels, fontsize=11)
    ax.set_xlabel("Вторая вершина")
    ax.set_ylabel("Первая вершина")
    ax.set_title("Расстояние до проекции минус расстояние после")

    ax.set_xticks(np.arange(-0.5, n - 1, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n - 1, 1), minor=True)
    ax.grid(which="minor", color="#d9d9d9", linestyle="-", linewidth=0.8)
    ax.tick_params(which="minor", bottom=False, left=False)

    for display_i, i in enumerate(range(n - 1)):
        for display_j, j in enumerate(range(1, n)):
            if j <= i:
                continue
            value_3d = d3[i, j]
            value_2d = d2[i, j]
            delta = abs(value_3d - value_2d)
            text_color = "white" if distortion[i, j] > vmax * 0.58 else "#1f2933"
            ax.text(
                display_j,
                display_i - 0.12,
                f"{value_3d:.1f} - {value_2d:.1f}",
                ha="center",
                va="center",
                color=text_color,
                fontsize=7.7,
            )
            ax.text(
                display_j,
                display_i + 0.12,
                f"~ {delta:.1f}",
                ha="center",
                va="center",
                color=text_color,
                fontsize=8.6,
            )

    colorbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
    colorbar.set_label("Относительное искажение расстояния")

    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    return (
        "Матрица расстояний между вершинами куба: в ячейках показано, как расстояние до проекции "
        "отличается от расстояния после, а цвет плавно меняется от зелёного при малом искажении к красному при большом"
    )


def plot_cube_mean_distortion(output_path: Path) -> str:
    points, _ = nice_cube_orientation(cube_vertices())
    projected = orthographic_projection_xy(points)
    return plot_distance_scatter(
        output_path,
        pairwise_distances(points),
        pairwise_distances(projected),
        title="Куб: 3D -> 2D",
        subtitle="Все те же 8 вершин куба",
    )


def plot_random_4d_mean_distortion(output_path: Path) -> str:
    rng = np.random.default_rng(7)
    cube_median = float(np.median(pairwise_distances(cube_vertices())))
    points = scaled_random_points(rng, source_dim=4, n_points=8, target_median_distance=cube_median)
    projected = random_projection(points, target_dim=3, rng=rng)
    return plot_distance_scatter(
        output_path,
        pairwise_distances(points),
        pairwise_distances(projected),
        title="Случайные точки: 4D -> 3D",
        subtitle="8 точек, масштаб подогнан под куб",
    )


def plot_random_5d_mean_distortion(output_path: Path) -> str:
    rng = np.random.default_rng(11)
    cube_median = float(np.median(pairwise_distances(cube_vertices())))
    points = scaled_random_points(rng, source_dim=5, n_points=8, target_median_distance=cube_median)
    projected = random_projection(points, target_dim=4, rng=rng)
    return plot_distance_scatter(
        output_path,
        pairwise_distances(points),
        pairwise_distances(projected),
        title="Случайные точки: 5D -> 4D",
        subtitle="Те же 8 точек, теряем одну координату",
    )


def plot_random_4d_5d_side_by_side(output_path: Path) -> str:
    cube_median = float(np.median(pairwise_distances(cube_vertices())))
    rng4 = np.random.default_rng(7)
    rng5 = np.random.default_rng(11)

    points4 = scaled_random_points(rng4, source_dim=4, n_points=8, target_median_distance=cube_median)
    projected4 = random_projection(points4, target_dim=3, rng=rng4)
    original4 = pairwise_distances(points4)
    reduced4 = pairwise_distances(projected4)

    points5 = scaled_random_points(rng5, source_dim=5, n_points=8, target_median_distance=cube_median)
    projected5 = random_projection(points5, target_dim=4, rng=rng5)
    original5 = pairwise_distances(points5)
    reduced5 = pairwise_distances(projected5)

    fig, axes = plt.subplots(1, 2, figsize=(9.1, 4.25))
    mean4 = scatter_panel(
        axes[0],
        original4,
        reduced4,
        title="4D -> 3D",
        subtitle="8 случайных точек",
    )
    mean5 = scatter_panel(
        axes[1],
        original5,
        reduced5,
        title="5D -> 4D",
        subtitle="Тот же размер выборки",
    )
    fig.suptitle("Два одиночных прогона подряд: 4D и 5D", fontsize=12.5)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    return (
        "Два маленьких точечных графика рядом для случайных точек при проекциях 4D -> 3D и 5D -> 4D; "
        f"среднее искажение в этих прогонах равно {mean4:.1%} и {mean5:.1%}"
    )


def plot_distortion_vs_target_dim(output_path: Path) -> str:
    rng = np.random.default_rng(19)
    source_dims = list(range(3, 33))
    n_points = 8
    trials = 160
    means = []
    p20 = []
    p80 = []
    cube_median = float(np.median(pairwise_distances(cube_vertices())))

    for source_dim in source_dims:
        errors = []
        for _ in range(trials):
            points = scaled_random_points(
                rng,
                source_dim=source_dim,
                n_points=n_points,
                target_median_distance=cube_median,
            )
            original_distances = pairwise_distances(points)
            projected = random_projection(points, target_dim=source_dim - 1, rng=rng)
            projected_distances = pairwise_distances(projected)
            errors.append(mean_relative_distortion(original_distances, projected_distances))
        means.append(float(np.mean(errors)))
        p20.append(float(np.percentile(errors, 20)))
        p80.append(float(np.percentile(errors, 80)))

    fig, ax = plt.subplots(figsize=(8.6, 5.2))
    ax.plot(source_dims, means, color="#d62828", linewidth=2.4)
    ax.fill_between(source_dims, p20, p80, color="#f4a261", alpha=0.28)
    ax.scatter([3, 4, 5], [means[0], means[1], means[2]], color="#1d3557", s=34, zorder=5)
    ax.set_title("Если терять только одну координату, высокие размерности страдают меньше")
    ax.set_xlabel("Исходная размерность D")
    ax.set_ylabel("Среднее относительное искажение при проекции D -> D-1")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(fig)

    return (
        "График среднего относительного искажения при случайной проекции из D в D-1: "
        "по мере роста исходной размерности потеря одной координаты в среднем вредит меньше"
    )


def plot_epsilon_corridor_scatter(output_path: Path) -> str:
    rng = np.random.default_rng(5)
    source_dim = 32
    target_dim = 16
    n_points = 20
    eps = 0.25
    cube_median = float(np.median(pairwise_distances(cube_vertices())))

    points = scaled_random_points(
        rng,
        source_dim=source_dim,
        n_points=n_points,
        target_median_distance=cube_median,
    )
    projected = random_projection(points, target_dim=target_dim, rng=rng)
    original_distances = pairwise_distances(points)
    projected_distances = pairwise_distances(projected)
    distortion = relative_distortion(original_distances, projected_distances)
    inside_mask = distortion <= eps
    inside_count = int(np.sum(inside_mask))
    total_count = int(len(distortion))

    lo = float(min(original_distances.min(), projected_distances.min()))
    hi = float(max(original_distances.max(), projected_distances.max()))
    padding = (hi - lo) * 0.08 if hi > lo else 0.2
    axis_min = max(0.0, lo - padding)
    axis_max = hi + padding
    x_vals = np.linspace(axis_min, axis_max, 300)

    fig, ax = plt.subplots(figsize=(6.2, 5.1))
    ax.fill_between(
        x_vals,
        (1 - eps) * x_vals,
        (1 + eps) * x_vals,
        color="#f4a261",
        alpha=0.26,
    )
    ax.plot(x_vals, x_vals, color="#1f2933", linewidth=1.5, alpha=0.95)
    ax.plot(x_vals, (1 - eps) * x_vals, color="#457b9d", linewidth=1.15, alpha=0.9)
    ax.plot(x_vals, (1 + eps) * x_vals, color="#457b9d", linewidth=1.15, alpha=0.9)
    ax.scatter(
        original_distances[inside_mask],
        projected_distances[inside_mask],
        s=32,
        color="#2a9d8f",
        alpha=0.78,
        edgecolors="white",
        linewidths=0.45,
    )
    ax.scatter(
        original_distances[~inside_mask],
        projected_distances[~inside_mask],
        s=36,
        color="#d62828",
        alpha=0.84,
        edgecolors="white",
        linewidths=0.45,
    )
    ax.set_xlim(axis_min, axis_max)
    ax.set_ylim(axis_min, axis_max)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.24)
    ax.set_xlabel("Расстояние до проекции")
    ax.set_ylabel("После проекции")
    ax.set_title("Что значит \"почти сохранились расстояния\"")
    ax.text(
        0.03,
        0.97,
        "Случайные точки: 32D -> 16D\n"
        f"ε = {eps:.0%}, внутри коридора: {inside_count}/{total_count} пар ({inside_count / total_count:.0%})",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9.2,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#dddddd", "alpha": 0.95},
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    return (
        "Точечный график попарных расстояний до и после случайной проекции 32D -> 16D: "
        "оранжевый клин показывает ε-коридор шириной 25% вокруг диагонали, "
        f"внутри него осталось {inside_count} из {total_count} пар точек"
    )


PLOT_BUILDERS = {
    "projection_3d_to_2d": ("projection_3d_to_2d.png", plot_projection_3d_to_2d),
    "cube_distance_shift": ("cube_distance_shift.png", plot_cube_distance_shift),
    "cube_mean_distortion": ("cube_mean_distortion.png", plot_cube_mean_distortion),
    "random_4d_mean_distortion": ("random_4d_mean_distortion.png", plot_random_4d_mean_distortion),
    "random_5d_mean_distortion": ("random_5d_mean_distortion.png", plot_random_5d_mean_distortion),
    "random_4d_5d_side_by_side": ("random_4d_5d_side_by_side.png", plot_random_4d_5d_side_by_side),
    "distortion_vs_target_dim": ("distortion_vs_target_dim.png", plot_distortion_vs_target_dim),
    "epsilon_corridor_scatter": ("epsilon_corridor_scatter.png", plot_epsilon_corridor_scatter),
}


def ensure_dirs() -> None:
    RESULT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def register_fonts() -> None:
    fonts = {
        "Body": FONT_DIR / "AdwaitaSans-Regular.ttf",
        "Body-Bold": FONT_DIR / "AdwaitaSans-Regular.ttf",
        "Body-Italic": FONT_DIR / "AdwaitaSans-Italic.ttf",
        "Body-BoldItalic": FONT_DIR / "AdwaitaSans-Italic.ttf",
    }
    for name, path in fonts.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))


def strip_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    _, rest = text.split("---\n", 1)
    frontmatter, body = rest.split("\n---\n", 1)
    return frontmatter.strip(), body.lstrip()


def build_markdown() -> str:
    source_text = SOURCE_PATH.read_text(encoding="utf-8")
    frontmatter, body = strip_frontmatter(source_text)

    plot_alts: dict[str, str] = {}
    for name, (filename, builder) in PLOT_BUILDERS.items():
        image_path = RESULT_IMAGES_DIR / filename
        alt = builder(image_path)
        shutil.copy2(image_path, ASSETS_DIR / filename)
        plot_alts[name] = alt

    def render_plot_reference(name: str) -> str:
        if name not in PLOT_BUILDERS:
            raise ValueError(f"Unknown plot marker: {name}")
        filename, _ = PLOT_BUILDERS[name]
        alt = plot_alts[name]
        return f"![{alt}](/assets/jl_projection/{filename})"

    def replace_plot(match: re.Match[str]) -> str:
        return render_plot_reference(match.group("name"))

    def replace_plot_tag(match: re.Match[str]) -> str:
        attrs = parse_tag_attributes(match.group("attrs"))
        name = attrs.get("name")
        if not name:
            raise ValueError("Plot tag requires name=\"...\"")
        return render_plot_reference(name)

    def replace_formula_tag(match: re.Match[str]) -> str:
        attrs = parse_tag_attributes(match.group("attrs"))
        filename, alt = render_formula_block(attrs, match.group("body"))
        return f"![{alt}](/assets/jl_projection/{filename})"

    rendered_body = FORMULA_TAG_PATTERN.sub(replace_formula_tag, body)
    rendered_body = PLOT_TAG_PATTERN.sub(replace_plot_tag, rendered_body)
    rendered_body = PLOT_PATTERN.sub(replace_plot, rendered_body).strip() + "\n"
    if frontmatter:
        return f"---\n{frontmatter}\n---\n\n{rendered_body}"
    return rendered_body


@dataclass
class Cursor:
    pdf: canvas.Canvas
    y: float
    width: float
    left: float
    bottom: float
    top: float

    def new_page(self) -> None:
        self.pdf.showPage()
        self.y = self.top


def draw_wrapped_text(cursor: Cursor, text: str, font_name: str, font_size: int, spacing: int = 6) -> None:
    cursor.pdf.setFont(font_name, font_size)
    char_width = max(40, int(cursor.width / (font_size * 0.55)))
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=char_width) or [""])

    line_height = font_size + spacing
    for line in lines:
        if cursor.y < cursor.bottom + line_height:
            cursor.new_page()
            cursor.pdf.setFont(font_name, font_size)
        cursor.pdf.drawString(cursor.left, cursor.y, line)
        cursor.y -= line_height


def draw_image(cursor: Cursor, image_path: Path, alt: str) -> None:
    reader = ImageReader(str(image_path))
    img_width, img_height = reader.getSize()
    usable_width = cursor.width
    usable_height = 320
    is_formula_image = image_path.name in FORMULA_IMAGE_FILENAMES
    scale = min(usable_width / img_width, usable_height / img_height)
    if is_formula_image:
        scale = min(scale, 0.88)
    draw_width = img_width * scale
    draw_height = img_height * scale

    if cursor.y < cursor.bottom + draw_height + 30:
        cursor.new_page()

    x = cursor.left + (usable_width - draw_width) / 2
    y = cursor.y - draw_height
    cursor.pdf.drawImage(reader, x, y, width=draw_width, height=draw_height, preserveAspectRatio=True)
    cursor.y = y - 18
    draw_wrapped_text(cursor, alt, "Body-Italic", 10, spacing=3)
    cursor.y -= 8


def render_preview_pdf(markdown_text: str) -> None:
    register_fonts()
    pdf = canvas.Canvas(str(PREVIEW_PDF_PATH), pagesize=A4)
    page_width, page_height = A4
    cursor = Cursor(
        pdf=pdf,
        y=page_height - 56,
        width=page_width - 96,
        left=48,
        bottom=48,
        top=page_height - 56,
    )

    body = strip_frontmatter(markdown_text)[1]
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line:
            cursor.y -= 10
            continue

        image_match = IMAGE_PATTERN.fullmatch(line.strip())
        if image_match:
            src = image_match.group("src")
            alt = image_match.group("alt")
            image_path = REPO_ROOT / src.lstrip("/")
            draw_image(cursor, image_path, alt)
            continue

        if line.startswith("# "):
            draw_wrapped_text(cursor, line[2:], "Body-Bold", 20, spacing=8)
            cursor.y -= 4
            continue

        if line.startswith("## "):
            draw_wrapped_text(cursor, line[3:], "Body-Bold", 15, spacing=6)
            cursor.y -= 2
            continue

        if line.startswith("<!--"):
            continue

        draw_wrapped_text(cursor, line, "Body", 11, spacing=4)

    pdf.save()


def main() -> None:
    ensure_dirs()
    markdown_text = build_markdown()
    POST_PATH.write_text(markdown_text, encoding="utf-8")
    render_preview_pdf(markdown_text)
    print(f"Wrote {POST_PATH}")
    print(f"Wrote {PREVIEW_PDF_PATH}")


if __name__ == "__main__":
    main()
