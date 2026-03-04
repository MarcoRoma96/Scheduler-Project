from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator

from src.common.custom_types import CareUnitName, MasterInstance, PatientName, ServiceName, Window
from src.plotters.care_unit_colors import get_care_unit_colors, get_care_unit_legend_handles


@dataclass(frozen=True)
class PatientWindowBlock:
    patient_name: PatientName
    service_name: ServiceName
    care_unit_name: CareUnitName
    window: Window


@dataclass(frozen=True)
class PositionedPatientWindowBlock:
    block: PatientWindowBlock
    y: float


def get_instance_day_names(instance: MasterInstance) -> list[int]:
    day_names = sorted(instance.days.keys())
    if len(day_names) == 0:
        raise ValueError('The master instance does not define any day.')
    return day_names


def get_instance_care_unit_names(instance: MasterInstance) -> list[str]:
    care_unit_names = sorted({
        care_unit_name
        for day in instance.days.values()
        for care_unit_name in day.care_units.keys()
    })
    if len(care_unit_names) == 0:
        raise ValueError('The master instance does not define any care unit.')
    return care_unit_names


def format_instance_label(instance_name: str) -> str:
    if instance_name.startswith('inst_'):
        return instance_name.removeprefix('inst_')
    return instance_name


def get_per_day_patient_window_overlaps(instance: MasterInstance) -> tuple[list[int], list[list[int]]]:
    day_names = get_instance_day_names(instance)

    if len(instance.patients) == 0:
        raise ValueError('The master instance does not define any patient.')

    per_day_patient_overlaps: list[list[int]] = []
    for day_name in day_names:
        patient_overlaps: list[int] = []
        for patient in instance.patients.values():
            patient_overlaps.append(sum(
                1
                for windows in patient.requests.values()
                for window in windows
                if window.contains(day_name)
            ))
        per_day_patient_overlaps.append(patient_overlaps)

    return day_names, per_day_patient_overlaps


def get_per_day_patient_weighted_window_overlaps(instance: MasterInstance) -> tuple[list[int], list[list[float]]]:
    day_names = get_instance_day_names(instance)

    if len(instance.patients) == 0:
        raise ValueError('The master instance does not define any patient.')

    per_day_patient_overlaps: list[list[float]] = []
    for day_name in day_names:
        patient_overlaps: list[float] = []
        for patient in instance.patients.values():
            patient_overlaps.append(sum(
                1.0 / (window.end - window.start + 1)
                for windows in patient.requests.values()
                for window in windows
                if window.contains(day_name)
            ))
        per_day_patient_overlaps.append(patient_overlaps)

    return day_names, per_day_patient_overlaps


def _sorted_patient_blocks(instance: MasterInstance) -> dict[PatientName, list[PatientWindowBlock]]:
    patient_blocks: dict[PatientName, list[PatientWindowBlock]] = {}

    for patient_name, patient in sorted(instance.patients.items()):
        patient_blocks[patient_name] = sorted(
            (
                PatientWindowBlock(
                    patient_name=patient_name,
                    service_name=service_name,
                    care_unit_name=instance.services[service_name].care_unit_name,
                    window=window,
                )
                for service_name, windows in patient.requests.items()
                for window in windows
            ),
            key=lambda block: (
                block.window.start,
                block.window.end,
                block.care_unit_name,
                block.service_name,
            ),
        )

    return patient_blocks


def _assign_rows(patient_blocks: list[PatientWindowBlock]) -> tuple[list[tuple[PatientWindowBlock, int]], int]:
    row_end_days: list[int] = []
    assigned_blocks: list[tuple[PatientWindowBlock, int]] = []

    for block in patient_blocks:
        row_index = next(
            (index for index, end_day in enumerate(row_end_days) if block.window.start > end_day),
            None,
        )
        if row_index is None:
            row_end_days.append(block.window.end)
            row_index = len(row_end_days) - 1
        else:
            row_end_days[row_index] = block.window.end

        assigned_blocks.append((block, row_index))

    return assigned_blocks, max(len(row_end_days), 1)


def get_patient_window_layout_parameters(total_row_count: int) -> tuple[float, float, float]:
    if total_row_count <= 25:
        return 0.62, 0.12, 1.20
    if total_row_count <= 60:
        return 0.52, 0.10, 1.00
    if total_row_count <= 120:
        return 0.42, 0.08, 0.82
    return 0.30, 0.05, 0.62


def get_patient_window_plot_ymax(instance: MasterInstance) -> float:
    patient_blocks = _sorted_patient_blocks(instance)
    total_row_count = 0
    for blocks in patient_blocks.values():
        _, row_count = _assign_rows(blocks)
        total_row_count += row_count

    row_height, row_gap, patient_gap = get_patient_window_layout_parameters(total_row_count)
    y_cursor = 0.0

    for blocks in patient_blocks.values():
        _, row_count = _assign_rows(blocks)
        patient_height = row_count * row_height + max(row_count - 1, 0) * row_gap
        y_cursor += patient_height + patient_gap

    if y_cursor > 0:
        y_cursor -= patient_gap

    return y_cursor + 0.2


def get_max_patient_window_overlap(instance: MasterInstance) -> int:
    _, per_day_patient_overlaps = get_per_day_patient_window_overlaps(instance)
    return max((max(day_overlaps) for day_overlaps in per_day_patient_overlaps), default=0)


def get_max_patient_weighted_window_overlap(instance: MasterInstance) -> float:
    _, per_day_patient_overlaps = get_per_day_patient_weighted_window_overlaps(instance)
    return max((max(day_overlaps) for day_overlaps in per_day_patient_overlaps), default=0.0)


def plot_master_instance_windows(
        instance: MasterInstance,
        save_path: Path,
        title: str,
) -> None:
    patient_blocks = _sorted_patient_blocks(instance)
    care_unit_colors = get_care_unit_colors(
        block.care_unit_name
        for blocks in patient_blocks.values()
        for block in blocks
    )

    total_row_count = 0

    for blocks in patient_blocks.values():
        _, row_count = _assign_rows(blocks)
        total_row_count += row_count

    row_height, row_gap, patient_gap = get_patient_window_layout_parameters(total_row_count)

    y_cursor = 0.0
    positioned_blocks: list[PositionedPatientWindowBlock] = []
    patient_label_positions: list[float] = []
    patient_labels: list[str] = []
    separator_positions: list[float] = []

    for patient_name, blocks in patient_blocks.items():
        assigned_blocks, row_count = _assign_rows(blocks)
        patient_height = row_count * row_height + max(row_count - 1, 0) * row_gap
        patient_bottom = y_cursor

        for block, row_index in assigned_blocks:
            row_offset = row_index * (row_height + row_gap)
            positioned_blocks.append(PositionedPatientWindowBlock(
                block=block,
                y=patient_bottom + row_offset,
            ))

        patient_label_positions.append(patient_bottom + patient_height * 0.5)
        patient_labels.append(patient_name)
        y_cursor += patient_height
        separator_positions.append(y_cursor + patient_gap * 0.5)
        y_cursor += patient_gap

    if y_cursor > 0:
        y_cursor -= patient_gap
        separator_positions = separator_positions[:-1]

    day_names = get_instance_day_names(instance)
    first_day = day_names[0]
    last_day = day_names[-1]
    day_boundaries = list(range(first_day, last_day + 2))
    day_centers = [day_name + 0.5 for day_name in day_names]

    if total_row_count <= 25:
        figure_height = max(5.5, 0.28 * total_row_count + 0.30 * max(len(patient_labels), 1))
    elif total_row_count <= 60:
        figure_height = max(5.0, 0.18 * total_row_count + 0.18 * max(len(patient_labels), 1))
    elif total_row_count <= 120:
        figure_height = max(4.8, 0.10 * total_row_count + 0.11 * max(len(patient_labels), 1))
    else:
        figure_height = max(4.5, 0.035 * total_row_count + 0.08 * max(len(patient_labels), 1))
    fig, ax = plt.subplots(figsize=(16, figure_height))

    for positioned_block in positioned_blocks:
        window = positioned_block.block.window
        ax.add_patch(Rectangle(
            (window.start, positioned_block.y),
            window.end - window.start + 1,
            row_height,
            linewidth=0.8,
            edgecolor='black',
            facecolor=care_unit_colors[positioned_block.block.care_unit_name],
        ))

    for boundary in day_boundaries:
        ax.axvline(boundary, color='lightgrey', linewidth=0.8, zorder=-2)

    for separator in separator_positions:
        ax.axhline(separator, color='lightgrey', linewidth=0.8, zorder=-2)

    ax.set_xlim(first_day, last_day + 1)
    ax.set_ylim(-0.2, y_cursor + 0.2)
    ax.invert_yaxis()

    ax.set_xticks(day_boundaries)
    ax.set_xticklabels([])
    ax.set_xticks(day_centers, [str(day_name) for day_name in day_names], minor=True)
    ax.tick_params(axis='x', which='major', length=6, width=0.8)
    ax.tick_params(axis='x', which='minor', length=0, pad=10)

    ax.set_yticks(patient_label_positions, patient_labels)
    ax.tick_params(axis='y', length=0, pad=8)

    ax.set_xlabel('Days')
    ax.set_ylabel('Patients')
    ax.set_title(title)

    legend_handles = get_care_unit_legend_handles(care_unit_colors)
    if len(legend_handles) > 0:
        ax.legend(
            handles=legend_handles,
            title='Care units',
            loc='upper left',
            bbox_to_anchor=(1.01, 1.0),
            borderaxespad=0.0,
        )

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_average_window_overlap_by_day(
        instance: MasterInstance,
        save_path: Path,
        title: str,
        ymax: float | None = None,
) -> None:
    day_names, per_day_patient_overlaps = get_per_day_patient_window_overlaps(instance)
    _plot_daywise_patient_boxplot(
        day_names,
        per_day_patient_overlaps,
        save_path,
        title,
        ylabel='Active windows per patient',
        box_facecolor='tab:blue',
        ymax=ymax,
    )


def plot_weighted_window_overlap_by_day(
        instance: MasterInstance,
        save_path: Path,
        title: str,
        ymax: float | None = None,
) -> None:
    day_names, per_day_patient_overlaps = get_per_day_patient_weighted_window_overlaps(instance)
    _plot_daywise_patient_boxplot(
        day_names,
        per_day_patient_overlaps,
        save_path,
        title,
        ylabel='Weighted active windows per patient',
        box_facecolor='tab:purple',
        ymax=ymax,
    )


def _plot_daywise_patient_boxplot(
        day_names: list[int],
        per_day_patient_overlaps: list[list[int]] | list[list[float]],
        save_path: Path,
        title: str,
        ylabel: str,
        box_facecolor: str,
        ymax: float | None,
) -> None:
    x_boundaries = list(range(day_names[0], day_names[-1] + 2))
    x_centers = [day_name + 0.5 for day_name in day_names]

    fig, ax = plt.subplots(figsize=(16, 4.8))

    boxplot = ax.boxplot(
        per_day_patient_overlaps,
        positions=x_centers,
        widths=0.72,
        patch_artist=True,
        manage_ticks=False,
        showfliers=False,
    )
    for box in boxplot['boxes']:
        box.set(facecolor=box_facecolor, edgecolor='black', linewidth=0.8, alpha=0.65)
    for whisker in boxplot['whiskers']:
        whisker.set(color='black', linewidth=0.8)
    for cap in boxplot['caps']:
        cap.set(color='black', linewidth=0.8)
    for median in boxplot['medians']:
        median.set(color='darkred', linewidth=1.2)

    for boundary in x_boundaries:
        ax.axvline(boundary, color='lightgrey', linewidth=0.8, zorder=-2)

    ax.set_xlim(day_names[0], day_names[-1] + 1)
    ax.set_xticks(x_boundaries)
    ax.set_xticklabels([])
    ax.set_xticks(x_centers, [str(day_name) for day_name in day_names], minor=True)
    ax.tick_params(axis='x', which='major', length=6, width=0.8)
    ax.tick_params(axis='x', which='minor', length=0, pad=10)
    if ymax is not None:
        ax.set_ylim(0.0, ymax)

    ax.set_xlabel('Days')
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color='lightgrey', linewidth=0.8)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def get_spread_capacity_heatmap_data(
        instance: MasterInstance,
) -> tuple[list[int], list[str], np.ndarray]:
    day_names = get_instance_day_names(instance)
    care_unit_names = get_instance_care_unit_names(instance)

    heatmap = np.full((len(care_unit_names), len(day_names)), np.nan)

    for care_unit_index, care_unit_name in enumerate(care_unit_names):
        for day_index, day_name in enumerate(day_names):
            spread = 0.0
            for patient in instance.patients.values():
                for service_name, windows in patient.requests.items():
                    service = instance.services[service_name]
                    if service.care_unit_name != care_unit_name:
                        continue

                    for window in windows:
                        if window.contains(day_name):
                            window_size = window.end - window.start + 1
                            spread += service.duration / window_size

            capacity = float(instance.days[day_name].duration(care_unit_name))
            if capacity > 0.0:
                heatmap[care_unit_index, day_index] = spread / capacity

    return day_names, care_unit_names, heatmap


def get_spread_capacity_heatmap_max(instance: MasterInstance) -> float:
    _, _, heatmap = get_spread_capacity_heatmap_data(instance)
    finite_values = heatmap[np.isfinite(heatmap)]
    if finite_values.size == 0:
        return 0.0
    return float(finite_values.max())


def get_daily_average_spread_capacity(instance: MasterInstance) -> list[float]:
    _, _, heatmap = get_spread_capacity_heatmap_data(instance)
    daily_means: list[float] = []

    for day_index in range(heatmap.shape[1]):
        day_values = heatmap[:, day_index]
        finite_values = day_values[np.isfinite(day_values)]
        if finite_values.size == 0:
            daily_means.append(0.0)
        else:
            daily_means.append(float(finite_values.mean()))

    return daily_means


def plot_spread_capacity_heatmap(
        instance: MasterInstance,
        save_path: Path,
        title: str,
        vmax: float | None = None,
) -> None:
    day_names, care_unit_names, heatmap = get_spread_capacity_heatmap_data(instance)

    fig_width = max(10.0, 0.28 * len(day_names) + 2.5)
    fig_height = max(4.0, 0.55 * len(care_unit_names) + 1.8)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    cmap = plt.get_cmap('YlOrRd').copy()
    cmap.set_bad(color='#d9d9d9')

    image = ax.imshow(
        heatmap,
        aspect='auto',
        interpolation='nearest',
        cmap=cmap,
        origin='upper',
        vmin=0.0,
        vmax=vmax,
    )

    colorbar = fig.colorbar(image, ax=ax, shrink=0.92)
    colorbar.set_label('Spread / capacity')

    ax.set_xticks(range(len(day_names)), [str(day_name) for day_name in day_names])
    ax.set_yticks(range(len(care_unit_names)), care_unit_names)
    ax.set_xlabel('Days')
    ax.set_ylabel('Care units')
    ax.set_title(title)

    ax.set_xticks(np.arange(-0.5, len(day_names), 1.0), minor=True)
    ax.set_yticks(np.arange(-0.5, len(care_unit_names), 1.0), minor=True)
    ax.grid(which='minor', color='white', linestyle='-', linewidth=0.8)
    ax.tick_params(which='minor', bottom=False, left=False)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def plot_grouped_instance_median_window_overlap_distribution(
        grouped_instance_overlap_medians: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
) -> None:
    _plot_grouped_instance_distribution(
        grouped_instance_overlap_medians,
        save_path,
        title,
        ylabel='Daily median active windows per patient',
        box_facecolor='tab:orange',
        integer_y_ticks=True,
    )


def plot_grouped_instance_average_spread_capacity_distribution(
        grouped_instance_daily_spread_capacity_means: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
) -> None:
    _plot_grouped_instance_distribution(
        grouped_instance_daily_spread_capacity_means,
        save_path,
        title,
        ylabel='Daily mean spread / capacity across care units',
        box_facecolor='tab:green',
        integer_y_ticks=False,
    )


def plot_grouped_instance_weighted_median_window_overlap_distribution(
        grouped_instance_weighted_overlap_medians: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
) -> None:
    _plot_grouped_instance_distribution(
        grouped_instance_weighted_overlap_medians,
        save_path,
        title,
        ylabel='Daily weighted median active windows per patient',
        box_facecolor='tab:purple',
        integer_y_ticks=False,
    )


def _plot_grouped_instance_distribution(
        grouped_instance_values: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
        ylabel: str,
        box_facecolor: str,
        integer_y_ticks: bool,
) -> None:
    if len(grouped_instance_values) == 0:
        raise ValueError('No grouped instance data available to plot.')

    positions: list[float] = []
    labels: list[str] = []
    data: list[list[float]] = []
    group_centers: list[tuple[str, float]] = []
    group_boundaries: list[float] = []

    total_instance_count = sum(len(instances) for instances in grouped_instance_values.values())
    if total_instance_count <= 6:
        instance_step = 0.41
        group_gap = 0.48
        box_width = 0.26
        tick_label_size = 10
    elif total_instance_count <= 14:
        instance_step = 0.34
        group_gap = 0.39
        box_width = 0.22
        tick_label_size = 9
    else:
        instance_step = 0.28
        group_gap = 0.33
        box_width = 0.18
        tick_label_size = 8

    x_position = 1.0
    for group_name, instances in grouped_instance_values.items():
        start_position = x_position
        ordered_instances = sorted(instances.items())
        for instance_name, values in ordered_instances:
            positions.append(x_position)
            labels.append(format_instance_label(instance_name))
            data.append(values)
            x_position += instance_step

        end_position = x_position - instance_step
        group_centers.append((group_name, (start_position + end_position) * 0.5))
        group_boundaries.append(end_position + group_gap * 0.5)
        x_position += group_gap

    used_width = positions[-1] - positions[0] + box_width + group_gap if len(positions) > 1 else 2.5
    fig_width = max(8.5, min(16.0, used_width * 1.55))
    fig, ax = plt.subplots(figsize=(fig_width, 5.6))

    boxplot = ax.boxplot(
        data,
        positions=positions,
        widths=box_width,
        patch_artist=True,
        showfliers=False,
    )
    for box in boxplot['boxes']:
        box.set(facecolor=box_facecolor, edgecolor='black', linewidth=0.8, alpha=0.7)
    for whisker in boxplot['whiskers']:
        whisker.set(color='black', linewidth=0.8)
    for cap in boxplot['caps']:
        cap.set(color='black', linewidth=0.8)
    for median_line in boxplot['medians']:
        median_line.set(color='darkred', linewidth=1.2)

    ax.set_xticks(positions, labels)
    ax.tick_params(axis='x', labelsize=tick_label_size)
    ax.set_xlabel('Instances')
    ax.set_ylabel(ylabel)
    ax.set_title(title, pad=24)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color='lightgrey', linewidth=0.8)
    if integer_y_ticks:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))

    for separator in group_boundaries[:-1]:
        ax.axvline(separator, color='lightgrey', linewidth=1.0, zorder=-2)

    top_axis = ax.secondary_xaxis('top')
    top_axis.set_xticks([center for _, center in group_centers], [group_name for group_name, _ in group_centers])
    top_axis.tick_params(axis='x', which='both', length=0, pad=4, labelsize=max(9, tick_label_size))
    top_axis.spines['top'].set_visible(False)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def get_daily_median_window_overlaps(instance: MasterInstance) -> list[float]:
    _, per_day_patient_overlaps = get_per_day_patient_window_overlaps(instance)
    return [float(median(day_overlaps)) for day_overlaps in per_day_patient_overlaps]


def get_daily_weighted_median_window_overlaps(instance: MasterInstance) -> list[float]:
    _, per_day_patient_overlaps = get_per_day_patient_weighted_window_overlaps(instance)
    return [float(median(day_overlaps)) for day_overlaps in per_day_patient_overlaps]


def get_request_count_per_patient(instance: MasterInstance) -> list[int]:
    request_counts: list[int] = []
    for patient in instance.patients.values():
        request_counts.append(sum(len(windows) for windows in patient.requests.values()))
    return request_counts


def get_duration_weighted_request_count_per_patient(instance: MasterInstance) -> list[int]:
    weighted_request_counts: list[int] = []
    for patient in instance.patients.values():
        weighted_request_counts.append(sum(
            len(windows) * instance.services[service_name].duration
            for service_name, windows in patient.requests.items()
        ))
    return weighted_request_counts


def plot_grouped_instance_request_count_distribution(
        grouped_instance_request_counts: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
) -> None:
    _plot_grouped_instance_distribution(
        grouped_instance_request_counts,
        save_path,
        title,
        ylabel='Requests per patient',
        box_facecolor='tab:brown',
        integer_y_ticks=True,
    )


def plot_grouped_instance_duration_weighted_request_count_distribution(
        grouped_instance_weighted_request_counts: dict[str, dict[str, list[float]]],
        save_path: Path,
        title: str,
) -> None:
    _plot_grouped_instance_distribution(
        grouped_instance_weighted_request_counts,
        save_path,
        title,
        ylabel='Duration-weighted requests per patient',
        box_facecolor='tab:red',
        integer_y_ticks=True,
    )
