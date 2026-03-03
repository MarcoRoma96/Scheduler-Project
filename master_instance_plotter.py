from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import json
from textwrap import dedent

from src.common.custom_types import MasterInstance
from src.common.file_load_and_dump import decode_master_instance
from src.plotters.master_instance_overview import (
    get_daily_average_spread_capacity,
    get_daily_median_window_overlaps,
    get_daily_weighted_median_window_overlaps,
    get_max_patient_window_overlap,
    get_max_patient_weighted_window_overlap,
    get_spread_capacity_heatmap_max,
    plot_average_window_overlap_by_day,
    plot_grouped_instance_average_spread_capacity_distribution,
    plot_grouped_instance_median_window_overlap_distribution,
    plot_grouped_instance_weighted_median_window_overlap_distribution,
    plot_master_instance_windows,
    plot_spread_capacity_heatmap,
    plot_weighted_window_overlap_by_day,
)

# Enable shared scales only where they improve comparability without distorting layout.
USE_GLOBAL_PLOT_SCALES = True


def iter_instance_files(input_path: Path) -> list[tuple[str, Path]]:
    direct_instance_files = sorted(input_path.glob('*.json'))
    if len(direct_instance_files) > 0:
        return [('root', instance_file) for instance_file in direct_instance_files]

    instance_files: list[tuple[str, Path]] = []
    for group_directory in sorted(path for path in input_path.iterdir() if path.is_dir()):
        if group_directory.name == 'plots_instances':
            continue

        for instance_file in sorted(group_directory.glob('*.json')):
            instance_files.append((group_directory.name, instance_file))

    return instance_files


def main() -> None:
    parser = ArgumentParser(
        prog='master_instance_plotter.py',
        formatter_class=RawTextHelpFormatter,
        description=dedent(
            """\
            Generate compact overview plots directly from master instances.

            The script scans an instances root and creates plots under:
              <input>/plots_instances/<group>/<instance_stem>/

            Currently implemented:
              - patient_windows_gantt.png
                Gantt-like view of patient request windows on the day axis.
              - average_window_overlap_by_day.png
                Daily boxplots of active-window counts across patients.
              - weighted_window_overlap_by_day.png
                Daily boxplots where each active window contributes 1/window_length.
              - spread_capacity_heatmap.png
                Heatmap care_unit x day of spread(day)/capacity(day).
              - instance_daily_median_window_overlap_distribution.png
                Aggregate comparison across instances, visually grouped by group.
              - instance_daily_weighted_window_overlap_distribution.png
                Aggregate comparison across instances of daily weighted patient-median overlaps.
              - instance_daily_average_spread_capacity_distribution.png
                Aggregate comparison across instances of daily mean spread/capacity across care units.
            """
        ),
        epilog=dedent(
            """\
            Examples:
              python master_instance_plotter.py -i instances
              python master_instance_plotter.py -i ist_prova
              python master_instance_plotter.py -i instances --skip-existing
            """
        ),
    )
    parser.add_argument(
        '-i', '--input',
        type=Path,
        required=True,
        help='Instances root. Expected layout: <root>/<group>/inst_*.json or direct *.json files.',
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip plot generation when the target PNG already exists.',
    )

    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f'Input path not found: {input_path}')

    instance_files = iter_instance_files(input_path)
    if len(instance_files) == 0:
        raise FileNotFoundError(f'No instance JSON files found under {input_path}')

    output_root = input_path.joinpath('plots_instances')
    output_root.mkdir(exist_ok=True)
    grouped_instance_daily_medians: dict[str, dict[str, list[float]]] = {}
    grouped_instance_daily_weighted_medians: dict[str, dict[str, list[float]]] = {}
    grouped_instance_daily_spread_capacity_means: dict[str, dict[str, list[float]]] = {}
    group_sort_keys: dict[str, tuple[int, int, str]] = {}
    loaded_instances: list[tuple[str, Path, MasterInstance]] = []

    for group_name, instance_file in instance_files:
        with open(instance_file, 'r') as file:
            instance = decode_master_instance(json.load(file))
        loaded_instances.append((group_name, instance_file, instance))

    global_window_overlap_ymax: float | None = None
    global_weighted_window_overlap_ymax: float | None = None
    global_spread_capacity_vmax: float | None = None

    if USE_GLOBAL_PLOT_SCALES:
        global_window_overlap_ymax = max(
            (float(get_max_patient_window_overlap(instance)) for _, _, instance in loaded_instances),
            default=0.0,
        )
        global_weighted_window_overlap_ymax = max(
            (get_max_patient_weighted_window_overlap(instance) for _, _, instance in loaded_instances),
            default=0.0,
        )
        global_spread_capacity_vmax = max(
            (get_spread_capacity_heatmap_max(instance) for _, _, instance in loaded_instances),
            default=0.0,
        )
        if global_window_overlap_ymax <= 0.0:
            global_window_overlap_ymax = None
        if global_weighted_window_overlap_ymax <= 0.0:
            global_weighted_window_overlap_ymax = None
        if global_spread_capacity_vmax <= 0.0:
            global_spread_capacity_vmax = None

    for group_name, instance_file, instance in loaded_instances:

        instance_output_path = output_root.joinpath(group_name, instance_file.stem)
        instance_output_path.mkdir(parents=True, exist_ok=True)

        patient_windows_plot_path = instance_output_path.joinpath('patient_windows_gantt.png')
        daily_overlap_plot_path = instance_output_path.joinpath('average_window_overlap_by_day.png')
        weighted_daily_overlap_plot_path = instance_output_path.joinpath('weighted_window_overlap_by_day.png')
        spread_capacity_heatmap_path = instance_output_path.joinpath('spread_capacity_heatmap.png')

        print(f'Plotting {group_name}/{instance_file.stem}...')
        if not (args.skip_existing and patient_windows_plot_path.exists()):
            plot_master_instance_windows(
                instance,
                patient_windows_plot_path,
                title=f'Patient request windows of {instance_file.stem} ({group_name})',
            )
        else:
            print(f'  skipping existing plot: {patient_windows_plot_path.name}')

        if not (args.skip_existing and daily_overlap_plot_path.exists()):
            plot_average_window_overlap_by_day(
                instance,
                daily_overlap_plot_path,
                title=f'Average window overlap by day of {instance_file.stem} ({group_name})',
                ymax=global_window_overlap_ymax,
            )
        else:
            print(f'  skipping existing plot: {daily_overlap_plot_path.name}')

        if not (args.skip_existing and weighted_daily_overlap_plot_path.exists()):
            plot_weighted_window_overlap_by_day(
                instance,
                weighted_daily_overlap_plot_path,
                title=f'Weighted window overlap by day of {instance_file.stem} ({group_name})',
                ymax=global_weighted_window_overlap_ymax,
            )
        else:
            print(f'  skipping existing plot: {weighted_daily_overlap_plot_path.name}')

        if not (args.skip_existing and spread_capacity_heatmap_path.exists()):
            plot_spread_capacity_heatmap(
                instance,
                spread_capacity_heatmap_path,
                title=f'Spread/capacity by care unit and day of {instance_file.stem} ({group_name})',
                vmax=global_spread_capacity_vmax,
            )
        else:
            print(f'  skipping existing plot: {spread_capacity_heatmap_path.name}')

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

        grouped_instance_daily_medians.setdefault(group_name, {})[instance_file.stem] = get_daily_median_window_overlaps(instance)
        grouped_instance_daily_weighted_medians.setdefault(group_name, {})[instance_file.stem] = get_daily_weighted_median_window_overlaps(instance)
        grouped_instance_daily_spread_capacity_means.setdefault(group_name, {})[instance_file.stem] = get_daily_average_spread_capacity(instance)

    ordered_grouped_instance_daily_medians = {
        group_name: grouped_instance_daily_medians[group_name]
        for group_name in sorted(grouped_instance_daily_medians.keys(), key=lambda name: group_sort_keys[name])
    }
    ordered_grouped_instance_daily_weighted_medians = {
        group_name: grouped_instance_daily_weighted_medians[group_name]
        for group_name in sorted(grouped_instance_daily_weighted_medians.keys(), key=lambda name: group_sort_keys[name])
    }
    ordered_grouped_instance_daily_spread_capacity_means = {
        group_name: grouped_instance_daily_spread_capacity_means[group_name]
        for group_name in sorted(grouped_instance_daily_spread_capacity_means.keys(), key=lambda name: group_sort_keys[name])
    }

    grouped_distribution_plot_path = output_root.joinpath('instance_daily_median_window_overlap_distribution.png')
    if not (args.skip_existing and grouped_distribution_plot_path.exists()):
        plot_grouped_instance_median_window_overlap_distribution(
            ordered_grouped_instance_daily_medians,
            grouped_distribution_plot_path,
            title='Distribution of daily patient-median window overlaps by instance',
        )
    else:
        print(f'Skipping existing plot: {grouped_distribution_plot_path.name}')

    weighted_grouped_distribution_plot_path = output_root.joinpath('instance_daily_weighted_window_overlap_distribution.png')
    if not (args.skip_existing and weighted_grouped_distribution_plot_path.exists()):
        plot_grouped_instance_weighted_median_window_overlap_distribution(
            ordered_grouped_instance_daily_weighted_medians,
            weighted_grouped_distribution_plot_path,
            title='Distribution of daily weighted patient-median window overlaps by instance',
        )
    else:
        print(f'Skipping existing plot: {weighted_grouped_distribution_plot_path.name}')

    spread_capacity_distribution_plot_path = output_root.joinpath('instance_daily_average_spread_capacity_distribution.png')
    if not (args.skip_existing and spread_capacity_distribution_plot_path.exists()):
        plot_grouped_instance_average_spread_capacity_distribution(
            ordered_grouped_instance_daily_spread_capacity_means,
            spread_capacity_distribution_plot_path,
            title='Distribution of daily mean spread/capacity by instance',
        )
    else:
        print(f'Skipping existing plot: {spread_capacity_distribution_plot_path.name}')


if __name__ == '__main__':
    main()
