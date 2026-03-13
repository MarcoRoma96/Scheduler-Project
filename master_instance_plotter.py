from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import json
from textwrap import dedent

import yaml

from src.common.custom_types import MasterInstance
from src.common.file_load_and_dump import decode_master_instance
from src.common.plot_catalog import (
    MASTER_INSTANCE_AGGREGATE_PLOT_NAMES,
    MASTER_INSTANCE_PER_INSTANCE_PLOT_NAMES,
    MASTER_INSTANCE_PLOT_NAMES,
)
from src.common.tools import is_combination_to_do
from src.plotters.master_instance_overview import (
    get_daily_average_spread_capacity,
    get_daily_median_window_overlaps,
    get_daily_weighted_median_window_overlaps,
    get_duration_weighted_request_count_per_patient,
    get_max_patient_weighted_window_overlap,
    get_max_patient_window_overlap,
    get_request_count_per_patient,
    get_same_service_overlapping_window_count_per_patient,
    get_spread_capacity_heatmap_max,
    plot_average_window_overlap_by_day,
    plot_grouped_instance_average_spread_capacity_distribution,
    plot_grouped_instance_duration_weighted_request_count_distribution,
    plot_grouped_instance_median_window_overlap_distribution,
    plot_grouped_instance_request_count_distribution,
    plot_grouped_instance_same_service_overlapping_window_distribution,
    plot_grouped_instance_weighted_median_window_overlap_distribution,
    plot_master_instance_windows,
    plot_spread_capacity_heatmap,
    plot_weighted_window_overlap_by_day,
)

DEFAULT_CONFIG = {
    "groups_to_do": ["all"],
    "groups_to_avoid": [],
    "instances_to_do": ["all"],
    "instances_to_avoid": [],
    "plots_to_do": list(MASTER_INSTANCE_PLOT_NAMES),
    "skip_existing": False,
    "use_global_value_scales": True,
}

PLOT_OUTPUT_NAMES = {
    "patient_windows_gantt": "patient_windows_gantt.png",
    "average_window_overlap_by_day": "average_window_overlap_by_day.png",
    "weighted_window_overlap_by_day": "weighted_window_overlap_by_day.png",
    "spread_capacity_heatmap": "spread_capacity_heatmap.png",
    "instance_daily_median_window_overlap_distribution": "instance_daily_median_window_overlap_distribution.png",
    "instance_daily_weighted_window_overlap_distribution": "instance_daily_weighted_window_overlap_distribution.png",
    "instance_daily_average_spread_capacity_distribution": "instance_daily_average_spread_capacity_distribution.png",
    "instance_request_count_distribution": "instance_request_count_distribution.png",
    "instance_duration_weighted_request_count_distribution": "instance_duration_weighted_request_count_distribution.png",
    "instance_same_service_overlapping_window_distribution": "instance_same_service_overlapping_window_distribution.png",
}


def iter_instance_files(input_path: Path) -> list[tuple[str, Path]]:
    direct_instance_files = sorted(input_path.glob("*.json"))
    if len(direct_instance_files) > 0:
        return [("root", instance_file) for instance_file in direct_instance_files]

    instance_files: list[tuple[str, Path]] = []
    for group_directory in sorted(path for path in input_path.iterdir() if path.is_dir()):
        if group_directory.name == "plots_instances":
            continue

        for instance_file in sorted(group_directory.glob("*.json")):
            instance_files.append((group_directory.name, instance_file))

    return instance_files


def load_config(config_path: Path | None) -> dict:
    config = dict(DEFAULT_CONFIG)
    if config_path is None:
        return config

    with open(config_path, "r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}

    if not isinstance(loaded, dict):
        raise ValueError("Top-level YAML content must be a mapping/object.")

    config.update(loaded)
    return config


def get_selected_plots(config: dict) -> list[str]:
    configured = config.get("plots_to_do", [])
    if not isinstance(configured, list):
        return []

    selected: list[str] = []
    for plot_name in configured:
        if isinstance(plot_name, str) and plot_name in MASTER_INSTANCE_PLOT_NAMES and plot_name not in selected:
            selected.append(plot_name)
    return selected


def should_skip_existing(config: dict, cli_skip_existing: bool) -> bool:
    return cli_skip_existing or bool(config.get("skip_existing", False))


def should_use_global_value_scales(config: dict) -> bool:
    return bool(config.get("use_global_value_scales", DEFAULT_CONFIG["use_global_value_scales"]))


def should_process_instance(group_name: str, instance_name: str, config: dict) -> bool:
    return is_combination_to_do(None, group_name, instance_name, config)


def ensure_output_root(input_path: Path) -> Path:
    output_root = input_path.joinpath("plots_instances")
    output_root.mkdir(exist_ok=True)
    return output_root


def main() -> None:
    parser = ArgumentParser(
        prog="master_instance_plotter.py",
        formatter_class=RawTextHelpFormatter,
        description=dedent(
            """\
            Generate compact overview plots directly from master instances.

            The script scans an instances root and creates plots under:
              <input>/plots_instances/<group>/<instance_stem>/

            Plot selection and filters can be controlled either by CLI or by a YAML
            config file compatible with configs/master_instance_plotter_config.yaml.
            """
        ),
        epilog=dedent(
            """\
            Examples:
              python master_instance_plotter.py -c configs/master_instance_plotter_config.yaml -i instances
              python master_instance_plotter.py -i ist_prova
              python master_instance_plotter.py -i instances --skip-existing

            Recognized plots_to_do values:
              patient_windows_gantt
              average_window_overlap_by_day
              weighted_window_overlap_by_day
              spread_capacity_heatmap
              instance_daily_median_window_overlap_distribution
              instance_daily_weighted_window_overlap_distribution
              instance_daily_average_spread_capacity_distribution
              instance_request_count_distribution
              instance_duration_weighted_request_count_distribution
              instance_same_service_overlapping_window_distribution
            """
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to the master-instance plotter YAML config.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Instances root. Expected layout: <root>/<group>/inst_*.json or direct *.json files.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip plot generation when the target PNG already exists. Overrides YAML skip_existing=true.",
    )

    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    config_path = args.config.resolve() if args.config is not None else None
    config = load_config(config_path)
    selected_plots = set(get_selected_plots(config))
    if len(selected_plots) == 0:
        print("No plots selected in plots_to_do, nothing to do.")
        return

    instance_files = [
        (group_name, instance_file)
        for group_name, instance_file in iter_instance_files(input_path)
        if should_process_instance(group_name, instance_file.stem, config)
    ]
    if len(instance_files) == 0:
        raise FileNotFoundError(f"No matching instance JSON files found under {input_path}")

    skip_existing = should_skip_existing(config, args.skip_existing)
    use_global_value_scales = should_use_global_value_scales(config)

    output_root = ensure_output_root(input_path)
    per_instance_plots = set(MASTER_INSTANCE_PER_INSTANCE_PLOT_NAMES) & selected_plots
    aggregate_plots = set(MASTER_INSTANCE_AGGREGATE_PLOT_NAMES) & selected_plots

    grouped_instance_daily_medians: dict[str, dict[str, list[float]]] = {}
    grouped_instance_daily_weighted_medians: dict[str, dict[str, list[float]]] = {}
    grouped_instance_daily_spread_capacity_means: dict[str, dict[str, list[float]]] = {}
    grouped_instance_request_counts: dict[str, dict[str, list[float]]] = {}
    grouped_instance_weighted_request_counts: dict[str, dict[str, list[float]]] = {}
    grouped_instance_same_service_overlapping_window_counts: dict[str, dict[str, list[float]]] = {}
    group_sort_keys: dict[str, tuple[int, int, str]] = {}
    loaded_instances: list[tuple[str, Path, MasterInstance]] = []

    for group_name, instance_file in instance_files:
        with open(instance_file, "r", encoding="utf-8") as file:
            instance = decode_master_instance(json.load(file))
        loaded_instances.append((group_name, instance_file, instance))

    global_window_overlap_ymax: float | None = None
    global_weighted_window_overlap_ymax: float | None = None
    global_spread_capacity_vmax: float | None = None

    if use_global_value_scales:
        if "average_window_overlap_by_day" in selected_plots:
            global_window_overlap_ymax = max(
                (float(get_max_patient_window_overlap(instance)) for _, _, instance in loaded_instances),
                default=0.0,
            )
            if global_window_overlap_ymax <= 0.0:
                global_window_overlap_ymax = None

        if "weighted_window_overlap_by_day" in selected_plots:
            global_weighted_window_overlap_ymax = max(
                (get_max_patient_weighted_window_overlap(instance) for _, _, instance in loaded_instances),
                default=0.0,
            )
            if global_weighted_window_overlap_ymax <= 0.0:
                global_weighted_window_overlap_ymax = None

        if "spread_capacity_heatmap" in selected_plots:
            global_spread_capacity_vmax = max(
                (get_spread_capacity_heatmap_max(instance) for _, _, instance in loaded_instances),
                default=0.0,
            )
            if global_spread_capacity_vmax <= 0.0:
                global_spread_capacity_vmax = None

    for group_name, instance_file, instance in loaded_instances:
        instance_output_path = output_root.joinpath(group_name, instance_file.stem)
        if len(per_instance_plots) > 0:
            instance_output_path.mkdir(parents=True, exist_ok=True)

        print(f"Plotting {group_name}/{instance_file.stem}...")

        if "patient_windows_gantt" in selected_plots:
            plot_path = instance_output_path.joinpath(PLOT_OUTPUT_NAMES["patient_windows_gantt"])
            if not (skip_existing and plot_path.exists()):
                plot_master_instance_windows(
                    instance,
                    plot_path,
                    title=f"Patient request windows of {instance_file.stem} ({group_name})",
                )
            else:
                print(f"  skipping existing plot: {plot_path.name}")

        if "average_window_overlap_by_day" in selected_plots:
            plot_path = instance_output_path.joinpath(PLOT_OUTPUT_NAMES["average_window_overlap_by_day"])
            if not (skip_existing and plot_path.exists()):
                plot_average_window_overlap_by_day(
                    instance,
                    plot_path,
                    title=f"Average window overlap by day of {instance_file.stem} ({group_name})",
                    ymax=global_window_overlap_ymax,
                )
            else:
                print(f"  skipping existing plot: {plot_path.name}")

        if "weighted_window_overlap_by_day" in selected_plots:
            plot_path = instance_output_path.joinpath(PLOT_OUTPUT_NAMES["weighted_window_overlap_by_day"])
            if not (skip_existing and plot_path.exists()):
                plot_weighted_window_overlap_by_day(
                    instance,
                    plot_path,
                    title=f"Weighted window overlap by day of {instance_file.stem} ({group_name})",
                    ymax=global_weighted_window_overlap_ymax,
                )
            else:
                print(f"  skipping existing plot: {plot_path.name}")

        if "spread_capacity_heatmap" in selected_plots:
            plot_path = instance_output_path.joinpath(PLOT_OUTPUT_NAMES["spread_capacity_heatmap"])
            if not (skip_existing and plot_path.exists()):
                plot_spread_capacity_heatmap(
                    instance,
                    plot_path,
                    title=f"Spread/capacity by care unit and day of {instance_file.stem} ({group_name})",
                    vmax=global_spread_capacity_vmax,
                )
            else:
                print(f"  skipping existing plot: {plot_path.name}")

        if group_name not in group_sort_keys:
            group_sort_keys[group_name] = (
                len(instance.patients),
                len({
                    care_unit_name
                    for day in instance.days.values()
                    for care_unit_name in day.care_units.keys()
                }),
                group_name,
            )

        if "instance_daily_median_window_overlap_distribution" in aggregate_plots:
            grouped_instance_daily_medians.setdefault(group_name, {})[instance_file.stem] = get_daily_median_window_overlaps(instance)
        if "instance_daily_weighted_window_overlap_distribution" in aggregate_plots:
            grouped_instance_daily_weighted_medians.setdefault(group_name, {})[instance_file.stem] = get_daily_weighted_median_window_overlaps(instance)
        if "instance_daily_average_spread_capacity_distribution" in aggregate_plots:
            grouped_instance_daily_spread_capacity_means.setdefault(group_name, {})[instance_file.stem] = get_daily_average_spread_capacity(instance)
        if "instance_request_count_distribution" in aggregate_plots:
            grouped_instance_request_counts.setdefault(group_name, {})[instance_file.stem] = [
                float(value) for value in get_request_count_per_patient(instance)
            ]
        if "instance_duration_weighted_request_count_distribution" in aggregate_plots:
            grouped_instance_weighted_request_counts.setdefault(group_name, {})[instance_file.stem] = [
                float(value) for value in get_duration_weighted_request_count_per_patient(instance)
            ]
        if "instance_same_service_overlapping_window_distribution" in aggregate_plots:
            grouped_instance_same_service_overlapping_window_counts.setdefault(group_name, {})[instance_file.stem] = [
                float(value) for value in get_same_service_overlapping_window_count_per_patient(instance)
            ]

    ordered_group_names = sorted(group_sort_keys.keys(), key=lambda name: group_sort_keys[name])

    if "instance_daily_median_window_overlap_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_daily_median_window_overlap_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_median_window_overlap_distribution(
                {group_name: grouped_instance_daily_medians[group_name] for group_name in ordered_group_names if group_name in grouped_instance_daily_medians},
                grouped_plot_path,
                title="Distribution of daily patient-median window overlaps by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")

    if "instance_daily_weighted_window_overlap_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_daily_weighted_window_overlap_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_weighted_median_window_overlap_distribution(
                {group_name: grouped_instance_daily_weighted_medians[group_name] for group_name in ordered_group_names if group_name in grouped_instance_daily_weighted_medians},
                grouped_plot_path,
                title="Distribution of daily weighted patient-median window overlaps by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")

    if "instance_daily_average_spread_capacity_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_daily_average_spread_capacity_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_average_spread_capacity_distribution(
                {group_name: grouped_instance_daily_spread_capacity_means[group_name] for group_name in ordered_group_names if group_name in grouped_instance_daily_spread_capacity_means},
                grouped_plot_path,
                title="Distribution of daily mean spread/capacity by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")

    if "instance_request_count_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_request_count_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_request_count_distribution(
                {group_name: grouped_instance_request_counts[group_name] for group_name in ordered_group_names if group_name in grouped_instance_request_counts},
                grouped_plot_path,
                title="Distribution of request counts per patient by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")

    if "instance_duration_weighted_request_count_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_duration_weighted_request_count_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_duration_weighted_request_count_distribution(
                {group_name: grouped_instance_weighted_request_counts[group_name] for group_name in ordered_group_names if group_name in grouped_instance_weighted_request_counts},
                grouped_plot_path,
                title="Distribution of duration-weighted request counts per patient by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")

    if "instance_same_service_overlapping_window_distribution" in aggregate_plots:
        grouped_plot_path = output_root.joinpath(PLOT_OUTPUT_NAMES["instance_same_service_overlapping_window_distribution"])
        if not (skip_existing and grouped_plot_path.exists()):
            plot_grouped_instance_same_service_overlapping_window_distribution(
                {
                    group_name: grouped_instance_same_service_overlapping_window_counts[group_name]
                    for group_name in ordered_group_names
                    if group_name in grouped_instance_same_service_overlapping_window_counts
                },
                grouped_plot_path,
                title="Distribution of same-service overlapping windows per patient by instance",
            )
        else:
            print(f"Skipping existing plot: {grouped_plot_path.name}")


if __name__ == "__main__":
    main()
