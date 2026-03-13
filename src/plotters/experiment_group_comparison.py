from collections import defaultdict
from pathlib import Path
import json
import re

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator

from src.common.analysis_constants import FINAL_GAP_OPTIMAL_TOLERANCE_PCT
from src.common.tools import is_combination_to_do
from src.common.file_load_and_dump import decode_master_result


CORE_NUMBER_COLUMNS = [
    'expanded_core_number',
    'pruned_core_number',
    'reduced_core_number',
    'basic_core_number',
    'generalist_core_number',
    'preemptive_core_number',
]

CORE_AVERAGE_SIZE_COLUMNS = [
    'expanded_average_core_size',
    'pruned_average_core_size',
    'reduced_average_core_size',
    'basic_average_core_size',
    'generalist_average_core_size',
    'preemptive_average_core_size',
]

# Tolleranza per classificare il gap del solver master come non ottimo.
MASTER_MIP_GAP_TOLERANCE_PCT = 1e-6

# Ordine di default dei test (crescente complessita').
DEFAULT_TEST_COMPLEXITY_ORDER = [
    'generalist',
    'basic',
    'reduced',
    'pruned',
    'preemptive',
    'expanded',
    'monolithic',
]

# Alias etichette test (settabile da codice).
# Esempio:
# TEST_NAME_ALIASES = {'generalist': 'GEN', 'basic': 'BAS'}
TEST_NAME_ALIASES: dict[str, str] = {}

GROUP_NAME_PATTERN = re.compile(
    r'(?P<patients>\d+)pat_(?P<care_units>\d+)cu(?:_(?P<operators>\d+)op)?',
    re.IGNORECASE)

BUBBLE_COLOR = '#4C78A8'


def _figure_width_for_categories(
        category_count: int,
        base_width: float = 11.0,
        per_category: float = 0.95) -> float:
    return max(base_width, float(category_count) * per_category)


def _bar_width_px(ax: plt.Axes, bar) -> float:
    try:
        x0 = float(bar.get_x())
        x1 = float(bar.get_x() + bar.get_width())
        p0 = ax.transData.transform((x0, 0.0))[0]
        p1 = ax.transData.transform((x1, 0.0))[0]
        return float(abs(p1 - p0))
    except Exception:
        return 0.0


def _filter_rows(df: pd.DataFrame, config) -> pd.DataFrame:
    if df.empty:
        return df
    required = {'config', 'group', 'instance'}
    if not required.issubset(set(df.columns)):
        return df.iloc[0:0].copy()

    mask = df.apply(
        lambda row: is_combination_to_do(
            str(row['config']),
            str(row['group']),
            str(row['instance']),
            config),
        axis=1)
    return df[mask].copy()


def _pick_core_number_per_iteration(master_rows: pd.DataFrame) -> pd.Series:
    available_columns = [name for name in CORE_NUMBER_COLUMNS if name in master_rows.columns]
    if len(available_columns) == 0:
        return pd.Series(dtype=float)
    return master_rows[available_columns].bfill(axis=1).iloc[:, 0]


def _pick_core_average_size_per_iteration(master_rows: pd.DataFrame) -> pd.Series:
    available_columns = [name for name in CORE_AVERAGE_SIZE_COLUMNS if name in master_rows.columns]
    if len(available_columns) == 0:
        return pd.Series(dtype=float)
    return master_rows[available_columns].bfill(axis=1).iloc[:, 0]


def _resolve_config_order(
        present_config_names: list[str],
        config) -> list[str]:
    present_set = set(present_config_names)

    configured_order: list[str] = []
    raw_order = config.get('experiment_group_comparison_config_order') if isinstance(config, dict) else None
    if isinstance(raw_order, list):
        configured_order = [str(name) for name in raw_order]
    elif isinstance(config, dict):
        # Fallback: se configs_to_do e' esplicito e non contiene all.
        raw_configs_to_do = config.get('configs_to_do')
        if isinstance(raw_configs_to_do, list):
            lowered = [str(name).strip().lower() for name in raw_configs_to_do]
            if 'all' not in lowered:
                configured_order = [str(name) for name in raw_configs_to_do]

    base_order = configured_order if len(configured_order) > 0 else DEFAULT_TEST_COMPLEXITY_ORDER
    rank = {name: index for index, name in enumerate(base_order)}
    return sorted(
        present_set,
        key=lambda name: (rank.get(name, len(rank)), name))


def _resolve_config_aliases(config) -> dict[str, str]:
    aliases = dict(TEST_NAME_ALIASES)
    raw_aliases = config.get('experiment_group_comparison_config_aliases') if isinstance(config, dict) else None
    if isinstance(raw_aliases, dict):
        for key, value in raw_aliases.items():
            aliases[str(key)] = str(value)
    return aliases


def _group_complexity_key(group_name: str) -> tuple[int, int, int, int, str]:
    match = GROUP_NAME_PATTERN.search(group_name)
    if match is None:
        return (1, 10**9, 10**9, 10**9, group_name)

    patients = int(match.group('patients'))
    care_units = int(match.group('care_units'))
    operators = int(match.group('operators')) if match.group('operators') is not None else 10**9
    return (0, patients, care_units, operators, group_name)


def _resolve_group_rank_map(config) -> dict[str, int]:
    group_rank_map: dict[str, int] = {}
    raw_order = config.get('experiment_group_comparison_group_order') if isinstance(config, dict) else None
    if isinstance(raw_order, list):
        for index, group_name in enumerate(raw_order):
            group_rank_map[str(group_name)] = index
    return group_rank_map


def _pair_group_sort_key(pair: tuple[str, str], group_rank_map: dict[str, int]) -> tuple[int, int, int, int, str]:
    return (
        group_rank_map.get(pair[1], len(group_rank_map)),
        *_group_complexity_key(pair[1]))


def _grouped_pairs_layout(
        pairs: list[tuple[str, str]],
        ordered_config_names: list[str],
        group_rank_map: dict[str, int]):
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for pair in pairs:
        grouped[pair[0]].append(pair)

    config_names = [name for name in ordered_config_names if name in grouped]
    for name in sorted(grouped.keys()):
        if name not in config_names:
            config_names.append(name)

    sorted_pairs: list[tuple[str, str]] = []
    for config_name in config_names:
        sorted_pairs.extend(sorted(
            grouped[config_name],
            key=lambda pair: _pair_group_sort_key(pair, group_rank_map)))

    x_positions: list[float] = []
    x_labels: list[str] = []
    config_spans: dict[str, tuple[float, float]] = {}
    separators: list[float] = []

    current_x = 1.0
    pair_step = 0.58
    config_gap = 0.65

    position_by_pair: dict[tuple[str, str], float] = {}
    for config_index, config_name in enumerate(config_names):
        cfg_pairs = sorted(
            grouped[config_name],
            key=lambda pair: _pair_group_sort_key(pair, group_rank_map))
        start_x = current_x
        for pair in cfg_pairs:
            x_positions.append(current_x)
            x_labels.append(pair[1])
            position_by_pair[pair] = current_x
            current_x += pair_step
        end_x = current_x - pair_step
        config_spans[config_name] = (start_x, end_x)
        if config_index < len(config_names) - 1:
            separators.append(current_x - (pair_step * 0.5) + (config_gap * 0.5))
        current_x += config_gap

    return sorted_pairs, position_by_pair, x_positions, x_labels, config_spans, separators


def _decorate_grouped_axis(
        ax: plt.Axes,
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str] | None = None):
    _ = config_aliases
    ax.set_xticks(x_positions)
    ax.set_xticklabels(x_labels, rotation=90, ha='center')
    for separator_x in separators:
        ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)

def _finalize_grouped_figure(
        fig,
        header_ax: plt.Axes,
        config_spans: dict[str, tuple[float, float]],
        config_aliases: dict[str, str] | None = None,
        bottom: float = 0.0,
        top: float = 0.90):
    aliases = config_aliases or {}
    fig.tight_layout(rect=(0.0, bottom, 1.0, top))
    fig.canvas.draw()

    y_header = min(0.985, float(header_ax.get_position().y1) + 0.055)
    for config_name, (start_x, end_x) in config_spans.items():
        center = (start_x + end_x) * 0.5
        label = aliases.get(config_name, config_name)
        x_disp, _ = header_ax.transData.transform((center, 0.0))
        x_fig, _ = fig.transFigure.inverted().transform((x_disp, 0.0))
        fig.text(
            float(x_fig),
            y_header,
            label,
            ha='center',
            va='bottom',
            fontsize=9,
            fontweight='bold')


def _instance_summary(
        master_df: pd.DataFrame,
        sub_df: pd.DataFrame,
        instance_df: pd.DataFrame | None = None) -> pd.DataFrame:
    sub_totals = {}
    if not sub_df.empty and 'time' in sub_df.columns:
        sub_totals = (
            sub_df
            .groupby(['config', 'group', 'instance'])['time']
            .sum()
            .to_dict()
        )

    instance_status_by_key: dict[tuple[str, str, str], dict[str, object]] = {}
    if instance_df is not None and len(instance_df) > 0:
        normalized = instance_df.copy()
        for required_column in ['config', 'group', 'instance']:
            if required_column in normalized.columns:
                normalized[required_column] = normalized[required_column].astype(str)
        for _, row in normalized.iterrows():
            key = (str(row['config']), str(row['group']), str(row['instance']))
            instance_status_by_key[key] = row.to_dict()

    rows = []
    for key, master_rows in master_df.groupby(['config', 'group', 'instance']):
        config_name, group_name, instance_name = key
        master_rows = master_rows.sort_values('iteration')

        iteration_count = int(master_rows['iteration'].nunique()) if 'iteration' in master_rows.columns else 0

        core_counts = _pick_core_number_per_iteration(master_rows).dropna()
        avg_cores_per_iteration = float(core_counts.mean()) if len(core_counts) > 0 else np.nan
        core_average_sizes = _pick_core_average_size_per_iteration(master_rows).dropna()
        avg_core_size_per_iteration = float(core_average_sizes.mean()) if len(core_average_sizes) > 0 else np.nan

        total_master_time = float(master_rows['master_time'].dropna().sum()) if 'master_time' in master_rows.columns else np.nan
        total_subproblem_time = float(sub_totals.get(key, np.nan))
        if pd.notna(total_master_time) and pd.notna(total_subproblem_time):
            total_solving_time = total_master_time + total_subproblem_time
        elif pd.notna(total_master_time):
            total_solving_time = total_master_time
        elif pd.notna(total_subproblem_time):
            total_solving_time = total_subproblem_time
        else:
            total_solving_time = np.nan

        final_gap_pct = np.nan
        master_status = ''
        timeout_feasible_nonoptimal_iteration_count = 0
        mean_master_gap_over_iterations = np.nan
        final_objective_value = np.nan
        scheduled_duration_over_capacity_ratio = np.nan
        scheduled_number_over_total_ratio = np.nan

        status_series = pd.Series(dtype=str)
        gap_series = pd.Series(dtype=float)
        objective_series = pd.Series(dtype=float)
        if 'master_status' in master_rows.columns:
            status_series = master_rows['master_status'].astype(str).str.strip().str.lower()
        if 'master_gap' in master_rows.columns:
            gap_series = pd.to_numeric(master_rows['master_gap'], errors='coerce')
        if 'master_objective_value' in master_rows.columns:
            objective_series = pd.to_numeric(master_rows['master_objective_value'], errors='coerce')

        if len(gap_series) > 0:
            valid_gaps = gap_series.dropna()
            if len(valid_gaps) > 0:
                mean_master_gap_over_iterations = float(valid_gaps.mean())

        if len(status_series) > 0 and len(gap_series) > 0:
            timeout_mask = status_series == 'time_limit'
            non_optimal_mask = gap_series > MASTER_MIP_GAP_TOLERANCE_PCT
            if len(objective_series) > 0:
                feasible_mask = objective_series.notna()
            else:
                feasible_mask = gap_series.notna()
            timeout_feasible_nonoptimal_iteration_count = int(
                (timeout_mask & feasible_mask & non_optimal_mask).sum())

        if len(master_rows) > 0 and 'iteration' in master_rows.columns:
            final_iteration = master_rows['iteration'].max()
            final_row = master_rows[master_rows['iteration'] == final_iteration].iloc[-1]
            upper_bound = final_row.get('master_upper_bound', np.nan)
            feasible_value = final_row.get('final_objective_value', np.nan)
            final_objective_value = pd.to_numeric(final_row.get('final_objective_value', np.nan), errors='coerce')
            master_status = str(final_row.get('master_status', '')).strip().lower()
            final_gap_pct = pd.to_numeric(final_row.get('lbbd_final_gap_pct', np.nan), errors='coerce')
            if pd.isna(final_gap_pct) and pd.notna(upper_bound) and pd.notna(feasible_value) and abs(float(feasible_value)) > 1e-9:
                final_gap_pct = max(0.0, (float(upper_bound) - float(feasible_value)) / abs(float(feasible_value)) * 100.0)

            total_scheduled_duration = pd.to_numeric(
                final_row.get('final_total_scheduled_request_duration', np.nan),
                errors='coerce')
            total_remaining_duration = pd.to_numeric(
                final_row.get('final_total_time_slots_remaining', np.nan),
                errors='coerce')
            if pd.notna(total_scheduled_duration) and pd.notna(total_remaining_duration):
                total_capacity_duration = float(total_scheduled_duration) + float(total_remaining_duration)
                if total_capacity_duration > 1e-9:
                    scheduled_duration_over_capacity_ratio = float(total_scheduled_duration) / total_capacity_duration

            total_scheduled_number = pd.to_numeric(
                final_row.get('final_total_scheduled_request_number', np.nan),
                errors='coerce')
            total_rejected_number = pd.to_numeric(
                final_row.get('final_total_rejected_request_number', np.nan),
                errors='coerce')
            if pd.notna(total_scheduled_number) and pd.notna(total_rejected_number):
                total_number = float(total_scheduled_number) + float(total_rejected_number)
                if total_number > 1e-9:
                    scheduled_number_over_total_ratio = float(total_scheduled_number) / total_number

        # Per i plot aggregati, "istanza ottima" significa gap finale
        # Master-Subproblem chiuso (entro tolleranza), non solo master MIP ottimo.
        status_label = ''
        status_reason = ''
        if key in instance_status_by_key:
            status_label = str(instance_status_by_key[key].get('status', '')).strip().lower()
            status_reason = str(instance_status_by_key[key].get('status_reason', '')).strip().lower()

        if status_label != '':
            is_optimal = status_label == 'optimal'
        else:
            is_optimal = bool(
                pd.notna(final_gap_pct) and
                final_gap_pct <= FINAL_GAP_OPTIMAL_TOLERANCE_PCT)

        rows.append({
            'config': config_name,
            'group': group_name,
            'instance': instance_name,
            'iteration_count': iteration_count,
            'avg_cores_per_iteration': avg_cores_per_iteration,
            'avg_core_size_per_iteration': avg_core_size_per_iteration,
            'total_master_time': total_master_time,
            'total_subproblem_time': total_subproblem_time,
            'total_solving_time': total_solving_time,
            'final_gap_pct': final_gap_pct,
            'final_objective_value': final_objective_value,
            'is_optimal': is_optimal,
            'status': status_label,
            'status_reason': status_reason,
            'timeout_feasible_nonoptimal_iteration_count': timeout_feasible_nonoptimal_iteration_count,
            'mean_master_gap_over_iterations': mean_master_gap_over_iterations,
            'scheduled_duration_over_capacity_ratio': scheduled_duration_over_capacity_ratio,
            'scheduled_number_over_total_ratio': scheduled_number_over_total_ratio,
        })

    return pd.DataFrame(rows)


def _compute_master_request_grouping_metrics_for_instance(
        result_dir: Path,
        iteration_values: list[int]) -> tuple[float, float]:
    multi_request_patients_per_iteration: list[float] = []
    patient_day_group_sizes_all_iterations: list[float] = []

    for iteration in iteration_values:
        master_result_path = result_dir.joinpath(f'iter_{iteration}', 'master_result.json')
        if not master_result_path.exists():
            continue

        with open(master_result_path, 'r') as file:
            master_result = decode_master_result(json.load(file))

        multi_request_patients_current_iteration = 0
        for requests in master_result.scheduled.values():
            patient_request_count: dict[str, int] = {}
            for request in requests:
                patient_name = request.patient_name
                patient_request_count[patient_name] = patient_request_count.get(patient_name, 0) + 1

            multi_request_patients_current_iteration += sum(
                1 for request_count in patient_request_count.values() if request_count > 1)
            patient_day_group_sizes_all_iterations.extend(patient_request_count.values())

        multi_request_patients_per_iteration.append(float(multi_request_patients_current_iteration))

    mean_multi_request_patients = (
        float(np.mean(multi_request_patients_per_iteration))
        if len(multi_request_patients_per_iteration) > 0 else np.nan)
    mean_patient_day_group_size = (
        float(np.mean(patient_day_group_sizes_all_iterations))
        if len(patient_day_group_sizes_all_iterations) > 0 else np.nan)

    return mean_multi_request_patients, mean_patient_day_group_size


def _build_master_request_grouping_metrics(
        master_df: pd.DataFrame,
        results_path: Path) -> pd.DataFrame:
    rows: list[dict[str, str | float]] = []

    for (config_name, group_name, instance_name), master_rows in master_df.groupby(['config', 'group', 'instance']):
        iteration_values = sorted({
            int(iteration)
            for iteration in pd.to_numeric(master_rows['iteration'], errors='coerce').dropna().tolist()
        })
        if len(iteration_values) == 0:
            continue

        result_dir = results_path.joinpath(f'{config_name}__{group_name}__{instance_name}')
        if not result_dir.exists():
            continue

        mean_multi_request_patients, mean_patient_day_group_size = _compute_master_request_grouping_metrics_for_instance(
            result_dir,
            iteration_values)

        rows.append({
            'config': str(config_name),
            'group': str(group_name),
            'instance': str(instance_name),
            'mean_multi_request_patients_per_iteration': mean_multi_request_patients,
            'mean_patient_day_group_size': mean_patient_day_group_size,
        })

    return pd.DataFrame(rows)


def _save_boxplot(
        data_by_pair: dict[tuple[str, str], list[float]],
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        ylabel: str,
        title: str,
        save_path: Path):
    fig, ax = plt.subplots(figsize=(max(10.0, len(sorted_pairs) * 0.58), 5.0))

    data = []
    positions = []
    for pair in sorted_pairs:
        values = [value for value in data_by_pair.get(pair, []) if pd.notna(value)]
        if len(values) == 0:
            continue
        data.append(values)
        positions.append(position_by_pair[pair])

    if len(data) > 0:
        ax.boxplot(data, positions=positions, widths=0.40, patch_artist=True, showfliers=True)

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def _scale_bubble_sizes(
        raw_values: list[float],
        min_size: float = 120.0,
        max_size: float = 1500.0) -> list[float]:
    values = np.array([
        float(value) if pd.notna(value) and float(value) > 0 else 0.0
        for value in raw_values
    ], dtype=float)

    if values.size == 0:
        return []

    transformed = np.log1p(values)
    t_min = float(transformed.min())
    t_max = float(transformed.max())
    if abs(t_max - t_min) <= 1e-12:
        return [float((min_size + max_size) * 0.5) for _ in values]

    scaled = min_size + (transformed - t_min) * (max_size - min_size) / (t_max - t_min)
    return [float(value) for value in scaled]


def _format_seconds_label(value: float) -> str:
    if not pd.notna(value):
        return 'T=NA'
    return f'T={float(value):.1f}s'


def _bubble_top_text_y(
        ax,
        center_y: float,
        bubble_size: float,
        extra_points: float = 8.0) -> float:
    # Matplotlib scatter size uses area in points^2.
    radius_points = float(np.sqrt(max(float(bubble_size), 0.0) / np.pi))
    total_points = radius_points + extra_points
    pixels = total_points * ax.figure.dpi / 72.0

    x_ref = float(ax.get_xlim()[0])
    x_disp, y_disp = ax.transData.transform((x_ref, float(center_y)))
    _, y_shifted = ax.transData.inverted().transform((x_disp, y_disp + pixels))
    return float(y_shifted)


def _instance_sort_key(instance_name: str) -> tuple[int, int, str]:
    match = re.search(r'(\d+)$', instance_name)
    if match is None:
        return (1, 10**9, instance_name)
    return (0, int(match.group(1)), instance_name)


def _plot_duration_ratio_bubble_aggregate(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, ax = plt.subplots(figsize=(max(10.0, len(sorted_pairs) * 0.75), 6.0))

    bubble_xs: list[float] = []
    bubble_ys: list[float] = []
    bubble_times: list[float] = []
    mean_gaps: list[float] = []
    mean_iterations: list[float] = []

    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
        if len(chunk) == 0:
            continue

        duration_values = pd.to_numeric(chunk['scheduled_duration_over_capacity_ratio'], errors='coerce').dropna()
        if len(duration_values) == 0:
            continue

        total_times = pd.to_numeric(chunk['total_solving_time'], errors='coerce').dropna()
        gap_values = pd.to_numeric(chunk['final_gap_pct'], errors='coerce').dropna()
        iteration_values = pd.to_numeric(chunk['iteration_count'], errors='coerce').dropna()

        bubble_xs.append(position_by_pair[pair])
        bubble_ys.append(float(duration_values.mean()))
        bubble_times.append(float(total_times.sum()) if len(total_times) > 0 else 0.0)
        mean_gaps.append(float(gap_values.mean()) if len(gap_values) > 0 else np.nan)
        mean_iterations.append(float(iteration_values.mean()) if len(iteration_values) > 0 else np.nan)

    bubble_sizes = _scale_bubble_sizes(bubble_times, min_size=160.0, max_size=1800.0)
    if len(bubble_xs) > 0:
        ax.scatter(
            bubble_xs,
            bubble_ys,
            s=bubble_sizes,
            color=BUBBLE_COLOR,
            alpha=0.52,
            edgecolors='#1f2f40',
            linewidths=1.0)

        y_min = min(bubble_ys)
        y_max = max(bubble_ys)
        y_span = max(y_max - y_min, 0.01)
        label_offset = max(y_span * 0.08, 0.001)
        y_padding = max(y_span * 0.24, 0.004)
        ax.set_ylim(y_min - y_padding * 1.9, y_max + y_padding * 1.4)
        fig.canvas.draw()
        for x, y, mean_gap, mean_iteration, bubble_size, total_time in zip(
                bubble_xs, bubble_ys, mean_gaps, mean_iterations, bubble_sizes, bubble_times):
            gap_text = f'{mean_gap:.2f}%' if pd.notna(mean_gap) else 'NA'
            iteration_text = f'{mean_iteration:.1f}' if pd.notna(mean_iteration) else 'NA'
            ax.text(
                x,
                _bubble_top_text_y(ax, y, bubble_size, extra_points=8.0),
                f'gap m={gap_text}\niter m={iteration_text}',
                ha='center',
                va='bottom',
                fontsize=8)

            time_label = _format_seconds_label(total_time)
            size_factor = 0.0
            if len(bubble_sizes) > 1:
                min_size = min(bubble_sizes)
                max_size = max(bubble_sizes)
                if max_size > min_size:
                    size_factor = (bubble_size - min_size) / (max_size - min_size)
            y_bottom = ax.get_ylim()[0]
            time_offset = label_offset * (0.85 + 0.85 * size_factor)
            ax.text(
                x,
                max(y - time_offset, y_bottom + label_offset * 0.60),
                time_label,
                ha='center',
                va='top',
                fontsize=8,
                color=BUBBLE_COLOR)

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel('Mean scheduled duration / total capacity')
    ax.set_title('Bubble summary: duration ratio (Y), total solving time (bubble size)')
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases, bottom=0.02, top=0.90)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_core_count_vs_core_size_bubble_aggregate(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, ax = plt.subplots(figsize=(max(10.0, len(sorted_pairs) * 0.75), 6.0))

    bubble_xs: list[float] = []
    bubble_ys: list[float] = []
    bubble_core_sizes: list[float] = []
    mean_iterations: list[float] = []

    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
        if len(chunk) == 0:
            continue

        core_count_values = pd.to_numeric(chunk['avg_cores_per_iteration'], errors='coerce').dropna()
        core_size_values = pd.to_numeric(chunk['avg_core_size_per_iteration'], errors='coerce').dropna()
        iteration_values = pd.to_numeric(chunk['iteration_count'], errors='coerce').dropna()

        core_count_median = float(core_count_values.median()) if len(core_count_values) > 0 else 0.0
        core_size_median = float(core_size_values.median()) if len(core_size_values) > 0 else 0.0

        bubble_xs.append(position_by_pair[pair])
        bubble_ys.append(core_count_median)
        bubble_core_sizes.append(core_size_median)
        mean_iterations.append(float(iteration_values.mean()) if len(iteration_values) > 0 else np.nan)

    bubble_sizes = _scale_bubble_sizes(bubble_core_sizes, min_size=160.0, max_size=1800.0)
    if len(bubble_xs) > 0:
        ax.scatter(
            bubble_xs,
            bubble_ys,
            s=bubble_sizes,
            color=BUBBLE_COLOR,
            alpha=0.52,
            edgecolors='#1f2f40',
            linewidths=1.0)

        y_min = min(bubble_ys)
        y_max = max(bubble_ys)
        y_span = max(y_max - y_min, 1.0)
        label_offset = max(y_span * 0.08, 0.08)
        y_padding = max(y_span * 0.24, 0.3)
        ax.set_ylim(y_min - y_padding * 1.9, y_max + y_padding * 1.4)
        fig.canvas.draw()
        for x, y, bubble_size, core_size_median, mean_iteration in zip(
                bubble_xs, bubble_ys, bubble_sizes, bubble_core_sizes, mean_iterations):
            iteration_text = f'{mean_iteration:.1f}' if pd.notna(mean_iteration) else 'NA'
            ax.text(
                x,
                _bubble_top_text_y(ax, y, bubble_size, extra_points=8.0),
                f'req/core m={core_size_median:.2f}\niter m={iteration_text}',
                ha='center',
                va='bottom',
                fontsize=8)

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel('Median avg generated cores per iteration')
    ax.set_title('Bubble summary: generated cores (Y), requests-per-core median (bubble size)')
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases, bottom=0.02, top=0.90)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_duration_ratio_bubble_per_group(
        summary_df: pd.DataFrame,
        results_path: Path):
    for (config_name, group_name), chunk in summary_df.groupby(['config', 'group']):
        pair_df = chunk.copy()
        if len(pair_df) == 0:
            continue

        pair_df['instance'] = pair_df['instance'].astype(str)
        pair_df['_sort_key'] = pair_df['instance'].map(_instance_sort_key)
        pair_df = pair_df.sort_values('_sort_key').drop(columns=['_sort_key'])

        x_positions = list(range(len(pair_df)))
        x_labels = pair_df['instance'].tolist()
        y_values = pd.to_numeric(pair_df['scheduled_duration_over_capacity_ratio'], errors='coerce').to_numpy(dtype=float)
        time_values = pd.to_numeric(pair_df['total_solving_time'], errors='coerce').fillna(0.0).to_numpy(dtype=float)
        gap_values = pd.to_numeric(pair_df['final_gap_pct'], errors='coerce').to_numpy(dtype=float)
        iteration_values = pd.to_numeric(pair_df['iteration_count'], errors='coerce').to_numpy(dtype=float)

        bubble_sizes = _scale_bubble_sizes(time_values.tolist(), min_size=140.0, max_size=1600.0)

        valid_indices = [idx for idx, value in enumerate(y_values) if pd.notna(value)]
        if len(valid_indices) == 0:
            continue

        fig, ax = plt.subplots(figsize=(max(9.0, len(valid_indices) * 1.2), 5.8))
        scatter_x = [x_positions[idx] for idx in valid_indices]
        scatter_y = [float(y_values[idx]) for idx in valid_indices]
        scatter_sizes = [bubble_sizes[idx] for idx in valid_indices]

        ax.scatter(
            scatter_x,
            scatter_y,
            s=scatter_sizes,
            color=BUBBLE_COLOR,
            alpha=0.52,
            edgecolors='#1f2f40',
            linewidths=1.0)

        y_min = min(scatter_y)
        y_max = max(scatter_y)
        y_span = max(y_max - y_min, 0.01)
        label_offset = max(y_span * 0.10, 0.001)
        y_padding = max(y_span * 0.24, 0.004)
        ax.set_ylim(y_min - y_padding * 1.9, y_max + y_padding * 1.4)
        fig.canvas.draw()
        for idx, bubble_size in zip(valid_indices, scatter_sizes):
            gap_text = f'{gap_values[idx]:.2f}%' if pd.notna(gap_values[idx]) else 'NA'
            iteration_text = f'{iteration_values[idx]:.1f}' if pd.notna(iteration_values[idx]) else 'NA'
            ax.text(
                x_positions[idx],
                _bubble_top_text_y(ax, float(y_values[idx]), bubble_size, extra_points=8.0),
                f'gap={gap_text}\niter={iteration_text}',
                ha='center',
                va='bottom',
                fontsize=8)

            time_label = _format_seconds_label(float(time_values[idx]))
            size_factor = 0.0
            if len(scatter_sizes) > 1:
                min_size = min(scatter_sizes)
                max_size = max(scatter_sizes)
                if max_size > min_size:
                    size_factor = (bubble_size - min_size) / (max_size - min_size)
            y_bottom = ax.get_ylim()[0]
            time_offset = label_offset * (0.75 + 0.80 * size_factor)
            ax.text(
                x_positions[idx],
                max(float(y_values[idx]) - time_offset, y_bottom + label_offset * 0.60),
                time_label,
                ha='center',
                va='top',
                fontsize=8,
                color=BUBBLE_COLOR)

        ax.set_xticks(x_positions)
        ax.set_xticklabels(x_labels)
        ax.set_ylabel('Scheduled duration / total capacity')
        ax.set_xlabel('Instance')
        ax.set_title(
            f'Bubble detail for {config_name} / {group_name}\n'
            'Y=duration ratio, bubble size=total solving time')
        ax.grid(axis='y', alpha=0.25)

        fig.tight_layout(rect=(0.0, 0.02, 1.0, 0.95))

        # Same destination style as per-instance analytical plots
        # (e.g., solving_times): save under each instance result folder.
        for instance_name in pair_df['instance'].tolist():
            instance_plot_path = results_path.joinpath(
                f'{config_name}__{group_name}__{instance_name}',
                'plots')
            instance_plot_path.mkdir(parents=True, exist_ok=True)
            fig.savefig(instance_plot_path.joinpath('bubble_duration_ratio_group.png'))
        plt.close(fig)


def _plot_optimal_count_and_mean_gap(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, ax = plt.subplots(
        figsize=(_figure_width_for_categories(len(sorted_pairs), base_width=11.0, per_category=0.92), 5.4))

    heights: list[int] = []
    mean_gaps = []
    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
        heights.append(int(chunk['is_optimal'].sum()))
        mean_gaps.append(float(chunk['final_gap_pct'].dropna().mean()) if chunk['final_gap_pct'].dropna().shape[0] > 0 else np.nan)

    max_height = max(heights) if len(heights) > 0 else 0
    y_top = 1.0 if max_height <= 0 else (max_height + max(1.0, max_height * 0.25))
    zero_stub = 0.04 * y_top
    displayed_heights = [float(height) if height > 0 else zero_stub for height in heights]

    bar_colors = ['#4C78A8' if height > 0 else '#CFD8E3' for height in heights]
    bars = ax.bar(
        x_positions,
        displayed_heights,
        width=0.42,
        color=bar_colors,
        edgecolor='#2f4f6f',
        linewidth=0.8)

    ax.set_ylim(0.0, y_top)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    label_offset = max(0.04 * y_top, 0.03)

    for bar, count, shown_height, mean_gap in zip(bars, heights, displayed_heights, mean_gaps):
        # Mostra sempre il conteggio sulla barra (anche quando e' zero).
        ax.text(
            bar.get_x() + bar.get_width() * 0.5,
            min(shown_height + 0.01 * y_top, y_top * 0.92),
            f'{count}',
            ha='center',
            va='bottom',
            fontsize=8,
            color='#1f2f40')
        if pd.notna(mean_gap):
            ax.text(
                bar.get_x() + bar.get_width() * 0.5,
                min(shown_height + label_offset, y_top * 0.98),
                f'gap m={mean_gap:.2f}%',
                ha='center',
                va='bottom',
                fontsize=8)

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel('Optimal instances count')
    ax.set_title('Optimal solved instances by test/group (label: mean final gap %)')
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases, top=0.90)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_subproblem_totals(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, ax = plt.subplots(
        figsize=(_figure_width_for_categories(len(sorted_pairs), base_width=11.5, per_category=1.02), 5.4))

    master_totals = []
    sub_totals = []
    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
        master_values = pd.to_numeric(chunk['total_master_time'], errors='coerce').dropna()
        sub_values = pd.to_numeric(chunk['total_subproblem_time'], errors='coerce').dropna()
        master_totals.append(float(master_values.mean()) if len(master_values) > 0 else 0.0)
        sub_totals.append(float(sub_values.mean()) if len(sub_values) > 0 else 0.0)

    total_heights = [m + s for m, s in zip(master_totals, sub_totals)]
    y_top = max(total_heights) * 1.20 if len(total_heights) > 0 and max(total_heights) > 0 else 1.0
    ax.set_ylim(0.0, y_top)

    bar_width = 0.42
    master_bars = ax.bar(
        x_positions,
        master_totals,
        width=bar_width,
        color='#4C78A8',
        label='MP mean time per instance')
    sub_bars = ax.bar(
        x_positions,
        sub_totals,
        width=bar_width,
        bottom=master_totals,
        color='#F58518',
        label='SP mean time per instance')

    label_offset = max(0.025 * y_top, 1.2)
    min_inside = 0.08 * y_top
    min_inside_bar_width_px = 56.0
    fig.canvas.draw()
    for x, master_value, sub_value, total_value, master_bar, sub_bar in zip(
            x_positions, master_totals, sub_totals, total_heights, master_bars, sub_bars):

        mp_label = f'MP {master_value:.1f}s'
        sp_label = f'SP {sub_value:.1f}s'
        bar_width_px = _bar_width_px(ax, master_bar)

        if master_value >= min_inside and bar_width_px >= min_inside_bar_width_px:
            ax.text(
                master_bar.get_x() + master_bar.get_width() * 0.5,
                master_value * 0.5,
                mp_label,
                ha='center',
                va='center',
                fontsize=8,
                color='white')
            mp_label_y = master_value
        else:
            mp_label_y = total_value + label_offset
            ax.text(
                x,
                mp_label_y,
                mp_label,
                ha='center',
                va='bottom',
                fontsize=8,
                color='#1f2f40',
                bbox={
                    'boxstyle': 'round,pad=0.16',
                    'facecolor': '#4C78A8',
                    'alpha': 0.16,
                    'edgecolor': 'none',
                })

        if sub_value >= min_inside and bar_width_px >= min_inside_bar_width_px:
            ax.text(
                sub_bar.get_x() + sub_bar.get_width() * 0.5,
                master_value + sub_value * 0.5,
                sp_label,
                ha='center',
                va='center',
                fontsize=8,
                color='#1f2f40')
        else:
            sp_label_y = total_value + label_offset
            min_label_distance = label_offset * 1.35
            if abs(sp_label_y - mp_label_y) < min_label_distance:
                sp_label_y = mp_label_y + min_label_distance
            ax.text(
                x,
                sp_label_y,
                sp_label,
                ha='center',
                va='bottom',
                fontsize=8,
                color='#1f2f40',
                bbox={
                    'boxstyle': 'round,pad=0.16',
                    'facecolor': '#F58518',
                    'alpha': 0.16,
                    'edgecolor': 'none',
                })

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel('Mean time per instance (s)')
    ax.set_title('Mean solving time per instance split by phase (stacked)')
    ax.legend()
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_subproblem_time_share_pct(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, ax = plt.subplots(
        figsize=(_figure_width_for_categories(len(sorted_pairs), base_width=11.5, per_category=1.02), 5.4))

    master_totals = []
    sub_totals = []
    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
        master_values = pd.to_numeric(chunk['total_master_time'], errors='coerce').dropna()
        sub_values = pd.to_numeric(chunk['total_subproblem_time'], errors='coerce').dropna()
        master_totals.append(float(master_values.mean()) if len(master_values) > 0 else 0.0)
        sub_totals.append(float(sub_values.mean()) if len(sub_values) > 0 else 0.0)

    master_shares = []
    sub_shares = []
    for master_value, sub_value in zip(master_totals, sub_totals):
        total_value = master_value + sub_value
        if total_value <= 0:
            master_shares.append(0.0)
            sub_shares.append(0.0)
        else:
            master_shares.append(100.0 * master_value / total_value)
            sub_shares.append(100.0 * sub_value / total_value)

    bar_width = 0.42
    master_bars = ax.bar(
        x_positions,
        master_shares,
        width=bar_width,
        color='#4C78A8',
        label='MP share (%)')
    sub_bars = ax.bar(
        x_positions,
        sub_shares,
        width=bar_width,
        bottom=master_shares,
        color='#F58518',
        label='SP share (%)')

    ax.set_ylim(0.0, 110.0)
    label_offset = 2.6
    min_inside = 8.0
    min_inside_bar_width_px = 56.0
    fig.canvas.draw()
    for x, master_pct, sub_pct, master_bar, sub_bar in zip(
            x_positions, master_shares, sub_shares, master_bars, sub_bars):
        mp_label = f'MP {master_pct:.1f}%'
        sp_label = f'SP {sub_pct:.1f}%'
        bar_width_px = _bar_width_px(ax, master_bar)

        if master_pct >= min_inside and bar_width_px >= min_inside_bar_width_px:
            ax.text(
                master_bar.get_x() + master_bar.get_width() * 0.5,
                master_pct * 0.5,
                mp_label,
                ha='center',
                va='center',
                fontsize=8,
                color='white')
            mp_label_y = master_pct
        else:
            mp_label_y = 100.0 + label_offset
            ax.text(
                x,
                mp_label_y,
                mp_label,
                ha='center',
                va='bottom',
                fontsize=8,
                color='#1f2f40',
                bbox={
                    'boxstyle': 'round,pad=0.16',
                    'facecolor': '#4C78A8',
                    'alpha': 0.16,
                    'edgecolor': 'none',
                })

        if sub_pct >= min_inside and bar_width_px >= min_inside_bar_width_px:
            ax.text(
                sub_bar.get_x() + sub_bar.get_width() * 0.5,
                master_pct + sub_pct * 0.5,
                sp_label,
                ha='center',
                va='center',
                fontsize=8,
                color='#1f2f40')
        else:
            sp_label_y = 100.0 + label_offset
            min_label_distance = label_offset * 1.35
            if abs(sp_label_y - mp_label_y) < min_label_distance:
                sp_label_y = mp_label_y + min_label_distance
            ax.text(
                x,
                sp_label_y,
                sp_label,
                ha='center',
                va='bottom',
                fontsize=8,
                color='#1f2f40',
                bbox={
                    'boxstyle': 'round,pad=0.16',
                    'facecolor': '#F58518',
                    'alpha': 0.16,
                    'edgecolor': 'none',
                })

    _decorate_grouped_axis(ax, x_positions, x_labels, config_spans, separators, config_aliases)
    ax.set_ylabel('Share of total solving time (%)')
    ax.set_title('Total solving time split by phase (stacked 100%)')
    ax.legend()
    ax.grid(axis='y', alpha=0.25)
    _finalize_grouped_figure(fig, ax, config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_mean_bar_and_subproblem_iteration_box(
        master_df: pd.DataFrame,
        sub_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, axs = plt.subplots(
        2, 1,
        figsize=(_figure_width_for_categories(len(sorted_pairs), base_width=11.0, per_category=0.90), 8.0),
        sharex=True,
        gridspec_kw={'height_ratios': [1.0, 1.2]})

    master_means = []
    for pair in sorted_pairs:
        chunk = master_df[(master_df['config'] == pair[0]) & (master_df['group'] == pair[1])]
        master_means.append(float(chunk['master_time'].dropna().mean()) if 'master_time' in chunk and chunk['master_time'].dropna().shape[0] > 0 else np.nan)
    axs[0].bar(x_positions, master_means, width=0.40, color='#4C78A8')
    axs[0].set_ylabel('Mean master time / iteration (s)')
    axs[0].set_title('Master mean time per iteration (bar) and subproblem iteration-time distribution (box)')
    axs[0].grid(axis='y', alpha=0.25)

    box_data = []
    for pair in sorted_pairs:
        chunk = sub_df[(sub_df['config'] == pair[0]) & (sub_df['group'] == pair[1])]
        if len(chunk) == 0 or 'time' not in chunk.columns or 'iteration' not in chunk.columns:
            box_data.append([np.nan])
            continue
        per_iteration_means = chunk.groupby('iteration')['time'].mean().dropna().values.tolist()
        if len(per_iteration_means) == 0:
            box_data.append([np.nan])
        else:
            box_data.append(per_iteration_means)

    axs[1].boxplot(box_data, positions=x_positions, widths=0.40, patch_artist=True, showfliers=True)
    axs[1].set_ylabel('Subproblem mean time by iteration (s)')
    axs[1].grid(axis='y', alpha=0.25)
    _decorate_grouped_axis(axs[1], x_positions, x_labels, config_spans, separators, config_aliases)

    _finalize_grouped_figure(fig, axs[0], config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_timeout_feasible_and_mean_master_gap(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, axs = plt.subplots(
        2, 1,
        figsize=(max(10.0, len(sorted_pairs) * 0.72), 8.2),
        sharex=True,
        gridspec_kw={'height_ratios': [1.0, 1.0]})

    timeout_data = []
    timeout_positions = []
    mean_gap_data = []
    mean_gap_positions = []

    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]

        timeout_values = pd.to_numeric(
            chunk['timeout_feasible_nonoptimal_iteration_count'],
            errors='coerce').dropna().values.tolist()
        if len(timeout_values) > 0:
            timeout_data.append(timeout_values)
            timeout_positions.append(position_by_pair[pair])

        mean_gap_values = pd.to_numeric(
            chunk['mean_master_gap_over_iterations'],
            errors='coerce').dropna().values.tolist()
        if len(mean_gap_values) > 0:
            mean_gap_data.append(mean_gap_values)
            mean_gap_positions.append(position_by_pair[pair])

    if len(timeout_data) > 0:
        axs[0].boxplot(
            timeout_data,
            positions=timeout_positions,
            widths=0.40,
            patch_artist=True,
            showfliers=True)

    if len(mean_gap_data) > 0:
        axs[1].boxplot(
            mean_gap_data,
            positions=mean_gap_positions,
            widths=0.40,
            patch_artist=True,
            showfliers=True)

    for ax in axs:
        for separator_x in separators:
            ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)
        ax.grid(axis='y', alpha=0.25)

    axs[0].set_ylabel('Timeout + feasible non-opt\niterations per instance')
    axs[0].set_title('Master timeout/non-opt iterations and mean master gap by test/group')
    axs[0].yaxis.set_major_locator(MaxNLocator(integer=True))
    axs[0].tick_params(axis='x', which='both', labelbottom=False)

    _decorate_grouped_axis(axs[1], x_positions, x_labels, config_spans, separators, config_aliases)
    axs[1].set_ylabel('Mean master gap\nacross iterations (%)')

    _finalize_grouped_figure(fig, axs[0], config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_request_grouping_boxplots(
        summary_df: pd.DataFrame,
        sorted_pairs: list[tuple[str, str]],
        position_by_pair: dict[tuple[str, str], float],
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, axs = plt.subplots(
        2, 1,
        figsize=(max(10.0, len(sorted_pairs) * 0.72), 8.4),
        sharex=True,
        gridspec_kw={'height_ratios': [1.0, 1.0]})

    multi_request_data = []
    multi_request_positions = []
    aggregated_request_data = []
    aggregated_request_positions = []

    for pair in sorted_pairs:
        chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]

        multi_values = pd.to_numeric(
            chunk['mean_multi_request_patients_per_iteration'],
            errors='coerce').dropna().values.tolist()
        if len(multi_values) > 0:
            multi_request_data.append(multi_values)
            multi_request_positions.append(position_by_pair[pair])

        aggregated_values = pd.to_numeric(
            chunk['mean_patient_day_group_size'],
            errors='coerce').dropna().values.tolist()
        if len(aggregated_values) > 0:
            aggregated_request_data.append(aggregated_values)
            aggregated_request_positions.append(position_by_pair[pair])

    if len(multi_request_data) > 0:
        axs[0].boxplot(
            multi_request_data,
            positions=multi_request_positions,
            widths=0.40,
            patch_artist=True,
            showfliers=True)

    if len(aggregated_request_data) > 0:
        axs[1].boxplot(
            aggregated_request_data,
            positions=aggregated_request_positions,
            widths=0.40,
            patch_artist=True,
            showfliers=True)

    for ax in axs:
        for separator_x in separators:
            ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)
        ax.grid(axis='y', alpha=0.25)

    axs[0].set_ylabel('Mean #patients/iteration\nwith >1 requests on same day')
    axs[0].set_title('Master same-day request concentration metrics by test/group')
    axs[0].tick_params(axis='x', which='both', labelbottom=False)

    _decorate_grouped_axis(axs[1], x_positions, x_labels, config_spans, separators, config_aliases)
    axs[1].set_ylabel('Mean requests per\npatient-day group')

    _finalize_grouped_figure(fig, axs[0], config_spans, config_aliases)
    fig.savefig(save_path)
    plt.close(fig)


def plot_experiment_group_comparison(
        master_result_df: pd.DataFrame,
        subproblem_result_df: pd.DataFrame,
        results_path: Path,
        config):
    master_df = _filter_rows(master_result_df, config)
    sub_df = _filter_rows(subproblem_result_df, config)
    instance_df = pd.DataFrame()

    instance_analysis_path = results_path.joinpath('analysis', 'instance_analysis.xlsx')
    if instance_analysis_path.exists():
        try:
            instance_df = pd.read_excel(
                instance_analysis_path,
                sheet_name='Instance data',
                usecols=lambda name: name in {
                    'config',
                    'group',
                    'instance',
                    'status',
                    'status_reason',
                    'final_gap_pct',
                })
            instance_df = _filter_rows(instance_df, config)
        except Exception as exc:
            print(f'WARNING: unable to read instance_analysis.xlsx for experiment comparison: {exc}')

    if len(master_df) == 0:
        print('No master rows after filters for experiment comparison plots.')
        return

    summary_df = _instance_summary(master_df, sub_df, instance_df)
    if len(summary_df) == 0:
        print('No instance summary rows available for experiment comparison plots.')
        return

    request_grouping_df = _build_master_request_grouping_metrics(master_df, results_path)
    if len(request_grouping_df) > 0:
        summary_df = summary_df.merge(
            request_grouping_df,
            on=['config', 'group', 'instance'],
            how='left')
    else:
        summary_df['mean_multi_request_patients_per_iteration'] = np.nan
        summary_df['mean_patient_day_group_size'] = np.nan

    pairs = list({(str(row['config']), str(row['group'])) for _, row in summary_df.iterrows()})
    present_configs = sorted({pair[0] for pair in pairs})
    ordered_config_names = _resolve_config_order(present_configs, config)
    config_aliases = _resolve_config_aliases(config)
    group_rank_map = _resolve_group_rank_map(config)
    sorted_pairs, position_by_pair, x_positions, x_labels, config_spans, separators = _grouped_pairs_layout(
        pairs,
        ordered_config_names,
        group_rank_map)

    save_path = results_path.joinpath('plots')
    save_path.mkdir(exist_ok=True)

    data_iteration_count: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_avg_cores: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_total_time: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_gap_pct: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_final_objective_value: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_duration_ratio: dict[tuple[str, str], list[float]] = defaultdict(list)
    data_scheduled_ratio: dict[tuple[str, str], list[float]] = defaultdict(list)

    for _, row in summary_df.iterrows():
        pair = (str(row['config']), str(row['group']))
        data_iteration_count[pair].append(float(row['iteration_count']))
        data_avg_cores[pair].append(float(row['avg_cores_per_iteration']))
        data_total_time[pair].append(float(row['total_solving_time']))
        data_gap_pct[pair].append(float(row['final_gap_pct']))
        data_final_objective_value[pair].append(float(row['final_objective_value']))
        data_duration_ratio[pair].append(float(row['scheduled_duration_over_capacity_ratio']))
        data_scheduled_ratio[pair].append(float(row['scheduled_number_over_total_ratio']))

    _save_boxplot(
        data_iteration_count,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='LBBD iterations',
        title='LBBD iterations distribution by test/group',
        save_path=save_path.joinpath('comparison_box_lbbd_iterations.png'))

    _save_boxplot(
        data_avg_cores,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Average generated cores per iteration',
        title='Average generated cores per iteration (instance distribution)',
        save_path=save_path.joinpath('comparison_box_avg_cores_per_iteration.png'))

    _save_boxplot(
        data_total_time,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Total solving time (s)',
        title='Total solving time distribution by test/group',
        save_path=save_path.joinpath('comparison_box_total_solving_time.png'))

    _save_boxplot(
        data_gap_pct,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Final gap % (master upper bound vs final feasible)',
        title='Final gap % distribution by test/group',
        save_path=save_path.joinpath('comparison_box_final_gap_pct.png'))

    _save_boxplot(
        data_final_objective_value,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Final objective value',
        title='Final objective value distribution by test/group',
        save_path=save_path.joinpath('comparison_box_final_objective_value.png'))

    _save_boxplot(
        data_duration_ratio,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Scheduled duration / total capacity',
        title='Satisfied duration ratio distribution by test/group',
        save_path=save_path.joinpath('comparison_box_scheduled_duration_over_capacity_ratio.png'))

    _save_boxplot(
        data_scheduled_ratio,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        ylabel='Scheduled services / total services',
        title='Scheduled services ratio distribution by test/group',
        save_path=save_path.joinpath('comparison_box_scheduled_services_ratio.png'))

    _plot_duration_ratio_bubble_aggregate(
        summary_df,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_bubble_duration_ratio_summary.png'))

    _plot_core_count_vs_core_size_bubble_aggregate(
        summary_df,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_bubble_core_count_vs_core_size.png'))

    _plot_duration_ratio_bubble_per_group(
        summary_df,
        results_path)

    _plot_optimal_count_and_mean_gap(
        summary_df,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_bar_optimal_count_with_mean_gap.png'))

    _plot_master_subproblem_totals(
        summary_df,
        sorted_pairs,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_bar_master_vs_subproblem_total_time.png'))

    _plot_master_subproblem_time_share_pct(
        summary_df,
        sorted_pairs,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_bar_master_vs_subproblem_time_share_pct.png'))

    _plot_master_mean_bar_and_subproblem_iteration_box(
        master_df,
        sub_df,
        sorted_pairs,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_master_bar_and_subproblem_iteration_box.png'))

    _plot_timeout_feasible_and_mean_master_gap(
        summary_df,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_master_timeout_feasible_and_mean_gap_boxplots.png'))

    _plot_master_request_grouping_boxplots(
        summary_df,
        sorted_pairs,
        position_by_pair,
        x_positions,
        x_labels,
        config_spans,
        separators,
        config_aliases,
        save_path=save_path.joinpath('comparison_master_same_day_request_grouping_boxplots.png'))
