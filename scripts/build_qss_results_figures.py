#!/usr/bin/env python3
"""Build manuscript figures directly from the frozen QSS evaluation outputs.

Every figure is drawn at its printed size (the manuscript text width) so that
font sizes match the body text without scaling, and every mark is a vector
path: no raster images are embedded in the PDF outputs.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "qss-failure-modes-manuscript" / "figures"

CONDITIONS = [
    # experiment, corpus, cutoff, total held-out, reachable held-out, per-bag results
    ("E01", "C1", 2022, 57, 13, "outputs/qss-late-pruner-v2-2022/per_bag_results.csv"),
    ("E02", "C1", 2023, 48, 11, "outputs/qss-late-pruner-v2-2023/per_bag_results.csv"),
    ("E03", "C1", 2024, 28, 8, "outputs/qss-late-pruner-v2-2024/per_bag_results.csv"),
    ("E04", "C2", 2022, 17, 3, "outputs/qss-multicorpus/C2/evaluation/2022/per_bag_results.csv"),
    ("E05", "C2", 2023, 14, 2, "outputs/qss-multicorpus/C2/evaluation/2023/per_bag_results.csv"),
    ("E06", "C2", 2024, 6, 1, "outputs/qss-multicorpus/C2/evaluation/2024/per_bag_results.csv"),
    ("E07", "C3", 2022, 9, 1, "outputs/qss-multicorpus/C3/evaluation/2022/per_bag_results.csv"),
    ("E08", "C3", 2023, 7, 0, "outputs/qss-multicorpus/C3/evaluation/2023/per_bag_results.csv"),
    ("E09", "C3", 2024, 6, 0, "outputs/qss-multicorpus/C3/evaluation/2024/per_bag_results.csv"),
    ("E10", "C4", 2024, 7, 3, "outputs/qss-multicorpus/C4/evaluation/2024/per_bag_results.csv"),
    ("E11", "C5", 2023, 0, 0, "outputs/qss-multicorpus/C5/evaluation/2023/per_bag_results.csv"),
    ("E12", "C6", 2024, 0, 0, "outputs/qss-multicorpus/C6/evaluation/2024/per_bag_results.csv"),
]

# Palette shared with the TikZ diagrams in the manuscript preamble.
BLUE = "#234F72"
LIGHT_BLUE = "#8FB8D2"
GOLD = "#B7791F"
LIGHT_GOLD = "#E6C56B"
GRAY = "#6B7280"
INK = "#25313B"
MUTED = "#4A5560"
RULE = "#D7DCE1"
BAND = "#F4F6F8"
PALE_GRAY = "#EEF1F4"

# A4 with 2.45 cm margins gives a 16.1 cm (6.34 in) text block.
TEXT_WIDTH_IN = 6.30
MINUS = "−"

SENSITIVITY_CASES = [
    ("C1-2022", "E01"),
    ("C1-2023", "E02"),
    ("C1-2024", "E03"),
    ("C2-2022", "E04"),
    ("C2-2023", "E05"),
    ("C2-2024", "E06"),
    ("C3-2022", "E07"),
    ("C3-2023", "E08"),
    ("C3-2024", "E09"),
    ("C4-2024", "E10"),
    ("C5-2023", "E11"),
    ("C6-2024", "E12"),
]

SENSITIVITY_VARIANTS = [
    ("m20", "$M=20$"),
    ("m50", "$M=50$"),
    ("k15", "$K=15$"),
    ("tau_g_012", "$\\tau_g=.12$"),
    ("tau_g_024", "$\\tau_g=.24$"),
    ("tau_role_005", "$\\tau_{\\mathrm{role}}=.05$"),
    ("tau_role_015", "$\\tau_{\\mathrm{role}}=.15$"),
    ("kappa_2", "$\\kappa=2$"),
    ("kappa_4", "$\\kappa=4$"),
    ("group_blend_060", "fit .60/.40"),
    ("group_blend_080", "fit .80/.20"),
]


def apply_style() -> None:
    plt.rcParams.update({
        # DejaVu Sans is also the manuscript's \sffamily used in the diagrams.
        "font.family": "DejaVu Sans",
        "mathtext.fontset": "dejavusans",
        "font.size": 7.6,
        "axes.titlesize": 8.0,
        "axes.labelsize": 7.6,
        "axes.edgecolor": MUTED,
        "axes.linewidth": 0.6,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": INK,
        "xtick.labelsize": 7.2,
        "ytick.labelsize": 7.4,
        "xtick.major.width": 0.6,
        "xtick.major.size": 2.5,
        "legend.fontsize": 7.2,
        "hatch.linewidth": 0.5,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def signed(value: float) -> str:
    decimals = 2 if 0 < abs(value) < 0.1 else 1
    text = f"{value:+.{decimals}f}"
    return text.replace("-", MINUS)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mean_for_policy(rows: list[dict[str, str]], policy: str, metric: str) -> float | None:
    values = []
    for row in rows:
        if row["policy"] != policy:
            continue
        value = row.get(metric, "").strip()
        if value and value not in {"NA", "N/A"}:
            values.append(float(value))
    return sum(values) / len(values) if values else None


def policy_delta_pp(rows: list[dict[str, str]], metric: str) -> float | None:
    fixed = mean_for_policy(rows, "fixed_top10", metric)
    recursive = mean_for_policy(rows, "late_pruned_top10", metric)
    if fixed is None or recursive is None:
        return None
    return 100.0 * (recursive - fixed)


def exact_recall_delta_pp(rows: list[dict[str, str]], heldout_count: int) -> float | None:
    """Recursive-minus-fixed exact recall using all held-out references."""
    if heldout_count == 0:
        return None
    fixed_hits = mean_for_policy(rows, "fixed_top10", "exact_hits")
    recursive_hits = mean_for_policy(rows, "late_pruned_top10", "exact_hits")
    if fixed_hits is None or recursive_hits is None:
        return None
    return 100.0 * (recursive_hits - fixed_hits) / heldout_count


def build_outcome_figure() -> None:
    records = []
    for experiment, corpus, year, heldout_count, reachable_count, relative_path in CONDITIONS:
        rows = read_csv(ROOT / relative_path)
        if len(rows) != 450:
            raise ValueError(f"Expected 450 policy-bag rows for {experiment}, found {len(rows)}")
        records.append(
            {
                "label": f"{experiment}  {corpus} {year}",
                "expected": (
                    exact_recall_delta_pp(rows, heldout_count)
                    if reachable_count > 0 else None
                ),
                "expected_status": (
                    "estimable" if reachable_count > 0
                    else "zero_ceiling" if heldout_count > 0
                    else "undefined"
                ),
                "role": policy_delta_pp(rows, "group_recall"),
                "scope": policy_delta_pp(rows, "frozen_group_coverage"),
            }
        )

    panels = [
        ("expected", "A  Expected-reference recall", (-8, 16), [-5, 0, 5, 10, 15]),
        ("role", "B  Evidence-role recall", (-50, 44), [-40, -20, 0, 20, 40]),
        ("scope", "C  Frozen-group coverage", (-48, 14), [-40, -20, 0]),
    ]
    y_positions = list(range(len(records)))

    apply_style()
    fig, axes = plt.subplots(
        1, 3, figsize=(TEXT_WIDTH_IN, 4.35), sharey=True,
        gridspec_kw={"width_ratios": [1.2, 0.95, 1.12]},
    )

    for axis, (metric, title, limits, ticks) in zip(axes, panels):
        for y in y_positions[::2]:
            axis.axhspan(y - 0.5, y + 0.5, color=BAND, linewidth=0, zorder=0)
        axis.axvline(0, color=INK, linewidth=0.8, zorder=1)
        axis.set_xlim(*limits)
        axis.set_xticks(ticks)
        axis.set_xticklabels([f"{t:d}".replace("-", MINUS) for t in ticks])
        axis.grid(axis="x", color=RULE, linewidth=0.5, zorder=0)
        axis.set_axisbelow(True)
        axis.set_title(title, loc="left", fontweight="bold", pad=6)
        axis.tick_params(axis="y", length=0, pad=4)
        axis.spines[["top", "right", "left"]].set_visible(False)

        for y, record in zip(y_positions, records):
            value = record[metric]
            if value is None:
                label = (
                    "ceiling = 0"
                    if metric == "expected" and record["expected_status"] == "zero_ceiling"
                    else "N/A"
                )
                if label == "ceiling = 0":
                    axis.scatter(0, y, s=24, marker="x", color=GRAY,
                                 linewidth=1.0, zorder=3)
                axis.text(0.018 * (limits[1] - limits[0]), y, label, color=GRAY,
                          va="center", ha="left", fontsize=6.8, style="italic")
                continue
            color = BLUE if value > 0 else GOLD if value < 0 else GRAY
            marker = "o" if value > 0 else "D" if value < 0 else "s"
            face = color if value > 0 else "white"
            axis.hlines(y, min(0, value), max(0, value), color=color, linewidth=1.6, zorder=2)
            axis.scatter(value, y, s=24 if marker != "D" else 20, marker=marker,
                         facecolor=face, edgecolor=color, linewidth=1.0, zorder=3)
            # Labels sit beyond the marker on the side away from zero, so they
            # never overlap the stem, the marker, or the zero line.
            negative = value < 0
            axis.annotate(
                signed(value) if value else "0.0", (value, y),
                xytext=(-6 if negative else 6, 0), textcoords="offset points",
                color=color, va="center", ha="right" if negative else "left",
                fontsize=6.8,
            )

    axes[0].set_yticks(y_positions, [record["label"] for record in records])
    axes[0].set_ylim(len(records) - 0.5, -0.5)
    axes[1].set_xlabel("Recursive minus fixed (percentage points)", labelpad=5)
    legend = [
        Line2D([0], [0], marker="o", color=BLUE, markerfacecolor=BLUE, markersize=5,
               linewidth=1.6, label="Recursive higher"),
        Line2D([0], [0], marker="D", color=GOLD, markerfacecolor="white", markersize=4.5,
               linewidth=1.6, label="Fixed higher"),
        Line2D([0], [0], marker="s", color=GRAY, markerfacecolor="white", markersize=4.5,
               linewidth=0, label="No measured difference"),
        Line2D([0], [0], marker="x", color=GRAY, markersize=5,
               linewidth=0, label="Uninformative: ceiling = 0"),
    ]
    # One legend row below the axis label keeps the label and legend apart.
    fig.legend(handles=legend, loc="lower center", ncol=4, frameon=False,
               bbox_to_anchor=(0.55, 0.0), handlelength=1.8, columnspacing=1.4)
    fig.subplots_adjust(left=0.135, right=0.995, top=0.94, bottom=0.19, wspace=0.1)
    save_figure(fig, "cross_case_outcome_deltas")


def build_loss_location_figure() -> None:
    rows = read_csv(ROOT / "outputs/qss-stage-diagnostics/group_stage_localization.csv")
    targets = [
        ("C2  residual diagnostic", "2022–2024", "C2", {2022, 2023, 2024}, "continuity_residual_diagnostic"),
        ("C3  iterative alignment", "2022", "C3", {2022}, "iterative_alignment_consensus"),
        ("C4  defect-risk taxonomy", "2024", "C4", {2024}, "defect_risk_taxonomy"),
        ("C5  vehicle make/model", "2023", "C5", {2023}, "vehicle_make_model_scope"),
        ("C5  semantic parts", "2023", "C5", {2023}, "semantic_parts_occlusion"),
        ("C6  multigroup equity", "2024", "C6", {2024}, "gender_multigroup_equity"),
    ]

    data = []
    for label, period, corpus, years, group in targets:
        selected = [
            row for row in rows
            if row["corpus"] == corpus and int(row["cutoff"]) in years and row["group"] == group
        ]
        if len(selected) != len(years):
            raise ValueError(f"Incomplete stage-localization rows for {label}")
        final = sum(int(row["final_represented_bags"]) for row in selected)
        exploration_loss = sum(int(row["exploration_omission_bags"]) for row in selected)
        pruning_loss = sum(int(row["pruning_omission_bags"]) for row in selected)
        if pruning_loss != 0:
            raise ValueError("The manuscript claim requires zero recurring group-level pruning omissions")
        total = final + exploration_loss + pruning_loss
        data.append((f"{label}\n{period}", final, exploration_loss, total))

    apply_style()
    fig, axis = plt.subplots(figsize=(TEXT_WIDTH_IN, 3.05))
    y_positions = list(range(len(data)))
    final_pct = [100 * final / total for _, final, _, total in data]
    exploration_pct = [100 * loss / total for _, _, loss, total in data]

    height = 0.6
    axis.barh(y_positions, final_pct, color=BLUE, edgecolor="white", linewidth=0.6,
              height=height, zorder=2)
    axis.barh(y_positions, exploration_pct, left=final_pct, color=LIGHT_GOLD,
              edgecolor=GOLD, hatch="////", linewidth=0.6, height=height, zorder=2)

    for y, (_, final, exploration, total) in enumerate(data):
        if final:
            axis.text(final_pct[y] / 2, y, str(final), ha="center", va="center",
                      color="white", fontweight="bold", fontsize=7.2, zorder=3)
        if exploration:
            axis.text(final_pct[y] + exploration_pct[y] / 2, y, str(exploration),
                      ha="center", va="center", color="#3B2A00", fontweight="bold",
                      fontsize=7.2, zorder=3,
                      bbox={"boxstyle": "round,pad=0.18", "facecolor": LIGHT_GOLD,
                            "edgecolor": "none"})
        axis.text(102, y, f"n = {total}", ha="left", va="center", color=MUTED, fontsize=6.9)

    axis.set_yticks(y_positions, [item[0] for item in data])
    axis.set_ylim(len(data) - 0.5, -0.5)
    axis.set_xlim(0, 112)
    axis.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    axis.set_xlabel("Share of seed variants", labelpad=4)
    axis.grid(axis="x", color=RULE, linewidth=0.5, zorder=0)
    axis.set_axisbelow(True)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.spines["bottom"].set_bounds(0, 100)
    axis.tick_params(axis="y", length=0, pad=5)
    axis.legend(
        handles=[
            Patch(facecolor=BLUE, label="Represented in final set"),
            Patch(facecolor=LIGHT_GOLD, edgecolor=GOLD, hatch="////", linewidth=0.6,
                  label="Absent before exploration"),
            Patch(facecolor="white", edgecolor=GRAY, linewidth=0.6,
                  label="Lost at final pruning (0 in every row)"),
        ],
        loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.42, -0.2),
        handlelength=1.8, columnspacing=1.6,
    )
    fig.subplots_adjust(left=0.235, right=0.985, top=0.98, bottom=0.27)
    save_figure(fig, "group_loss_stage_localization")


def build_parameter_sensitivity_figure() -> None:
    rows = read_csv(
        ROOT / "outputs/qss-parameter-sensitivity-v1/sensitivity_results.csv"
    )
    by_key = {(row["variant_id"], row["case_id"]): row for row in rows}
    expected = len(SENSITIVITY_VARIANTS) * len(SENSITIVITY_CASES)
    if sum(key in by_key for key in (
        (variant, case)
        for variant, _ in SENSITIVITY_VARIANTS
        for case, _ in SENSITIVITY_CASES
    )) != expected:
        raise ValueError("Sensitivity result matrix is incomplete")

    matrix: list[list[int]] = []
    annotations: list[list[str]] = []
    changed_counts: list[int] = []
    for variant, _ in SENSITIVITY_VARIANTS:
        is_construct_threshold = variant.startswith("tau_role_")
        matrix_row: list[int] = []
        annotation_row: list[str] = []
        for case, _ in SENSITIVITY_CASES:
            row = by_key[(variant, case)]
            changed = row["pattern_matches_baseline"] == "False"
            matrix_row.append(2 if changed and is_construct_threshold else 1 if changed else 0)
            code = "".join(
                label
                for field, label in (
                    ("exact_direction", "E"),
                    ("role_direction", "R"),
                    ("group_direction", "G"),
                    ("group_loss_stage", "S"),
                )
                if field in row["changed_dimensions"].split(";")
            )
            annotation_row.append(code)
        matrix.append(matrix_row)
        annotations.append(annotation_row)
        changed_counts.append(sum(value > 0 for value in matrix_row))

    fills = [PALE_GRAY, LIGHT_GOLD, LIGHT_BLUE]
    edges = [PALE_GRAY, GOLD, BLUE]
    n_rows, n_cols = len(SENSITIVITY_VARIANTS), len(SENSITIVITY_CASES)

    apply_style()
    fig, (matrix_axis, bar_axis) = plt.subplots(
        1, 2, figsize=(TEXT_WIDTH_IN, 3.95), sharey=True,
        gridspec_kw={"width_ratios": [n_cols, 3.1]},
    )
    # Draw each cell as a vector rectangle (no imshow raster).
    gap = 0.07
    for y, (row, labels) in enumerate(zip(matrix, annotations)):
        for x, (value, label) in enumerate(zip(row, labels)):
            matrix_axis.add_patch(Rectangle(
                (x - 0.5 + gap, y - 0.5 + gap), 1 - 2 * gap, 1 - 2 * gap,
                facecolor=fills[value], edgecolor=edges[value],
                linewidth=0.6 if value else 0, joinstyle="round",
            ))
            if label:
                matrix_axis.text(x, y, label, ha="center", va="center", color="#2F2412",
                                 fontsize=6.4 if len(label) > 2 else 7.0, fontweight="bold")
    matrix_axis.set_xlim(-0.5, n_cols - 0.5)
    matrix_axis.set_ylim(n_rows - 0.5, -0.5)
    matrix_axis.set_aspect("auto")
    matrix_axis.set_xticks(range(n_cols), [label for _, label in SENSITIVITY_CASES])
    matrix_axis.set_yticks(range(n_rows), [label for _, label in SENSITIVITY_VARIANTS])
    matrix_axis.tick_params(axis="x", top=True, labeltop=True, bottom=False,
                            labelbottom=False, length=0, labelsize=6.9, pad=3)
    matrix_axis.tick_params(axis="y", length=0, pad=4)
    for spine in matrix_axis.spines.values():
        spine.set_visible(False)
    # Separate policy-parameter rows from the outcome-threshold rows.
    for boundary in (4.5, 6.5):
        matrix_axis.axhline(boundary, color=MUTED, linewidth=0.6, linestyle=(0, (2, 2)))
    matrix_axis.set_title("A  Case-level direction or stage changes", loc="left",
                          fontweight="bold", pad=18)

    for y, (variant, _) in enumerate(SENSITIVITY_VARIANTS):
        changed = changed_counts[y]
        threshold = variant.startswith("tau_role_")
        bar_axis.barh(y, n_cols, color=PALE_GRAY, height=0.62, zorder=1)
        if changed:
            bar_axis.barh(y, changed, color=LIGHT_BLUE if threshold else LIGHT_GOLD,
                          edgecolor=BLUE if threshold else GOLD, linewidth=0.6,
                          height=0.62, zorder=2)
        bar_axis.text(n_cols + 0.4, y, str(changed), va="center", ha="left",
                      fontsize=7.0, color=INK if changed else GRAY,
                      fontweight="bold" if changed else "normal")
    for boundary in (4.5, 6.5):
        bar_axis.axhline(boundary, color=MUTED, linewidth=0.6, linestyle=(0, (2, 2)))
    bar_axis.set_xlim(0, n_cols + 2.2)
    bar_axis.set_xticks([0, 6, 12])
    bar_axis.set_xlabel("Changed cases (of 12)", labelpad=4)
    bar_axis.tick_params(axis="y", left=False, labelleft=False)
    bar_axis.spines[["top", "right", "left"]].set_visible(False)
    bar_axis.spines["bottom"].set_bounds(0, n_cols)
    bar_axis.set_title("B  Changed cases", loc="left", fontweight="bold", pad=18)

    fig.legend(
        handles=[
            Patch(facecolor=PALE_GRAY, edgecolor=PALE_GRAY, label="Baseline pattern retained"),
            Patch(facecolor=LIGHT_GOLD, edgecolor=GOLD, linewidth=0.6, label="Policy-parameter change"),
            Patch(facecolor=LIGHT_BLUE, edgecolor=BLUE, linewidth=0.6, label="Outcome-threshold change"),
        ],
        loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.04),
        handlelength=1.6, columnspacing=1.8,
    )
    fig.text(
        0.5, 0.005,
        "E expected-reference direction    R evidence-role direction    "
        "G frozen-group direction    S group-loss stage",
        ha="center", fontsize=6.8, color=MUTED,
    )
    fig.subplots_adjust(left=0.115, right=0.985, top=0.885, bottom=0.205, wspace=0.06)
    save_figure(fig, "parameter_sensitivity_case_matrix")


def save_figure(fig: plt.Figure, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # No bbox_inches="tight": the PDF keeps its exact text-width size so the
    # manuscript includes it at 1:1 scale.
    fig.savefig(OUT / f"{stem}.pdf")
    fig.savefig(OUT / f"{stem}.svg")
    fig.savefig(OUT / f"{stem}.png", dpi=300, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    build_outcome_figure()
    build_loss_location_figure()
    build_parameter_sensitivity_figure()
    print(f"Wrote result figures to {OUT}")
