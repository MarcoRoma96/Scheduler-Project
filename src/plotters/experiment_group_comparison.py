from collections import defaultdict
from pathlib import Path
import json
import re

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import to_hex, to_rgb
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator, NullFormatter

from src.common.analysis_constants import FINAL_GAP_OPTIMAL_TOLERANCE_PCT
from src.common.tools import is_combination_to_do
from src.common.file_load_and_dump import decode_cores, decode_master_result


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

CORE_FILE_PREFIXES = [
    'expanded',
    'pruned',
    'reduced',
    'basic',
    'generalist',
    'preemptive',
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
ROW_SPLIT_DIMENSION_ALIASES = {
    'patients': 'patient_number',
    'patient_number': 'patient_number',
    'care_units': 'care_unit_number',
    'care_unit_number': 'care_unit_number',
    'test': 'test',
    'config': 'test',
}
ROW_SPLIT_DIMENSION_LABELS = {
    'patient_number': 'patients',
    'care_unit_number': 'care units',
    'test': 'test',
}

BUBBLE_COLOR = '#4C78A8'
CARE_UNIT_BASE_PALETTE = [
    '#8FB6E8',
    '#90CFAF',
    '#F0C38C',
    '#C4B0E3',
    '#E7A6A1',
]
GROUP_MARKER_CYCLE = ['o', 's', '^', 'D', 'v', 'P', 'X', '<', '>', 'h']

# Modalita' di scalatura della dimensione delle bolle.
# Valori ammessi:
# - 'linear': usa il valore grezzo
# - 'log1p': comprime il range con log(1 + x)
BUBBLE_SIZE_SCALE_MODE = 'linear'
GROUP_PROFILE_BASE_SAMPLE_STEP = 10
GROUP_PROFILE_MAX_DISPLAYED_ITERATIONS = 80
GROUP_PROFILE_MAX_FIGURE_WIDTH = 18.0


def _figure_width_for_categories(
        category_count: int,
        base_width: float = 11.0,
        per_category: float = 0.95) -> float:
    return max(base_width, float(category_count) * per_category)


def _blend_color(color: str, factor: float) -> str:
    rgb = np.array(to_rgb(color), dtype=float)
    if factor >= 1.0:
        mixed = rgb + (1.0 - rgb) * min(factor - 1.0, 1.0)
    else:
        mixed = rgb * max(factor, 0.0)
    return str(to_hex(np.clip(mixed, 0.0, 1.0)))


def _build_pair_style_map(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    if len(pairs) == 0:
        return {}

    care_units = sorted({
        int(attributes['care_unit_number'])
        for _, group_name in pairs
        for attributes in [_extract_group_attributes(group_name)]
        if attributes['care_unit_number'] is not None
    })
    patients = sorted({
        int(attributes['patient_number'])
        for _, group_name in pairs
        for attributes in [_extract_group_attributes(group_name)]
        if attributes['patient_number'] is not None
    })

    care_unit_colors = {
        care_unit: CARE_UNIT_BASE_PALETTE[index % len(CARE_UNIT_BASE_PALETTE)]
        for index, care_unit in enumerate(care_units)
    }

    if len(patients) <= 1:
        patient_factors = {patient: 0.92 for patient in patients}
    else:
        patient_factors = {
            patient: float(np.linspace(1.06, 0.72, len(patients))[index])
            for index, patient in enumerate(patients)
        }

    style_map: dict[tuple[str, str], dict[str, str]] = {}
    for pair in pairs:
        attributes = _extract_group_attributes(pair[1])
        care_unit_number = attributes['care_unit_number']
        patient_number = attributes['patient_number']
        base_color = care_unit_colors.get(
            int(care_unit_number) if care_unit_number is not None else -1,
            '#A8B6C8')
        tone_factor = patient_factors.get(
            int(patient_number) if patient_number is not None else -1,
            0.90)
        facecolor = _blend_color(base_color, tone_factor)
        edgecolor = _blend_color(facecolor, 0.62)
        style_map[pair] = {
            'facecolor': facecolor,
            'edgecolor': edgecolor,
        }
    return style_map


def _pair_style(
        pair_styles: dict[tuple[str, str], dict[str, str]] | None,
        pair: tuple[str, str]) -> dict[str, str]:
    if pair_styles is None:
        return {'facecolor': '#4C78A8', 'edgecolor': '#2f4f6f'}
    return pair_styles.get(pair, {'facecolor': '#4C78A8', 'edgecolor': '#2f4f6f'})


def _build_config_color_map(config_names: list[str]) -> dict[str, str]:
    if len(config_names) == 0:
        return {}
    cmap = plt.get_cmap('tab10')
    return {
        str(config_name): str(to_hex(cmap(index % 10)))
        for index, config_name in enumerate(config_names)
    }


def _flatten_numeric_values(sequences: list[list[float]]) -> list[float]:
    values: list[float] = []
    for sequence in sequences:
        values.extend([
            float(value)
            for value in sequence
            if pd.notna(value)
        ])
    return values


def _compute_shared_ylim(
        values: list[float],
        *,
        include_zero: bool = False,
        lower_pad_frac: float = 0.08,
        upper_pad_frac: float = 0.12,
        min_positive_span: float = 1.0) -> tuple[float, float] | None:
    cleaned = [float(value) for value in values if pd.notna(value)]
    if len(cleaned) == 0:
        return None

    min_value = min(cleaned)
    max_value = max(cleaned)
    if include_zero and min_value >= 0.0:
        min_value = 0.0

    span = max(max_value - min_value, min_positive_span if max_value >= 0.0 else abs(max_value) * 0.10, 1e-6)
    lower = min_value - span * lower_pad_frac
    upper = max_value + span * upper_pad_frac
    if include_zero and min(cleaned) >= 0.0:
        lower = 0.0
    if upper <= lower:
        upper = lower + max(min_positive_span, 1.0)
    return float(lower), float(upper)


def _merge_ylim_bounds(bounds: list[tuple[float, float] | None]) -> tuple[float, float] | None:
    valid_bounds = [bound for bound in bounds if bound is not None]
    if len(valid_bounds) == 0:
        return None
    lower = min(bound[0] for bound in valid_bounds)
    upper = max(bound[1] for bound in valid_bounds)
    if upper <= lower:
        upper = lower + 1.0
    return float(lower), float(upper)


def _hide_labels_above_unit_ratio(ax) -> None:
    def _formatter(value, _position):
        if value > 1.0 + 1e-9:
            return ''
        return f'{value:g}'

    ax.yaxis.set_major_formatter(FuncFormatter(_formatter))


def _style_boxplot_artists(
        boxplot_result,
        pairs: list[tuple[str, str]],
        pair_styles: dict[tuple[str, str], dict[str, str]] | None):
    for patch, pair in zip(boxplot_result.get('boxes', []), pairs):
        style = _pair_style(pair_styles, pair)
        patch.set_facecolor(style['facecolor'])
        patch.set_edgecolor(style['edgecolor'])
        patch.set_alpha(0.92)
        patch.set_linewidth(1.0)

    for whisker_index, whisker in enumerate(boxplot_result.get('whiskers', [])):
        pair = pairs[min(whisker_index // 2, len(pairs) - 1)]
        whisker.set_color(_pair_style(pair_styles, pair)['edgecolor'])
        whisker.set_linewidth(1.0)

    for cap_index, cap in enumerate(boxplot_result.get('caps', [])):
        pair = pairs[min(cap_index // 2, len(pairs) - 1)]
        cap.set_color(_pair_style(pair_styles, pair)['edgecolor'])
        cap.set_linewidth(1.0)

    for median in boxplot_result.get('medians', []):
        median.set_color('#2f2f2f')
        median.set_linewidth(1.1)

    for flier_index, flier in enumerate(boxplot_result.get('fliers', [])):
        pair = pairs[min(flier_index, len(pairs) - 1)]
        style = _pair_style(pair_styles, pair)
        flier.set_markeredgecolor(style['edgecolor'])
        flier.set_markerfacecolor(style['facecolor'])
        flier.set_alpha(0.65)


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


def _filter_experiment_comparison_rows(df: pd.DataFrame, config) -> pd.DataFrame:
    if df.empty:
        return df
    required = {'config', 'group', 'instance'}
    if not required.issubset(set(df.columns)):
        return df.iloc[0:0].copy()

    comparison_filter_config = config
    if isinstance(config, dict):
        comparison_filter_config = dict(config)
        # For experiment-group comparisons, the dedicated comparison selection
        # must control which tests appear. Keep generic group/instance filters,
        # but neutralize generic config filtering to avoid hiding explicitly
        # selected comparison configs from the GUI.
        if 'configs_to_do' in comparison_filter_config:
            comparison_filter_config['configs_to_do'] = ['all']

    mask = df.apply(
        lambda row: is_combination_to_do(
            str(row['config']),
            str(row['group']),
            str(row['instance']),
            comparison_filter_config),
        axis=1)
    filtered_df = df[mask].copy()
    if filtered_df.empty or not isinstance(config, dict):
        return filtered_df

    raw_configs_to_do = config.get('experiment_group_comparison_configs_to_do')
    if not isinstance(raw_configs_to_do, list):
        return filtered_df

    selected_configs = [str(name).strip() for name in raw_configs_to_do if str(name).strip() != '']
    if len(selected_configs) == 0:
        return filtered_df

    lowered_configs = {name.lower() for name in selected_configs}
    if 'all' in lowered_configs:
        return filtered_df

    return filtered_df[filtered_df['config'].astype(str).isin(selected_configs)].copy()


def _get_experiment_comparison_save_path(results_path: Path, config) -> Path:
    save_path = results_path.joinpath('plots')
    if isinstance(config, dict):
        raw_subdir = str(config.get('experiment_group_comparison_output_subdir', '')).strip()
        if raw_subdir != '':
            save_path = save_path.joinpath(raw_subdir)
    save_path.mkdir(parents=True, exist_ok=True)
    return save_path


def _get_group_plot_save_path(results_path: Path, config_name: str, group_name: str) -> Path:
    save_path = results_path.joinpath('plots', 'groups', f'{config_name}__{group_name}')
    save_path.mkdir(parents=True, exist_ok=True)
    return save_path


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


def _extract_group_attributes(group_name: str) -> dict[str, int | None]:
    match = GROUP_NAME_PATTERN.search(group_name)
    if match is None:
        return {
            'patient_number': None,
            'care_unit_number': None,
            'operator_number': None,
        }
    operators = match.group('operators')
    return {
        'patient_number': int(match.group('patients')),
        'care_unit_number': int(match.group('care_units')),
        'operator_number': int(operators) if operators is not None else None,
    }


def _format_group_label_without_dimensions(group_name: str, omitted_dimensions: set[str]) -> str:
    attributes = _extract_group_attributes(group_name)
    if all(value is None for value in attributes.values()):
        return group_name

    parts: list[str] = []
    if 'patient_number' not in omitted_dimensions and attributes['patient_number'] is not None:
        parts.append(f"{attributes['patient_number']}pat")
    if 'care_unit_number' not in omitted_dimensions and attributes['care_unit_number'] is not None:
        parts.append(f"{attributes['care_unit_number']}cu")
    if 'operator_number' not in omitted_dimensions and attributes['operator_number'] is not None:
        parts.append(f"{attributes['operator_number']}op")

    return '_'.join(parts) if len(parts) > 0 else group_name


def _resolve_group_rank_map(config) -> dict[str, int]:
    group_rank_map: dict[str, int] = {}
    raw_order = config.get('experiment_group_comparison_group_order') if isinstance(config, dict) else None
    if isinstance(raw_order, list):
        for index, group_name in enumerate(raw_order):
            group_rank_map[str(group_name)] = index
    return group_rank_map


def _resolve_row_split_priority(config) -> list[str]:
    raw_priority = config.get('experiment_group_comparison_row_split_priority') if isinstance(config, dict) else None
    if not isinstance(raw_priority, list):
        return []

    resolved: list[str] = []
    for value in raw_priority:
        normalized = ROW_SPLIT_DIMENSION_ALIASES.get(str(value).strip().lower())
        if normalized is None or normalized in resolved:
            continue
        resolved.append(normalized)
    return resolved


def _pair_row_dimension_value(pair: tuple[str, str], dimension: str):
    if dimension == 'test':
        return str(pair[0])
    attributes = _extract_group_attributes(str(pair[1]))
    return attributes.get(dimension)


def _row_group_sort_key(
        row_key: tuple[object, ...],
        row_split_priority: list[str],
        config_rank: dict[str, int]) -> tuple:
    sort_key: list[object] = []
    sentinel = 10 ** 9
    for dimension, value in zip(row_split_priority, row_key):
        if dimension == 'test':
            label = str(value)
            sort_key.extend([config_rank.get(label, len(config_rank)), label])
        else:
            numeric_value = int(value) if value is not None else sentinel
            sort_key.append(numeric_value)
    return tuple(sort_key)


def _format_row_group_label(
        row_key: tuple[object, ...],
        row_split_priority: list[str],
        config_aliases: dict[str, str]) -> str:
    if len(row_split_priority) == 0:
        return ''

    parts: list[str] = []
    for dimension, value in zip(row_split_priority, row_key):
        if dimension == 'test':
            shown_value = config_aliases.get(str(value), str(value))
        else:
            shown_value = 'NA' if value is None else str(value)
        parts.append(f'{ROW_SPLIT_DIMENSION_LABELS[dimension]}={shown_value}')
    return ' | '.join(parts)


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


def _build_grouped_layout_rows(
        pairs: list[tuple[str, str]],
        ordered_config_names: list[str],
        group_rank_map: dict[str, int],
        row_split_priority: list[str],
        config_aliases: dict[str, str]):
    if len(row_split_priority) == 0:
        sorted_pairs, position_by_pair, x_positions, x_labels, config_spans, separators = _grouped_pairs_layout(
            pairs,
            ordered_config_names,
            group_rank_map)
        return [{
            'row_key': tuple(),
            'row_label': '',
            'sorted_pairs': sorted_pairs,
            'position_by_pair': position_by_pair,
            'x_positions': x_positions,
            'x_labels': x_labels,
            'compact_x_labels': x_labels,
            'show_x_labels': True,
            'config_spans': config_spans,
            'separators': separators,
        }]

    grouped_rows: dict[tuple[object, ...], list[tuple[str, str]]] = defaultdict(list)
    for pair in pairs:
        row_key = tuple(_pair_row_dimension_value(pair, dimension) for dimension in row_split_priority)
        grouped_rows[row_key].append(pair)

    config_rank = {name: index for index, name in enumerate(ordered_config_names)}
    ordered_row_keys = sorted(
        grouped_rows.keys(),
        key=lambda row_key: _row_group_sort_key(row_key, row_split_priority, config_rank))

    layout_rows = []
    for row_key in ordered_row_keys:
        sorted_pairs, position_by_pair, x_positions, x_labels, config_spans, separators = _grouped_pairs_layout(
            grouped_rows[row_key],
            ordered_config_names,
            group_rank_map)
        omitted_dimensions = {dimension for dimension in row_split_priority if dimension in {'patient_number', 'care_unit_number', 'operator_number'}}
        omitted_dimensions.add('operator_number')  # Sempre omettere il numero di operatori, se presente, per compattezza.
        compact_labels = [
            _format_group_label_without_dimensions(pair[1], omitted_dimensions)
            for pair in sorted_pairs
        ]
        layout_rows.append({
            'row_key': row_key,
            'row_label': _format_row_group_label(row_key, row_split_priority, config_aliases),
            'sorted_pairs': sorted_pairs,
            'position_by_pair': position_by_pair,
            'x_positions': x_positions,
            'x_labels': x_labels,
            'compact_x_labels': compact_labels,
            'show_x_labels': True,
            'config_spans': config_spans,
            'separators': separators,
        })

    for row_index, row_layout in enumerate(layout_rows):
        current_labels = row_layout.get('compact_x_labels', row_layout.get('x_labels', []))
        next_labels = None
        if row_index + 1 < len(layout_rows):
            next_labels = layout_rows[row_index + 1].get('compact_x_labels', layout_rows[row_index + 1].get('x_labels', []))
        row_layout['show_x_labels'] = row_index == len(layout_rows) - 1 or current_labels != next_labels

    return layout_rows


def _decorate_grouped_axis(
        ax: plt.Axes,
        x_positions: list[float],
        x_labels: list[str],
        config_spans: dict[str, tuple[float, float]],
        separators: list[float],
        config_aliases: dict[str, str] | None = None,
        show_x_labels: bool = True):
    _ = config_aliases
    ax.set_xticks(x_positions)
    if show_x_labels:
        ax.set_xticklabels(x_labels, rotation=90, ha='center')
    else:
        ax.set_xticklabels([])
        ax.tick_params(axis='x', which='both', labelbottom=False)
    for separator_x in separators:
        ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)

def _figure_width_for_layout_rows(
        layout_rows: list[dict[str, object]],
        base_width: float,
        per_category: float) -> float:
    if len(layout_rows) == 0:
        return base_width
    max_pair_count = max(len(row.get('sorted_pairs', [])) for row in layout_rows)
    return _figure_width_for_categories(max_pair_count, base_width=base_width, per_category=per_category)


def _normalize_axes_list(axes) -> list[plt.Axes]:
    if isinstance(axes, np.ndarray):
        return [ax for ax in axes.flatten().tolist() if isinstance(ax, plt.Axes)]
    if isinstance(axes, list):
        return [ax for ax in axes if isinstance(ax, plt.Axes)]
    return [axes]


def _annotate_grouped_row_headers(
        header_ax: plt.Axes,
        row_layout: dict[str, object],
        config_aliases: dict[str, str] | None = None,
        show_config_headers: bool = True):
    aliases = config_aliases or {}
    config_spans = row_layout.get('config_spans', {})
    row_label = str(row_layout.get('row_label', '')).strip()
    x_limits = header_ax.get_xlim()
    x_span = x_limits[1] - x_limits[0]
    if abs(x_span) <= 1e-12:
        x_span = 1.0

    if row_label != '':
        header_ax.text(
            -0.10,
            0.5,
            row_label.replace(' | ', '\n'),
            transform=header_ax.transAxes,
            ha='center',
            va='center',
            fontsize=9,
            fontweight='bold',
            rotation=90,
            clip_on=False)

    if not show_config_headers:
        return

    y_header = 1.02 if row_label == '' else 1.01
    for config_name, (start_x, end_x) in config_spans.items():
        center = (start_x + end_x) * 0.5
        label = aliases.get(config_name, config_name)
        x_axes = (center - x_limits[0]) / x_span
        header_ax.text(
            float(x_axes),
            y_header,
            label,
            transform=header_ax.transAxes,
            ha='center',
            va='bottom',
            fontsize=9,
            fontweight='bold',
            clip_on=False)


def _finalize_grouped_figure(
        fig,
        header_axes,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str] | None = None,
        main_title: str | None = None,
        bottom: float = 0.0,
        top: float = 0.968):
    left_margin = 0.075 if any(str(row.get('row_label', '')).strip() != '' for row in layout_rows) else 0.0
    if main_title:
        fig.suptitle(main_title, y=0.986)
    fig.tight_layout(rect=(left_margin, bottom, 1.0, top))
    fig.canvas.draw()

    normalized_axes = _normalize_axes_list(header_axes)
    for row_index, (row_layout, header_ax) in enumerate(zip(layout_rows, normalized_axes)):
        _annotate_grouped_row_headers(
            header_ax,
            row_layout,
            config_aliases,
            show_config_headers=(row_index == 0))


def _create_axis_blocks(
        row_count: int,
        block_height_ratios: list[float],
        width: float,
        per_row_height: float):
    fig, axes = plt.subplots(
        row_count * len(block_height_ratios), 1,
        figsize=(width, max(per_row_height, per_row_height * row_count)),
        squeeze=False,
        gridspec_kw={'height_ratios': block_height_ratios * row_count})
    normalized_axes = _normalize_axes_list(axes)
    block_size = len(block_height_ratios)
    return fig, [
        normalized_axes[row_index * block_size:(row_index + 1) * block_size]
        for row_index in range(row_count)
    ]


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
        instance_status_row = instance_status_by_key.get(key, {})
        run_total_time_elapsed = pd.to_numeric(
            instance_status_row.get('run_total_time_elapsed', np.nan),
            errors='coerce')
        fallback_phase_total = 0.0
        has_phase_component = False
        if pd.notna(total_master_time):
            fallback_phase_total += float(total_master_time)
            has_phase_component = True
        if pd.notna(total_subproblem_time):
            fallback_phase_total += float(total_subproblem_time)
            has_phase_component = True
        total_solving_time = float(run_total_time_elapsed) if pd.notna(run_total_time_elapsed) else (fallback_phase_total if has_phase_component else np.nan)
        total_other_tracked_time = np.nan
        if pd.notna(total_solving_time):
            total_other_tracked_time = max(
                0.0,
                float(total_solving_time)
                - (float(total_master_time) if pd.notna(total_master_time) else 0.0)
                - (float(total_subproblem_time) if pd.notna(total_subproblem_time) else 0.0))

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
        status_label = str(instance_status_row.get('status', '')).strip().lower()
        status_reason = str(instance_status_row.get('status_reason', '')).strip().lower()

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
            'total_other_tracked_time': total_other_tracked_time,
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


def _safe_chunk_mean(chunk: pd.DataFrame, column_name: str) -> float:
    if column_name not in chunk.columns:
        return 0.0
    values = pd.to_numeric(chunk[column_name], errors='coerce').dropna()
    return float(values.mean()) if len(values) > 0 else 0.0


def _normalize_day_key(day_value) -> int | str | None:
    if pd.isna(day_value):
        return None
    if isinstance(day_value, (list, tuple)):
        if len(day_value) == 0:
            return None
        day_value = day_value[0]
    try:
        return int(day_value)
    except (TypeError, ValueError):
        normalized = str(day_value).strip()
        return normalized if normalized != '' else None


def _sortable_day_key(day_value) -> tuple[int, int | str]:
    if isinstance(day_value, int):
        return (0, day_value)
    return (1, str(day_value))


def _resolve_core_file_prefix(config_name: str, iteration_path: Path, iteration_rows: pd.DataFrame) -> str | None:
    normalized_config_name = str(config_name).strip().lower()
    if normalized_config_name in CORE_FILE_PREFIXES:
        return normalized_config_name

    for column_name in CORE_NUMBER_COLUMNS:
        if column_name not in iteration_rows.columns:
            continue
        column_values = pd.to_numeric(iteration_rows[column_name], errors='coerce').dropna()
        if len(column_values) > 0:
            return column_name.removesuffix('_core_number')

    existing_prefixes = [
        prefix
        for prefix in CORE_FILE_PREFIXES
        if iteration_path.joinpath(f'{prefix}_cores.json').exists()
    ]
    if len(existing_prefixes) == 0:
        return None
    return existing_prefixes[0]


def _build_core_size_share_metrics(
        master_df: pd.DataFrame,
        sub_df: pd.DataFrame,
        results_path: Path) -> pd.DataFrame:
    if len(master_df) == 0 or len(sub_df) == 0 or 'total_request_number' not in sub_df.columns:
        return pd.DataFrame()

    normalized_sub_df = sub_df.copy()
    for column_name in ['config', 'group', 'instance']:
        normalized_sub_df[column_name] = normalized_sub_df[column_name].astype(str)
    normalized_sub_df['iteration'] = pd.to_numeric(normalized_sub_df['iteration'], errors='coerce')
    normalized_sub_df['day_key'] = normalized_sub_df['day'].map(_normalize_day_key)
    normalized_sub_df['total_request_number'] = pd.to_numeric(
        normalized_sub_df['total_request_number'],
        errors='coerce')
    normalized_sub_df = normalized_sub_df.dropna(subset=['iteration', 'total_request_number'])
    normalized_sub_df = normalized_sub_df[normalized_sub_df['day_key'].notna()].copy()

    subproblem_request_count_by_key = (
        normalized_sub_df
        .groupby(['config', 'group', 'instance', 'iteration', 'day_key'])['total_request_number']
        .mean()
        .to_dict()
    )

    rows: list[dict[str, str | float]] = []
    for (config_name, group_name, instance_name), master_rows in master_df.groupby(['config', 'group', 'instance']):
        result_dir = results_path.joinpath(f'{config_name}__{group_name}__{instance_name}')
        if not result_dir.exists():
            continue

        iteration_share_means: list[float] = []
        for iteration_value, iteration_rows in master_rows.groupby('iteration'):
            if pd.isna(iteration_value):
                continue

            iteration_index = int(iteration_value)
            iteration_path = result_dir.joinpath(f'iter_{iteration_index}')
            if not iteration_path.exists():
                continue

            core_file_prefix = _resolve_core_file_prefix(str(config_name), iteration_path, iteration_rows)
            if core_file_prefix is None:
                continue

            core_path = iteration_path.joinpath(f'{core_file_prefix}_cores.json')
            if not core_path.exists():
                continue

            with open(core_path, 'r') as file:
                cores = decode_cores(json.load(file))

            per_core_shares: list[float] = []
            for core in cores:
                day_key = _normalize_day_key(core.day)
                if day_key is None:
                    continue
                total_request_number = pd.to_numeric(
                    subproblem_request_count_by_key.get(
                        (str(config_name), str(group_name), str(instance_name), iteration_index, day_key),
                        np.nan),
                    errors='coerce')
                if pd.isna(total_request_number) or float(total_request_number) <= 1e-9:
                    continue
                per_core_shares.append(float(len(core.components)) / float(total_request_number))

            if len(per_core_shares) > 0:
                iteration_share_means.append(float(np.mean(per_core_shares)))

        rows.append({
            'config': str(config_name),
            'group': str(group_name),
            'instance': str(instance_name),
            'avg_core_size_share_per_iteration': (
                float(np.mean(iteration_share_means))
                if len(iteration_share_means) > 0 else np.nan),
        })

    return pd.DataFrame(rows)


def _build_core_generation_iteration_metrics(
        master_df: pd.DataFrame,
        sub_df: pd.DataFrame,
        results_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(master_df) == 0:
        return (
            pd.DataFrame(columns=[
                'config', 'group', 'instance', 'iteration',
                'iteration_position', 'iteration_progress', 'total_core_count',
            ]),
            pd.DataFrame(columns=[
                'config', 'group', 'instance', 'iteration',
                'day_key', 'day_core_count',
            ]),
        )

    normalized_sub_df = pd.DataFrame()
    subproblem_days_by_key: dict[tuple[str, str, str, int], list[int | str]] = {}
    if len(sub_df) > 0 and {'config', 'group', 'instance', 'iteration', 'day'}.issubset(sub_df.columns):
        normalized_sub_df = sub_df.copy()
        for column_name in ['config', 'group', 'instance']:
            normalized_sub_df[column_name] = normalized_sub_df[column_name].astype(str)
        normalized_sub_df['iteration'] = pd.to_numeric(normalized_sub_df['iteration'], errors='coerce')
        normalized_sub_df['day_key'] = normalized_sub_df['day'].map(_normalize_day_key)
        normalized_sub_df = normalized_sub_df.dropna(subset=['iteration'])
        normalized_sub_df = normalized_sub_df[normalized_sub_df['day_key'].notna()].copy()

        grouped_days = (
            normalized_sub_df
            .groupby(['config', 'group', 'instance', 'iteration'])['day_key']
            .apply(lambda series: sorted(set(series.tolist()), key=_sortable_day_key))
        )
        subproblem_days_by_key = {
            (str(config_name), str(group_name), str(instance_name), int(iteration_value)): list(day_values)
            for (config_name, group_name, instance_name, iteration_value), day_values in grouped_days.items()
        }

    iteration_rows: list[dict[str, str | float | int]] = []
    day_rows: list[dict[str, str | float | int]] = []

    for (config_name, group_name, instance_name), master_rows in master_df.groupby(['config', 'group', 'instance']):
        result_dir = results_path.joinpath(f'{config_name}__{group_name}__{instance_name}')
        if not result_dir.exists():
            continue

        normalized_master_rows = master_rows.copy()
        normalized_master_rows['iteration_numeric'] = pd.to_numeric(
            normalized_master_rows['iteration'],
            errors='coerce')
        normalized_master_rows = normalized_master_rows.dropna(subset=['iteration_numeric']).copy()
        if len(normalized_master_rows) == 0:
            continue
        normalized_master_rows['iteration_numeric'] = normalized_master_rows['iteration_numeric'].astype(int)
        normalized_master_rows = normalized_master_rows.sort_values('iteration_numeric')
        iteration_values = sorted(normalized_master_rows['iteration_numeric'].unique().tolist())
        total_iteration_count = len(iteration_values)

        for iteration_position, iteration_index in enumerate(iteration_values):
            iteration_rows_df = normalized_master_rows[
                normalized_master_rows['iteration_numeric'] == iteration_index
            ]
            iteration_path = result_dir.joinpath(f'iter_{iteration_index}')
            core_file_prefix = _resolve_core_file_prefix(
                str(config_name),
                iteration_path,
                iteration_rows_df)

            total_core_count = 0.0
            day_core_count_map: dict[int | str, int] = defaultdict(int)
            core_file_loaded = False
            if core_file_prefix is not None:
                core_path = iteration_path.joinpath(f'{core_file_prefix}_cores.json')
                if core_path.exists():
                    with open(core_path, 'r', encoding='utf-8') as file:
                        cores = decode_cores(json.load(file))
                    total_core_count = float(len(cores))
                    core_file_loaded = True
                    for core in cores:
                        day_key = _normalize_day_key(core.day)
                        if day_key is None:
                            continue
                        day_core_count_map[day_key] += 1

            if not core_file_loaded:
                fallback_core_counts = pd.to_numeric(
                    _pick_core_number_per_iteration(iteration_rows_df),
                    errors='coerce').dropna()
                if len(fallback_core_counts) > 0:
                    total_core_count = float(fallback_core_counts.iloc[-1])

            if total_iteration_count <= 1:
                iteration_progress = 1.0
            else:
                iteration_progress = float(iteration_position) / float(total_iteration_count - 1)

            iteration_rows.append({
                'config': str(config_name),
                'group': str(group_name),
                'instance': str(instance_name),
                'iteration': int(iteration_index),
                'iteration_position': int(iteration_position),
                'iteration_progress': float(iteration_progress),
                'total_core_count': float(total_core_count),
            })

            subproblem_days = subproblem_days_by_key.get(
                (str(config_name), str(group_name), str(instance_name), int(iteration_index)),
                [])
            if len(subproblem_days) == 0:
                considered_days = sorted(day_core_count_map.keys(), key=_sortable_day_key)
            else:
                considered_days = sorted(
                    set(subproblem_days) | set(day_core_count_map.keys()),
                    key=_sortable_day_key)

            if not core_file_loaded and float(total_core_count) > 0.0:
                continue

            for day_key in considered_days:
                day_rows.append({
                    'config': str(config_name),
                    'group': str(group_name),
                    'instance': str(instance_name),
                    'iteration': int(iteration_index),
                    'day_key': day_key,
                    'day_core_count': int(day_core_count_map.get(day_key, 0)),
                })

    return (
        pd.DataFrame(iteration_rows, columns=[
            'config', 'group', 'instance', 'iteration',
            'iteration_position', 'iteration_progress', 'total_core_count',
        ]),
        pd.DataFrame(day_rows, columns=[
            'config', 'group', 'instance', 'iteration',
            'day_key', 'day_core_count',
        ]),
    )


def _scan_result_config_names(results_path: Path) -> set[str]:
    config_names: set[str] = set()
    if not results_path.exists():
        return config_names
    for entry in results_path.iterdir():
        if not entry.is_dir() or entry.name in {'analysis', 'plots'}:
            continue
        tokens = entry.name.split('__')
        if len(tokens) != 3:
            continue
        config_names.add(tokens[0])
    return config_names


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
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        ylabel: str,
        title: str,
        save_path: Path,
        hide_labels_above_unit: bool = False):
    row_limits = []
    for row_layout in layout_rows:
        row_values = _flatten_numeric_values([
            data_by_pair.get(pair, [])
            for pair in row_layout['sorted_pairs']
        ])
        row_limits.append(_compute_shared_ylim(
            row_values,
            include_zero=False,
            lower_pad_frac=0.06,
            upper_pad_frac=0.12,
            min_positive_span=0.2))
    shared_ylim = _merge_ylim_bounds(row_limits)
    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=10.0, per_category=0.58), max(5.0, 3.7 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    for row_layout, ax in zip(layout_rows, normalized_axes):
        data = []
        positions = []
        colored_pairs: list[tuple[str, str]] = []
        sorted_pairs = row_layout['sorted_pairs']
        position_by_pair = row_layout['position_by_pair']

        for pair in sorted_pairs:
            values = [value for value in data_by_pair.get(pair, []) if pd.notna(value)]
            if len(values) == 0:
                continue
            data.append(values)
            positions.append(position_by_pair[pair])
            colored_pairs.append(pair)

        if len(data) > 0:
            boxplot_result = ax.boxplot(data, positions=positions, widths=0.40, patch_artist=True, showfliers=True)
            _style_boxplot_artists(boxplot_result, colored_pairs, pair_styles)

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel(ylabel)
        ax.grid(axis='y', alpha=0.25)
        if shared_ylim is not None:
            ax.set_ylim(*shared_ylim)
        if hide_labels_above_unit:
            _hide_labels_above_unit_ratio(ax)

    _finalize_grouped_figure(fig, normalized_axes, layout_rows, config_aliases, main_title=title)
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

    if BUBBLE_SIZE_SCALE_MODE == 'log1p':
        transformed = np.log1p(values)
    else:
        transformed = values.copy()
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
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=10.0, per_category=0.75), max(6.0, 4.7 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    row_limits: list[tuple[float, float] | None] = []
    duration_ratio_means: list[float] = []
    for row_layout in layout_rows:
        row_values: list[float] = []
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            duration_values = pd.to_numeric(chunk['scheduled_duration_over_capacity_ratio'], errors='coerce').dropna()
            if len(duration_values) == 0:
                continue
            pair_mean = float(duration_values.mean())
            row_values.append(pair_mean)
            duration_ratio_means.append(pair_mean)
        row_limits.append(_compute_shared_ylim(
            row_values,
            include_zero=False,
            lower_pad_frac=0.10,
            upper_pad_frac=0.34,
            min_positive_span=0.05))
    shared_ylim = _merge_ylim_bounds(row_limits)
    if shared_ylim is not None and len(duration_ratio_means) > 0:
        shared_ymin = float(shared_ylim[0])
        data_ymax = float(max(duration_ratio_means))
        adjusted_ymax = data_ymax + (data_ymax - shared_ymin) / 3.0
        if adjusted_ymax <= shared_ymin:
            adjusted_ymax = shared_ymin + 0.05
        shared_ylim = (shared_ymin, adjusted_ymax)

    for row_layout, ax in zip(layout_rows, normalized_axes):
        bubble_xs: list[float] = []
        bubble_ys: list[float] = []
        bubble_times: list[float] = []
        mean_gaps: list[float] = []
        mean_iterations: list[float] = []
        bubble_colors: list[str] = []
        bubble_edge_colors: list[str] = []

        sorted_pairs = row_layout['sorted_pairs']
        position_by_pair = row_layout['position_by_pair']
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

            style = _pair_style(pair_styles, pair)
            bubble_xs.append(position_by_pair[pair])
            bubble_ys.append(float(duration_values.mean()))
            bubble_times.append(float(total_times.sum()) if len(total_times) > 0 else 0.0)
            mean_gaps.append(float(gap_values.mean()) if len(gap_values) > 0 else np.nan)
            mean_iterations.append(float(iteration_values.mean()) if len(iteration_values) > 0 else np.nan)
            bubble_colors.append(style['facecolor'])
            bubble_edge_colors.append(style['edgecolor'])

        bubble_sizes = _scale_bubble_sizes(bubble_times, min_size=160.0, max_size=1800.0)
        if len(bubble_xs) > 0:
            ax.scatter(
                bubble_xs,
                bubble_ys,
                s=bubble_sizes,
                color=bubble_colors,
                alpha=0.52,
                edgecolors=bubble_edge_colors,
                linewidths=1.0)

            if shared_ylim is not None:
                ax.set_ylim(*shared_ylim)
                y_min, y_max = shared_ylim
            else:
                y_min = min(bubble_ys)
                y_max = max(bubble_ys)
            y_span = max(y_max - y_min, 0.01)
            label_offset = max(y_span * 0.08, 0.001)
            fig.canvas.draw()
            for x, y, mean_gap, mean_iteration, bubble_size, total_time, bubble_color in zip(
                    bubble_xs, bubble_ys, mean_gaps, mean_iterations, bubble_sizes, bubble_times, bubble_colors):
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
                    color=bubble_color)

        if shared_ylim is not None:
            ax.set_ylim(*shared_ylim)

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel('Mean scheduled duration / total capacity')
        ax.grid(axis='y', alpha=0.25)
        _hide_labels_above_unit_ratio(ax)

    _finalize_grouped_figure(
        fig,
        normalized_axes,
        layout_rows,
        config_aliases,
        main_title='Bubble summary: duration ratio (Y), tracked elapsed time (bubble size)',
        bottom=0.02)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_core_count_vs_core_size_bubble_aggregate(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=10.0, per_category=0.75), max(6.0, 4.7 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    row_limits: list[tuple[float, float] | None] = []
    for row_layout in layout_rows:
        row_values: list[float] = []
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            core_count_values = pd.to_numeric(chunk['avg_cores_per_iteration'], errors='coerce').dropna()
            if len(core_count_values) == 0:
                continue
            row_values.append(float(core_count_values.median()))
        row_limits.append(_compute_shared_ylim(
            row_values,
            include_zero=True,
            lower_pad_frac=0.16,
            upper_pad_frac=0.22,
            min_positive_span=1.0))
    shared_ylim = _merge_ylim_bounds(row_limits)

    for row_layout, ax in zip(layout_rows, normalized_axes):
        bubble_xs: list[float] = []
        bubble_ys: list[float] = []
        bubble_core_sizes: list[float] = []
        mean_iterations: list[float] = []
        bubble_colors: list[str] = []
        bubble_edge_colors: list[str] = []

        sorted_pairs = row_layout['sorted_pairs']
        position_by_pair = row_layout['position_by_pair']
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            if len(chunk) == 0:
                continue

            core_count_values = pd.to_numeric(chunk['avg_cores_per_iteration'], errors='coerce').dropna()
            core_size_values = pd.to_numeric(chunk['avg_core_size_share_per_iteration'], errors='coerce').dropna()
            iteration_values = pd.to_numeric(chunk['iteration_count'], errors='coerce').dropna()

            core_count_median = float(core_count_values.median()) if len(core_count_values) > 0 else 0.0
            core_size_median = float(core_size_values.median()) if len(core_size_values) > 0 else 0.0

            style = _pair_style(pair_styles, pair)
            bubble_xs.append(position_by_pair[pair])
            bubble_ys.append(core_count_median)
            bubble_core_sizes.append(core_size_median)
            mean_iterations.append(float(iteration_values.mean()) if len(iteration_values) > 0 else np.nan)
            bubble_colors.append(style['facecolor'])
            bubble_edge_colors.append(style['edgecolor'])

        bubble_sizes = _scale_bubble_sizes(bubble_core_sizes, min_size=160.0, max_size=1800.0)
        if len(bubble_xs) > 0:
            ax.scatter(
                bubble_xs,
                bubble_ys,
                s=bubble_sizes,
                color=bubble_colors,
                alpha=0.52,
                edgecolors=bubble_edge_colors,
                linewidths=1.0)

            if shared_ylim is not None:
                ax.set_ylim(*shared_ylim)
                y_min, y_max = shared_ylim
            else:
                y_min = min(bubble_ys)
                y_max = max(bubble_ys)
            fig.canvas.draw()
            for x, y, bubble_size, core_size_median, mean_iteration in zip(
                    bubble_xs, bubble_ys, bubble_sizes, bubble_core_sizes, mean_iterations):
                iteration_text = f'{mean_iteration:.1f}' if pd.notna(mean_iteration) else 'NA'
                ax.text(
                    x,
                    _bubble_top_text_y(ax, y, bubble_size, extra_points=8.0),
                    f'core/SP share m={core_size_median:.2f}\niter m={iteration_text}',
                    ha='center',
                    va='bottom',
                    fontsize=8)

        if shared_ylim is not None:
            ax.set_ylim(*shared_ylim)

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel('Median avg generated cores per iteration')
        ax.grid(axis='y', alpha=0.25)

    _finalize_grouped_figure(
        fig,
        normalized_axes,
        layout_rows,
        config_aliases,
        main_title='Bubble summary: generated cores (Y), core/SP share median (bubble size)',
        bottom=0.02)
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
            'Y=duration ratio, bubble size=tracked elapsed time')
        ax.grid(axis='y', alpha=0.25)
        _hide_labels_above_unit_ratio(ax)

        fig.tight_layout(rect=(0.0, 0.02, 1.0, 0.95))

        group_plot_path = _get_group_plot_save_path(results_path, str(config_name), str(group_name))
        fig.savefig(group_plot_path.joinpath('bubble_duration_ratio_group.png'))
        plt.close(fig)


def _group_style_from_pair_styles(
        group_name: str,
        pair_styles: dict[tuple[str, str], dict[str, str]]) -> dict[str, str]:
    for (config_name, candidate_group), style in pair_styles.items():
        _ = config_name
        if candidate_group == group_name:
            return style
    return {'facecolor': '#4C78A8', 'edgecolor': '#2f4f6f'}


def _build_group_marker_map(group_names: list[str]) -> dict[str, str]:
    ordered_groups = sorted(set(group_names), key=_group_complexity_key)
    return {
        group_name: GROUP_MARKER_CYCLE[index % len(GROUP_MARKER_CYCLE)]
        for index, group_name in enumerate(ordered_groups)
    }


def _select_sparse_iteration_ticks(iteration_values: list[int], max_tick_count: int = 16) -> list[int]:
    if len(iteration_values) <= max_tick_count:
        return list(iteration_values)
    step = max(1, int(np.ceil(len(iteration_values) / max_tick_count)))
    ticks = [int(iteration_values[index]) for index in range(0, len(iteration_values), step)]
    if int(iteration_values[-1]) not in ticks:
        ticks.append(int(iteration_values[-1]))
    return ticks


def _compress_group_profile_iterations(
        pair_iteration_df: pd.DataFrame,
        iteration_positions: list[int]) -> tuple[list[int], int]:
    if len(iteration_positions) <= GROUP_PROFILE_MAX_DISPLAYED_ITERATIONS:
        return list(iteration_positions), 1

    sample_step = max(
        GROUP_PROFILE_BASE_SAMPLE_STEP,
        int(np.ceil(len(iteration_positions) / GROUP_PROFILE_MAX_DISPLAYED_ITERATIONS)))
    kept_iterations: set[int] = {
        int(iteration_positions[0]),
        int(iteration_positions[-1]),
    }

    for position, iteration_index in enumerate(iteration_positions):
        if position % sample_step == 0:
            kept_iterations.add(int(iteration_index))

    normalized_df = pair_iteration_df[['instance', 'iteration', 'total_core_count']].copy()
    normalized_df['instance'] = normalized_df['instance'].astype(str)
    normalized_df['iteration'] = pd.to_numeric(normalized_df['iteration'], errors='coerce')
    normalized_df['total_core_count'] = pd.to_numeric(normalized_df['total_core_count'], errors='coerce')
    normalized_df = normalized_df.dropna(subset=['iteration', 'total_core_count']).copy()
    normalized_df['iteration'] = normalized_df['iteration'].astype(int)

    for _instance_name, instance_chunk in normalized_df.groupby('instance'):
        instance_chunk = instance_chunk.sort_values('iteration')
        previous_iteration: int | None = None
        previous_value: float | None = None
        for row in instance_chunk.itertuples(index=False):
            current_iteration = int(row.iteration)
            current_value = float(row.total_core_count)
            if previous_value is not None and not np.isclose(current_value, previous_value):
                if previous_iteration is not None:
                    kept_iterations.add(int(previous_iteration))
                kept_iterations.add(current_iteration)
            previous_iteration = current_iteration
            previous_value = current_value

    return sorted(kept_iterations), sample_step


def _plot_core_generation_group_profiles(
        iteration_detail_df: pd.DataFrame,
        day_detail_df: pd.DataFrame,
        results_path: Path):
    if len(iteration_detail_df) == 0:
        return

    for (config_name, group_name), chunk in iteration_detail_df.groupby(['config', 'group']):
        pair_iteration_df = chunk.copy()
        if len(pair_iteration_df) == 0:
            continue

        pair_iteration_df['instance'] = pair_iteration_df['instance'].astype(str)
        pair_iteration_df['_sort_key'] = pair_iteration_df['instance'].map(_instance_sort_key)
        pair_iteration_df = pair_iteration_df.sort_values(['_sort_key', 'iteration']).drop(columns=['_sort_key'])
        iteration_positions = sorted({
            int(value)
            for value in pd.to_numeric(pair_iteration_df['iteration'], errors='coerce').dropna().tolist()
        })
        if len(iteration_positions) == 0:
            continue

        displayed_iteration_positions, sample_step = _compress_group_profile_iterations(
            pair_iteration_df,
            iteration_positions)
        pair_iteration_df = pair_iteration_df[
            pd.to_numeric(pair_iteration_df['iteration'], errors='coerce').isin(displayed_iteration_positions)
        ].copy()

        if {'config', 'group', 'iteration', 'day_core_count'}.issubset(day_detail_df.columns):
            pair_day_df = day_detail_df[
                (day_detail_df['config'] == config_name) &
                (day_detail_df['group'] == group_name)
            ].copy()
            pair_day_df['iteration'] = pd.to_numeric(pair_day_df['iteration'], errors='coerce')
            pair_day_df = pair_day_df.dropna(subset=['iteration'])
            pair_day_df['iteration'] = pair_day_df['iteration'].astype(int)
            pair_day_df = pair_day_df[pair_day_df['iteration'].isin(displayed_iteration_positions)].copy()
        else:
            pair_day_df = pd.DataFrame(columns=['iteration', 'day_core_count'])

        fig, (ax_lines, ax_box) = plt.subplots(
            2, 1,
            figsize=(
                min(
                    GROUP_PROFILE_MAX_FIGURE_WIDTH,
                    max(9.0, 0.55 * len(displayed_iteration_positions) + 5.2)),
                7.6),
            sharex=True,
            gridspec_kw={'height_ratios': [1.0, 1.0]})

        instance_names = pair_iteration_df['instance'].drop_duplicates().tolist()
        line_colors = plt.cm.tab20(np.linspace(0.0, 1.0, max(len(instance_names), 1)))
        for color_index, instance_name in enumerate(instance_names):
            instance_chunk = pair_iteration_df[pair_iteration_df['instance'] == instance_name].sort_values('iteration')
            instance_chunk = instance_chunk.copy()
            instance_chunk['iteration'] = pd.to_numeric(instance_chunk['iteration'], errors='coerce')
            instance_chunk['total_core_count'] = pd.to_numeric(instance_chunk['total_core_count'], errors='coerce')
            instance_chunk = instance_chunk.dropna(subset=['iteration', 'total_core_count'])
            x_values = instance_chunk['iteration'].astype(int).tolist()
            y_values = instance_chunk['total_core_count'].astype(float).tolist()
            if len(x_values) == 0 or len(y_values) == 0:
                continue
            ax_lines.plot(
                x_values,
                y_values,
                marker='o',
                linewidth=1.4,
                markersize=3.8,
                alpha=0.86,
                color=line_colors[color_index],
                label=instance_name)

        line_ylim = _compute_shared_ylim(
            pd.to_numeric(pair_iteration_df['total_core_count'], errors='coerce').dropna().tolist(),
            include_zero=True,
            lower_pad_frac=0.0,
            upper_pad_frac=0.12,
            min_positive_span=1.0)
        if line_ylim is not None:
            ax_lines.set_ylim(*line_ylim)
        ax_lines.set_ylabel('Generated cores')
        ax_lines.grid(axis='y', alpha=0.25)
        ax_lines.yaxis.set_major_locator(MaxNLocator(integer=True))

        if len(instance_names) <= 12:
            ax_lines.legend(
                title='Instance',
                loc='upper left',
                bbox_to_anchor=(1.01, 1.0),
                borderaxespad=0.0,
                fontsize=8,
                title_fontsize=8)

        box_data: list[list[float]] = []
        box_positions: list[int] = []
        for iteration_index in iteration_positions:
            iteration_day_values = pd.to_numeric(
                pair_day_df[pair_day_df['iteration'] == iteration_index]['day_core_count'],
                errors='coerce').dropna().tolist()
            if len(iteration_day_values) == 0:
                continue
            box_positions.append(int(iteration_index))
            box_data.append([float(value) for value in iteration_day_values])

        if len(box_data) > 0:
            boxplot_result = ax_box.boxplot(
                box_data,
                positions=box_positions,
                widths=0.60,
                patch_artist=True,
                showfliers=True)
            for patch in boxplot_result.get('boxes', []):
                patch.set_facecolor('#8FB6E8')
                patch.set_edgecolor('#4C78A8')
                patch.set_alpha(0.88)
                patch.set_linewidth(1.0)
            for artist_group in ['whiskers', 'caps']:
                for artist in boxplot_result.get(artist_group, []):
                    artist.set_color('#4C78A8')
                    artist.set_linewidth(1.0)
            for median in boxplot_result.get('medians', []):
                median.set_color('#2f2f2f')
                median.set_linewidth(1.2)
            for flier in boxplot_result.get('fliers', []):
                flier.set_markeredgecolor('#4C78A8')
                flier.set_markerfacecolor('#8FB6E8')
                flier.set_alpha(0.65)
        else:
            ax_box.text(
                0.5, 0.5,
                'No per-day core data',
                transform=ax_box.transAxes,
                ha='center',
                va='center',
                fontsize=9,
                color='#555555')

        day_ylim = _compute_shared_ylim(
            pd.to_numeric(pair_day_df['day_core_count'], errors='coerce').dropna().tolist(),
            include_zero=True,
            lower_pad_frac=0.0,
            upper_pad_frac=0.12,
            min_positive_span=1.0)
        if day_ylim is not None:
            ax_box.set_ylim(*day_ylim)
        ax_box.set_ylabel('Cores per SP day')
        ax_box.set_xlabel('Iteration')
        ax_box.grid(axis='y', alpha=0.25)
        ax_box.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax_box.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax_box.set_xticks(_select_sparse_iteration_ticks(displayed_iteration_positions))

        compression_note = ''
        if len(displayed_iteration_positions) < len(iteration_positions):
            compression_note = (
                f'\nDisplayed iterations: every ~{sample_step} plus all core-count changes'
            )
        fig.suptitle(
            f'Core generation profile for {config_name} / {group_name}\n'
            'Top: total cores per iteration by instance; bottom: per-day core-count distribution by iteration'
            f'{compression_note}',
            y=0.985)
        fig.tight_layout(rect=(0.0, 0.02, 0.86 if len(instance_names) <= 12 else 1.0, 0.95))

        group_plot_path = _get_group_plot_save_path(results_path, str(config_name), str(group_name))
        fig.savefig(group_plot_path.joinpath('core_generation_profile_group.png'))
        plt.close(fig)


def _plot_core_generation_progress_scatter_aggregate(
        iteration_detail_df: pd.DataFrame,
        ordered_config_names: list[str],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    if len(iteration_detail_df) == 0:
        return

    plot_df = iteration_detail_df.copy()
    plot_df['iteration_progress'] = pd.to_numeric(plot_df['iteration_progress'], errors='coerce')
    plot_df['total_core_count'] = pd.to_numeric(plot_df['total_core_count'], errors='coerce')
    plot_df = plot_df.dropna(subset=['iteration_progress', 'total_core_count'])
    if len(plot_df) == 0:
        return

    present_configs = [config_name for config_name in ordered_config_names if config_name in set(plot_df['config'].astype(str))]
    for config_name in sorted(set(plot_df['config'].astype(str))):
        if config_name not in present_configs:
            present_configs.append(config_name)
    if len(present_configs) == 0:
        return

    group_names = sorted(set(plot_df['group'].astype(str)), key=_group_complexity_key)
    marker_map = _build_group_marker_map(group_names)
    group_style_map = {
        group_name: _group_style_from_pair_styles(group_name, pair_styles)
        for group_name in group_names
    }

    ncols = min(3, len(present_configs))
    nrows = int(np.ceil(len(present_configs) / ncols))
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(max(5.2 * ncols, 9.5), max(4.4 * nrows, 4.8)),
        squeeze=False,
        sharex=True,
        sharey=True)
    normalized_axes = _normalize_axes_list(axes)

    shared_ylim = _compute_shared_ylim(
        plot_df['total_core_count'].tolist(),
        include_zero=True,
        lower_pad_frac=0.0,
        upper_pad_frac=0.12,
        min_positive_span=1.0)

    legend_handles = []
    for axis_index, config_name in enumerate(present_configs):
        ax = normalized_axes[axis_index]
        config_chunk = plot_df[plot_df['config'].astype(str) == str(config_name)]
        for group_name in group_names:
            group_chunk = config_chunk[config_chunk['group'].astype(str) == str(group_name)]
            if len(group_chunk) == 0:
                continue
            style = group_style_map[group_name]
            scatter = ax.scatter(
                group_chunk['iteration_progress'],
                group_chunk['total_core_count'],
                s=44.0,
                marker=marker_map[group_name],
                color=style['facecolor'],
                edgecolors=style['edgecolor'],
                linewidths=0.9,
                alpha=0.78,
                label=group_name)
            if axis_index == 0:
                legend_handles.append((group_name, scatter))

        ax.set_title(config_aliases.get(config_name, config_name))
        ax.set_xlim(-0.02, 1.02)
        ax.set_xticks(np.linspace(0.0, 1.0, 5))
        ax.grid(alpha=0.25)
        if shared_ylim is not None:
            ax.set_ylim(*shared_ylim)
        if axis_index % ncols == 0:
            ax.set_ylabel('Generated cores')
        if axis_index >= (nrows - 1) * ncols:
            ax.set_xlabel('Normalized iteration progress')
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))

    for axis_index in range(len(present_configs), len(normalized_axes)):
        normalized_axes[axis_index].set_visible(False)

    if len(legend_handles) > 0:
        unique_handles = []
        seen_groups: set[str] = set()
        for group_name, scatter in legend_handles:
            if group_name in seen_groups:
                continue
            seen_groups.add(group_name)
            unique_handles.append((group_name, scatter))
        fig.legend(
            [handle for _, handle in unique_handles],
            [group_name for group_name, _ in unique_handles],
            title='Group',
            loc='upper center',
            ncol=min(4, max(len(unique_handles), 1)),
            bbox_to_anchor=(0.5, 0.985),
            fontsize=8,
            title_fontsize=8)

    fig.suptitle(
        'Core generation progress by normalized iteration\n'
        'Each point is one instance at one iteration; color + marker identify the group',
        y=0.995)
    fig.tight_layout(rect=(0.0, 0.02, 1.0, 0.92 if len(legend_handles) > 0 else 0.95))
    fig.savefig(save_path)
    plt.close(fig)


def _plot_total_time_performance_profiles(
        summary_df: pd.DataFrame,
        ordered_config_names: list[str],
        config_aliases: dict[str, str],
        save_path: Path):
    if len(summary_df) == 0:
        return

    plot_df = summary_df.copy()
    plot_df['config'] = plot_df['config'].astype(str)
    plot_df['group'] = plot_df['group'].astype(str)
    plot_df['instance'] = plot_df['instance'].astype(str)
    plot_df['total_solving_time'] = pd.to_numeric(plot_df['total_solving_time'], errors='coerce')
    plot_df['is_optimal'] = plot_df['is_optimal'].fillna(False).astype(bool)

    group_names = sorted(set(plot_df['group']), key=_group_complexity_key)
    if len(group_names) == 0:
        return

    patient_numbers = sorted({
        attributes['patient_number']
        for group_name in group_names
        for attributes in [_extract_group_attributes(group_name)]
        if attributes['patient_number'] is not None
    })
    care_unit_numbers = sorted({
        attributes['care_unit_number']
        for group_name in group_names
        for attributes in [_extract_group_attributes(group_name)]
        if attributes['care_unit_number'] is not None
    })
    if len(patient_numbers) == 0 or len(care_unit_numbers) == 0:
        return

    config_names = [name for name in ordered_config_names if name in set(plot_df['config'])]
    for config_name in sorted(set(plot_df['config'])):
        if config_name not in config_names:
            config_names.append(config_name)
    if len(config_names) == 0:
        return

    group_panel_data: dict[str, dict[str, object]] = {}
    finite_ratios_global: list[float] = []
    for group_name in group_names:
        group_chunk = plot_df[plot_df['group'] == group_name].copy()
        if len(group_chunk) == 0:
            continue

        instance_names = sorted(set(group_chunk['instance']), key=_instance_sort_key)
        ratios_by_config: dict[str, np.ndarray] = {}
        valid_instance_count = 0
        for instance_name in instance_names:
            instance_chunk = group_chunk[group_chunk['instance'] == instance_name]
            times_by_config: dict[str, float] = {}
            for config_name in config_names:
                config_chunk = instance_chunk[instance_chunk['config'] == config_name]
                if len(config_chunk) == 0:
                    times_by_config[config_name] = float('inf')
                    continue
                row = config_chunk.iloc[0]
                total_time = pd.to_numeric(row.get('total_solving_time', np.nan), errors='coerce')
                is_optimal = bool(row.get('is_optimal', False))
                times_by_config[config_name] = float(total_time) if is_optimal and pd.notna(total_time) and float(total_time) > 0.0 else float('inf')

            best_time = min(times_by_config.values())
            if not np.isfinite(best_time):
                continue
            valid_instance_count += 1
            for config_name, value in times_by_config.items():
                ratios_by_config.setdefault(config_name, []).append(float(value / best_time) if np.isfinite(value) else float('inf'))

        if valid_instance_count == 0:
            group_panel_data[group_name] = {
                'valid_instance_count': 0,
                'ratios_by_config': {},
            }
            continue

        normalized_ratios = {
            config_name: np.asarray(ratios_by_config.get(config_name, [float('inf')] * valid_instance_count), dtype=float)
            for config_name in config_names
        }
        for ratios in normalized_ratios.values():
            finite_ratios_global.extend([float(value) for value in ratios if np.isfinite(value)])

        group_panel_data[group_name] = {
            'valid_instance_count': valid_instance_count,
            'ratios_by_config': normalized_ratios,
        }

    if len(group_panel_data) == 0:
        return

    finite_ratios_global = [value for value in finite_ratios_global if value >= 1.0]
    max_ratio = max(finite_ratios_global) if len(finite_ratios_global) > 0 else 1.5
    tau_max = max(1.5, max_ratio * 1.05)
    tau_grid = np.geomspace(1.0, tau_max, 240)
    config_color_map = _build_config_color_map(config_names)

    fig, axes = plt.subplots(
        len(patient_numbers),
        len(care_unit_numbers),
        figsize=(max(4.6 * len(care_unit_numbers), 11.0), max(3.45 * len(patient_numbers), 6.6)),
        squeeze=False,
        sharex=True,
        sharey=True)

    for row_index, patient_number in enumerate(patient_numbers):
        for col_index, care_unit_number in enumerate(care_unit_numbers):
            ax = axes[row_index][col_index]
            matching_group_name = None
            for candidate_group_name in group_names:
                attributes = _extract_group_attributes(candidate_group_name)
                if attributes['patient_number'] == patient_number and attributes['care_unit_number'] == care_unit_number:
                    matching_group_name = candidate_group_name
                    break

            if row_index == 0:
                ax.set_title(f'{care_unit_number}cu', fontsize=14, pad=8)
            if col_index == 0:
                ax.set_ylabel(f'patients={patient_number}\nPerformance profile', fontsize=12)
            if row_index == len(patient_numbers) - 1:
                ax.set_xlabel('Performance factor on tracked elapsed time', fontsize=12)

            ax.set_xscale('log')
            ax.set_xlim(1.0, tau_max)
            ax.set_ylim(0.0, 1.02)
            ax.grid(alpha=0.25)
            ax.tick_params(axis='both', labelsize=10.5)
            ax.xaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0, 2.0, 5.0)))
            ax.xaxis.set_major_formatter(FuncFormatter(
                lambda value, _pos: f'{value:g}' if value >= 1.0 and value <= tau_max * 1.001 else ''))
            ax.xaxis.set_minor_formatter(NullFormatter())

            if matching_group_name is None:
                ax.set_visible(False)
                continue

            panel_data = group_panel_data.get(matching_group_name, {})
            valid_instance_count = int(panel_data.get('valid_instance_count', 0))
            ratios_by_config = panel_data.get('ratios_by_config', {})

            panel_title = _format_group_label_without_dimensions(
                matching_group_name,
                {'patient_number', 'care_unit_number'})
            if valid_instance_count == 0 or not isinstance(ratios_by_config, dict):
                ax.text(
                    0.5,
                    0.5,
                    f'{panel_title}\nNo optimally solved instances',
                    transform=ax.transAxes,
                    ha='center',
                    va='center',
                    fontsize=9,
                    color='#555555')
                continue

            for config_name in config_names:
                ratios = ratios_by_config.get(config_name)
                if ratios is None:
                    continue
                profile_values = np.array([
                    float(np.mean(ratios <= tau))
                    for tau in tau_grid
                ], dtype=float)
                ax.step(
                    tau_grid,
                    profile_values,
                    where='post',
                    color=config_color_map[config_name],
                    linewidth=2.2,
                    alpha=0.95)

            ax.text(
                0.98,
                0.03,
                f'{panel_title}\nvalid inst={valid_instance_count}',
                transform=ax.transAxes,
                ha='right',
                va='bottom',
                fontsize=10.2,
                bbox={
                    'boxstyle': 'round,pad=0.22',
                    'facecolor': 'white',
                    'edgecolor': '#d0d0d0',
                    'alpha': 0.88,
                })

    legend_handles = [
        Line2D([0], [0], color=config_color_map[config_name], lw=2.2, label=config_aliases.get(config_name, config_name))
        for config_name in config_names
    ]
    fig.legend(
        handles=legend_handles,
        loc='upper center',
        ncol=min(5, max(1, len(legend_handles))),
        bbox_to_anchor=(0.5, 0.952),
        fontsize=11,
        title='Method',
        title_fontsize=11,
        frameon=True)
    fig.suptitle(
        'Performance profiles on tracked elapsed time by instance group',
        y=0.988,
        fontsize=16,
        fontweight='bold')
    fig.text(
        0.5,
        0.967,
        'Rows = patient count, columns = care-unit count',
        ha='center',
        va='top',
        fontsize=12)
    fig.tight_layout(rect=(0.0, 0.02, 1.0, 0.885))
    fig.savefig(save_path)
    plt.close(fig)


def _plot_optimal_count_and_mean_gap(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    global_heights: list[int] = []
    for row_layout in layout_rows:
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            global_heights.append(int(chunk['is_optimal'].sum()))
    global_max_height = max(global_heights) if len(global_heights) > 0 else 0
    global_y_top = 1.0 if global_max_height <= 0 else (global_max_height + max(1.0, global_max_height * 0.25))
    zero_stub = 0.04 * global_y_top

    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=11.0, per_category=0.92), max(5.4, 4.1 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    for row_layout, ax in zip(layout_rows, normalized_axes):
        heights: list[int] = []
        mean_gaps = []
        sorted_pairs = row_layout['sorted_pairs']
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            heights.append(int(chunk['is_optimal'].sum()))
            mean_gaps.append(float(chunk['final_gap_pct'].dropna().mean()) if chunk['final_gap_pct'].dropna().shape[0] > 0 else np.nan)

        displayed_heights = [float(height) if height > 0 else zero_stub for height in heights]

        bar_colors = []
        edge_colors = []
        for pair, height in zip(sorted_pairs, heights):
            style = _pair_style(pair_styles, pair)
            bar_colors.append(style['facecolor'] if height > 0 else _blend_color(style['facecolor'], 1.18))
            edge_colors.append(style['edgecolor'])
        bars = ax.bar(
            row_layout['x_positions'],
            displayed_heights,
            width=0.42,
            color=bar_colors,
            edgecolor=edge_colors,
            linewidth=0.8)

        ax.set_ylim(0.0, global_y_top)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        label_offset = max(0.04 * global_y_top, 0.03)

        for bar, count, shown_height, mean_gap in zip(bars, heights, displayed_heights, mean_gaps):
            ax.text(
                bar.get_x() + bar.get_width() * 0.5,
                min(shown_height + 0.01 * global_y_top, global_y_top * 0.92),
                f'{count}',
                ha='center',
                va='bottom',
                fontsize=8,
                color='#1f2f40')
            if pd.notna(mean_gap):
                ax.text(
                    bar.get_x() + bar.get_width() * 0.5,
                    min(shown_height + label_offset, global_y_top * 0.98),
                    f'gap m={mean_gap:.2f}%',
                    ha='center',
                    va='bottom',
                    fontsize=8)

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel('Optimal instances count')
        ax.grid(axis='y', alpha=0.25)

    _finalize_grouped_figure(
        fig,
        normalized_axes,
        layout_rows,
        config_aliases,
        main_title='Optimal solved instances by test/group (label: mean final gap %)',
        top=0.93)
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_subproblem_totals(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        save_path: Path):
    global_total_max = 0.0
    for row_layout in layout_rows:
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            master_total = _safe_chunk_mean(chunk, 'total_master_time')
            sub_total = _safe_chunk_mean(chunk, 'total_subproblem_time')
            other_total = _safe_chunk_mean(chunk, 'total_other_tracked_time')
            global_total_max = max(global_total_max, master_total + sub_total + other_total)

    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=11.5, per_category=1.02), max(5.4, 4.2 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    for row_index, (row_layout, ax) in enumerate(zip(layout_rows, normalized_axes)):
        sorted_pairs = row_layout['sorted_pairs']
        master_totals = []
        sub_totals = []
        other_totals = []
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            master_totals.append(_safe_chunk_mean(chunk, 'total_master_time'))
            sub_totals.append(_safe_chunk_mean(chunk, 'total_subproblem_time'))
            other_totals.append(_safe_chunk_mean(chunk, 'total_other_tracked_time'))

        total_heights = [m + s + o for m, s, o in zip(master_totals, sub_totals, other_totals)]
        y_top = global_total_max * 1.20 if global_total_max > 0 else 1.0
        ax.set_ylim(0.0, y_top)

        bar_width = 0.42
        master_bars = ax.bar(
            row_layout['x_positions'],
            master_totals,
            width=bar_width,
            color='#4C78A8',
            label='MP mean time per instance')
        sub_bars = ax.bar(
            row_layout['x_positions'],
            sub_totals,
            width=bar_width,
            bottom=master_totals,
            color='#F58518',
            label='SP mean time per instance')
        other_bars = ax.bar(
            row_layout['x_positions'],
            other_totals,
            width=bar_width,
            bottom=[m + s for m, s in zip(master_totals, sub_totals)],
            color='#9EA3A8',
            label='Other tracked processing per instance')

        label_offset = max(0.025 * y_top, 1.2)
        min_inside = 0.08 * y_top
        min_inside_bar_width_px = 56.0
        fig.canvas.draw()
        for x, master_value, sub_value, other_value, total_value, master_bar, sub_bar, other_bar in zip(
                row_layout['x_positions'], master_totals, sub_totals, other_totals, total_heights, master_bars, sub_bars, other_bars):
            mp_label = f'MP {master_value:.1f}s'
            sp_label = f'SP {sub_value:.1f}s'
            other_label = f'Other {other_value:.1f}s'
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

            if other_value >= min_inside and bar_width_px >= min_inside_bar_width_px:
                ax.text(
                    other_bar.get_x() + other_bar.get_width() * 0.5,
                    master_value + sub_value + other_value * 0.5,
                    other_label,
                    ha='center',
                    va='center',
                    fontsize=8,
                    color='#1f2f40')
            elif other_value > 0.0:
                other_label_y = total_value + label_offset
                candidate_offsets = [mp_label_y]
                if sub_value < min_inside or bar_width_px < min_inside_bar_width_px:
                    candidate_offsets.append(sp_label_y)
                min_label_distance = label_offset * 1.35
                if len(candidate_offsets) > 0:
                    other_label_y = max(other_label_y, max(candidate_offsets) + min_label_distance)
                ax.text(
                    x,
                    other_label_y,
                    other_label,
                    ha='center',
                    va='bottom',
                    fontsize=8,
                    color='#1f2f40',
                    bbox={
                        'boxstyle': 'round,pad=0.16',
                        'facecolor': '#9EA3A8',
                        'alpha': 0.18,
                        'edgecolor': 'none',
                    })

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel('Mean time per instance (s)')
        if row_index == 0:
            ax.legend()
        ax.grid(axis='y', alpha=0.25)

    _finalize_grouped_figure(
        fig,
        normalized_axes,
        layout_rows,
        config_aliases,
        main_title='Mean tracked elapsed time per instance split by phase + other processing (stacked)')
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_subproblem_time_share_pct(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        save_path: Path):
    fig, axes = plt.subplots(
        len(layout_rows), 1,
        figsize=(_figure_width_for_layout_rows(layout_rows, base_width=11.5, per_category=1.02), max(5.4, 4.2 * len(layout_rows))),
        squeeze=False)
    normalized_axes = _normalize_axes_list(axes)

    for row_index, (row_layout, ax) in enumerate(zip(layout_rows, normalized_axes)):
        sorted_pairs = row_layout['sorted_pairs']
        master_totals = []
        sub_totals = []
        other_totals = []
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            master_totals.append(_safe_chunk_mean(chunk, 'total_master_time'))
            sub_totals.append(_safe_chunk_mean(chunk, 'total_subproblem_time'))
            other_totals.append(_safe_chunk_mean(chunk, 'total_other_tracked_time'))

        master_shares = []
        sub_shares = []
        other_shares = []
        for master_value, sub_value, other_value in zip(master_totals, sub_totals, other_totals):
            total_value = master_value + sub_value + other_value
            if total_value <= 0:
                master_shares.append(0.0)
                sub_shares.append(0.0)
                other_shares.append(0.0)
            else:
                master_shares.append(100.0 * master_value / total_value)
                sub_shares.append(100.0 * sub_value / total_value)
                other_shares.append(100.0 * other_value / total_value)

        bar_width = 0.42
        master_bars = ax.bar(
            row_layout['x_positions'],
            master_shares,
            width=bar_width,
            color='#4C78A8',
            label='MP share (%)')
        sub_bars = ax.bar(
            row_layout['x_positions'],
            sub_shares,
            width=bar_width,
            bottom=master_shares,
            color='#F58518',
            label='SP share (%)')
        other_bars = ax.bar(
            row_layout['x_positions'],
            other_shares,
            width=bar_width,
            bottom=[m + s for m, s in zip(master_shares, sub_shares)],
            color='#9EA3A8',
            label='Other tracked share (%)')

        label_offset = 2.6
        min_inside = 8.0
        min_inside_bar_width_px = 56.0
        # Leave enough headroom for up to three stacked outside labels above 100%.
        ax.set_ylim(0.0, 100.0 + label_offset * 4.8)
        fig.canvas.draw()
        for x, master_pct, sub_pct, other_pct, master_bar, sub_bar, other_bar in zip(
                row_layout['x_positions'], master_shares, sub_shares, other_shares, master_bars, sub_bars, other_bars):
            mp_label = f'MP {master_pct:.1f}%'
            sp_label = f'SP {sub_pct:.1f}%'
            other_label = f'Other {other_pct:.1f}%'
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

            if other_pct >= min_inside and bar_width_px >= min_inside_bar_width_px:
                ax.text(
                    other_bar.get_x() + other_bar.get_width() * 0.5,
                    master_pct + sub_pct + other_pct * 0.5,
                    other_label,
                    ha='center',
                    va='center',
                    fontsize=8,
                    color='#1f2f40')
            elif other_pct > 0.0:
                other_label_y = 100.0 + label_offset
                candidate_offsets = [mp_label_y]
                if sub_pct < min_inside or bar_width_px < min_inside_bar_width_px:
                    candidate_offsets.append(sp_label_y)
                min_label_distance = label_offset * 1.35
                if len(candidate_offsets) > 0:
                    other_label_y = max(other_label_y, max(candidate_offsets) + min_label_distance)
                ax.text(
                    x,
                    other_label_y,
                    other_label,
                    ha='center',
                    va='bottom',
                    fontsize=8,
                    color='#1f2f40',
                    bbox={
                        'boxstyle': 'round,pad=0.16',
                        'facecolor': '#9EA3A8',
                        'alpha': 0.18,
                        'edgecolor': 'none',
                    })

        _decorate_grouped_axis(
            ax,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax.set_ylabel('Share of tracked elapsed time (%)')
        if row_index == 0:
            ax.legend()
        ax.grid(axis='y', alpha=0.25)

    _finalize_grouped_figure(
        fig,
        normalized_axes,
        layout_rows,
        config_aliases,
        main_title='Tracked elapsed time split by phase + other processing (stacked 100%)')
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_mean_bar_and_subproblem_iteration_box(
        master_df: pd.DataFrame,
        sub_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    master_row_limits: list[tuple[float, float] | None] = []
    sub_row_limits: list[tuple[float, float] | None] = []
    for row_layout in layout_rows:
        row_master_means: list[float] = []
        row_sub_iteration_means: list[float] = []
        for pair in row_layout['sorted_pairs']:
            master_chunk = master_df[(master_df['config'] == pair[0]) & (master_df['group'] == pair[1])]
            if 'master_time' in master_chunk.columns:
                master_values = master_chunk['master_time'].dropna()
                if len(master_values) > 0:
                    row_master_means.append(float(master_values.mean()))

            sub_chunk = sub_df[(sub_df['config'] == pair[0]) & (sub_df['group'] == pair[1])]
            if len(sub_chunk) > 0 and 'time' in sub_chunk.columns and 'iteration' in sub_chunk.columns:
                per_iteration_means = sub_chunk.groupby('iteration')['time'].mean().dropna().tolist()
                row_sub_iteration_means.extend([float(value) for value in per_iteration_means])

        master_row_limits.append(_compute_shared_ylim(
            row_master_means,
            include_zero=True,
            lower_pad_frac=0.0,
            upper_pad_frac=0.12,
            min_positive_span=0.2))
        sub_row_limits.append(_compute_shared_ylim(
            row_sub_iteration_means,
            include_zero=True,
            lower_pad_frac=0.03,
            upper_pad_frac=0.10,
            min_positive_span=0.3))

    shared_master_ylim = _merge_ylim_bounds(master_row_limits)
    shared_sub_ylim = _merge_ylim_bounds(sub_row_limits)

    fig, axis_blocks = _create_axis_blocks(
        len(layout_rows),
        [1.0, 1.2],
        _figure_width_for_layout_rows(layout_rows, base_width=11.0, per_category=0.90),
        per_row_height=7.6)

    header_axes: list[plt.Axes] = []
    for row_layout, (ax_master, ax_sub) in zip(layout_rows, axis_blocks):
        header_axes.append(ax_master)
        sorted_pairs = row_layout['sorted_pairs']

        master_means = []
        for pair in sorted_pairs:
            chunk = master_df[(master_df['config'] == pair[0]) & (master_df['group'] == pair[1])]
            master_means.append(float(chunk['master_time'].dropna().mean()) if 'master_time' in chunk and chunk['master_time'].dropna().shape[0] > 0 else np.nan)
        bar_colors = [_pair_style(pair_styles, pair)['facecolor'] for pair in sorted_pairs]
        bar_edge_colors = [_pair_style(pair_styles, pair)['edgecolor'] for pair in sorted_pairs]
        ax_master.bar(
            row_layout['x_positions'],
            master_means,
            width=0.40,
            color=bar_colors,
            edgecolor=bar_edge_colors,
            linewidth=0.9)
        ax_master.set_ylabel('Mean master time / iteration (s)')
        ax_master.grid(axis='y', alpha=0.25)
        ax_master.tick_params(axis='x', which='both', labelbottom=False)
        _decorate_grouped_axis(
            ax_master,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=False)
        if shared_master_ylim is not None:
            ax_master.set_ylim(*shared_master_ylim)

        box_data = []
        colored_pairs: list[tuple[str, str]] = []
        for pair in sorted_pairs:
            chunk = sub_df[(sub_df['config'] == pair[0]) & (sub_df['group'] == pair[1])]
            if len(chunk) == 0 or 'time' not in chunk.columns or 'iteration' not in chunk.columns:
                continue
            per_iteration_means = chunk.groupby('iteration')['time'].mean().dropna().values.tolist()
            if len(per_iteration_means) == 0:
                continue
            box_data.append(per_iteration_means)
            colored_pairs.append(pair)

        if len(box_data) > 0:
            sub_positions = [row_layout['position_by_pair'][pair] for pair in colored_pairs]
            boxplot_result = ax_sub.boxplot(
                box_data,
                positions=sub_positions,
                widths=0.40,
                patch_artist=True,
                showfliers=True)
            _style_boxplot_artists(boxplot_result, colored_pairs, pair_styles)
        ax_sub.set_ylabel('Subproblem mean time by iteration (s)')
        ax_sub.grid(axis='y', alpha=0.25)
        if shared_sub_ylim is not None:
            ax_sub.set_ylim(*shared_sub_ylim)
        _decorate_grouped_axis(
            ax_sub,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))

    _finalize_grouped_figure(
        fig,
        header_axes,
        layout_rows,
        config_aliases,
        main_title='Master mean time per iteration (bar) and subproblem iteration-time distribution (box)')
    fig.savefig(save_path)
    plt.close(fig)


def _plot_timeout_feasible_and_mean_master_gap(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    timeout_row_limits: list[tuple[float, float] | None] = []
    gap_row_limits: list[tuple[float, float] | None] = []
    for row_layout in layout_rows:
        timeout_values: list[float] = []
        gap_values: list[float] = []
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            timeout_values.extend(pd.to_numeric(
                chunk['timeout_feasible_nonoptimal_iteration_count'],
                errors='coerce').dropna().tolist())
            gap_values.extend(pd.to_numeric(
                chunk['mean_master_gap_over_iterations'],
                errors='coerce').dropna().tolist())
        timeout_row_limits.append(_compute_shared_ylim(
            timeout_values,
            include_zero=True,
            lower_pad_frac=0.0,
            upper_pad_frac=0.12,
            min_positive_span=1.0))
        gap_row_limits.append(_compute_shared_ylim(
            gap_values,
            include_zero=True,
            lower_pad_frac=0.04,
            upper_pad_frac=0.10,
            min_positive_span=0.02))

    shared_timeout_ylim = _merge_ylim_bounds(timeout_row_limits)
    shared_gap_ylim = _merge_ylim_bounds(gap_row_limits)

    fig, axis_blocks = _create_axis_blocks(
        len(layout_rows),
        [1.0, 1.0],
        _figure_width_for_layout_rows(layout_rows, base_width=10.0, per_category=0.72),
        per_row_height=7.8)

    header_axes: list[plt.Axes] = []
    for row_layout, (ax_timeout, ax_gap) in zip(layout_rows, axis_blocks):
        header_axes.append(ax_timeout)
        timeout_data = []
        timeout_positions = []
        timeout_pairs: list[tuple[str, str]] = []
        mean_gap_data = []
        mean_gap_positions = []
        mean_gap_pairs: list[tuple[str, str]] = []

        sorted_pairs = row_layout['sorted_pairs']
        position_by_pair = row_layout['position_by_pair']
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]

            timeout_values = pd.to_numeric(
                chunk['timeout_feasible_nonoptimal_iteration_count'],
                errors='coerce').dropna().values.tolist()
            if len(timeout_values) > 0:
                timeout_data.append(timeout_values)
                timeout_positions.append(position_by_pair[pair])
                timeout_pairs.append(pair)

            mean_gap_values = pd.to_numeric(
                chunk['mean_master_gap_over_iterations'],
                errors='coerce').dropna().values.tolist()
            if len(mean_gap_values) > 0:
                mean_gap_data.append(mean_gap_values)
                mean_gap_positions.append(position_by_pair[pair])
                mean_gap_pairs.append(pair)

        if len(timeout_data) > 0:
            timeout_boxplot = ax_timeout.boxplot(
                timeout_data,
                positions=timeout_positions,
                widths=0.40,
                patch_artist=True,
                showfliers=True)
            _style_boxplot_artists(timeout_boxplot, timeout_pairs, pair_styles)

        if len(mean_gap_data) > 0:
            gap_boxplot = ax_gap.boxplot(
                mean_gap_data,
                positions=mean_gap_positions,
                widths=0.40,
                patch_artist=True,
                showfliers=True)
            _style_boxplot_artists(gap_boxplot, mean_gap_pairs, pair_styles)

        for ax in [ax_timeout, ax_gap]:
            for separator_x in row_layout['separators']:
                ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)
            ax.grid(axis='y', alpha=0.25)

        ax_timeout.set_ylabel('Timeout + feasible non-opt\niterations per instance')
        ax_timeout.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax_timeout.tick_params(axis='x', which='both', labelbottom=False)
        if shared_timeout_ylim is not None:
            ax_timeout.set_ylim(*shared_timeout_ylim)

        _decorate_grouped_axis(
            ax_gap,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax_gap.set_ylabel('Mean master gap\nacross iterations (%)')
        if shared_gap_ylim is not None:
            ax_gap.set_ylim(*shared_gap_ylim)

    _finalize_grouped_figure(
        fig,
        header_axes,
        layout_rows,
        config_aliases,
        main_title='Master timeout/non-opt iterations and mean master gap by test/group')
    fig.savefig(save_path)
    plt.close(fig)


def _plot_master_request_grouping_boxplots(
        summary_df: pd.DataFrame,
        layout_rows: list[dict[str, object]],
        config_aliases: dict[str, str],
        pair_styles: dict[tuple[str, str], dict[str, str]],
        save_path: Path):
    multi_row_limits: list[tuple[float, float] | None] = []
    group_row_limits: list[tuple[float, float] | None] = []
    for row_layout in layout_rows:
        multi_values: list[float] = []
        group_values: list[float] = []
        for pair in row_layout['sorted_pairs']:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]
            multi_values.extend(pd.to_numeric(
                chunk['mean_multi_request_patients_per_iteration'],
                errors='coerce').dropna().tolist())
            group_values.extend(pd.to_numeric(
                chunk['mean_patient_day_group_size'],
                errors='coerce').dropna().tolist())
        multi_row_limits.append(_compute_shared_ylim(
            multi_values,
            include_zero=True,
            lower_pad_frac=0.0,
            upper_pad_frac=0.12,
            min_positive_span=1.0))
        group_row_limits.append(_compute_shared_ylim(
            group_values,
            include_zero=True,
            lower_pad_frac=0.03,
            upper_pad_frac=0.10,
            min_positive_span=0.2))

    shared_multi_ylim = _merge_ylim_bounds(multi_row_limits)
    shared_group_ylim = _merge_ylim_bounds(group_row_limits)

    fig, axis_blocks = _create_axis_blocks(
        len(layout_rows),
        [1.0, 1.0],
        _figure_width_for_layout_rows(layout_rows, base_width=10.0, per_category=0.72),
        per_row_height=8.0)

    header_axes: list[plt.Axes] = []
    for row_layout, (ax_multi, ax_group) in zip(layout_rows, axis_blocks):
        header_axes.append(ax_multi)
        multi_request_data = []
        multi_request_positions = []
        multi_request_pairs: list[tuple[str, str]] = []
        aggregated_request_data = []
        aggregated_request_positions = []
        aggregated_request_pairs: list[tuple[str, str]] = []

        sorted_pairs = row_layout['sorted_pairs']
        position_by_pair = row_layout['position_by_pair']
        for pair in sorted_pairs:
            chunk = summary_df[(summary_df['config'] == pair[0]) & (summary_df['group'] == pair[1])]

            multi_values = pd.to_numeric(
                chunk['mean_multi_request_patients_per_iteration'],
                errors='coerce').dropna().values.tolist()
            if len(multi_values) > 0:
                multi_request_data.append(multi_values)
                multi_request_positions.append(position_by_pair[pair])
                multi_request_pairs.append(pair)

            aggregated_values = pd.to_numeric(
                chunk['mean_patient_day_group_size'],
                errors='coerce').dropna().values.tolist()
            if len(aggregated_values) > 0:
                aggregated_request_data.append(aggregated_values)
                aggregated_request_positions.append(position_by_pair[pair])
                aggregated_request_pairs.append(pair)

        if len(multi_request_data) > 0:
            multi_boxplot = ax_multi.boxplot(
                multi_request_data,
                positions=multi_request_positions,
                widths=0.40,
                patch_artist=True,
                showfliers=True)
            _style_boxplot_artists(multi_boxplot, multi_request_pairs, pair_styles)

        if len(aggregated_request_data) > 0:
            aggregated_boxplot = ax_group.boxplot(
                aggregated_request_data,
                positions=aggregated_request_positions,
                widths=0.40,
                patch_artist=True,
                showfliers=True)
            _style_boxplot_artists(aggregated_boxplot, aggregated_request_pairs, pair_styles)

        for ax in [ax_multi, ax_group]:
            for separator_x in row_layout['separators']:
                ax.axvline(separator_x, color='gray', linestyle='--', linewidth=0.7, alpha=0.7)
            ax.grid(axis='y', alpha=0.25)

        ax_multi.set_ylabel('Mean #patients/iteration\nwith >1 requests on same day')
        ax_multi.tick_params(axis='x', which='both', labelbottom=False)
        if shared_multi_ylim is not None:
            ax_multi.set_ylim(*shared_multi_ylim)

        _decorate_grouped_axis(
            ax_group,
            row_layout['x_positions'],
            row_layout['compact_x_labels'],
            row_layout['config_spans'],
            row_layout['separators'],
            config_aliases,
            show_x_labels=bool(row_layout.get('show_x_labels', True)))
        ax_group.set_ylabel('Mean requests per\npatient-day group')
        if shared_group_ylim is not None:
            ax_group.set_ylim(*shared_group_ylim)

    _finalize_grouped_figure(
        fig,
        header_axes,
        layout_rows,
        config_aliases,
        main_title='Master same-day request concentration metrics by test/group')
    fig.savefig(save_path)
    plt.close(fig)


def plot_experiment_group_comparison(
        master_result_df: pd.DataFrame,
        subproblem_result_df: pd.DataFrame,
        results_path: Path,
        config,
        analysis_path: Path | None = None,
        selected_comparison_plots: set[str] | None = None,
        selected_group_plots: set[str] | None = None):
    selected_comparison_plots = set(selected_comparison_plots or [])
    selected_group_plots = set(selected_group_plots or [])
    if len(selected_comparison_plots) == 0 and len(selected_group_plots) == 0:
        return

    if analysis_path is None:
        analysis_path = results_path.joinpath('analysis')

    raw_instance_df = pd.DataFrame()
    instance_analysis_path = analysis_path.joinpath('instance_analysis.xlsx')
    if instance_analysis_path.exists():
        try:
            usecols = lambda name: name in {
                'config',
                'group',
                'instance',
                'status',
                'status_reason',
                'final_gap_pct',
                'run_total_time_elapsed',
            }
            try:
                raw_instance_df = pd.read_excel(
                    instance_analysis_path,
                    sheet_name='Instance data',
                    usecols=usecols)
            except ValueError:
                raw_instance_df = pd.read_excel(
                    instance_analysis_path,
                    sheet_name='Master instance data',
                    usecols=usecols)
        except Exception as exc:
            print(f'WARNING: unable to read instance_analysis.xlsx for experiment comparison: {exc}')

    if len(selected_comparison_plots) > 0:
        comparison_master_df = _filter_experiment_comparison_rows(master_result_df, config)
        comparison_sub_df = _filter_experiment_comparison_rows(subproblem_result_df, config)
        comparison_instance_df = _filter_experiment_comparison_rows(raw_instance_df, config)

        if len(comparison_master_df) == 0:
            print('No master rows after filters for experiment-comparison plots.')
        else:
            comparison_summary_df = _instance_summary(comparison_master_df, comparison_sub_df, comparison_instance_df)
            if len(comparison_summary_df) == 0:
                print('No instance summary rows available for experiment-comparison plots.')
            else:
                core_size_share_df = _build_core_size_share_metrics(comparison_master_df, comparison_sub_df, results_path)
                if len(core_size_share_df) > 0:
                    comparison_summary_df = comparison_summary_df.merge(
                        core_size_share_df,
                        on=['config', 'group', 'instance'],
                        how='left')
                else:
                    comparison_summary_df['avg_core_size_share_per_iteration'] = np.nan

                raw_selected_configs = config.get('experiment_group_comparison_configs_to_do') if isinstance(config, dict) else None
                if isinstance(raw_selected_configs, list):
                    selected_configs = [str(name).strip() for name in raw_selected_configs if str(name).strip() != '']
                    if len(selected_configs) > 0 and 'all' not in {name.lower() for name in selected_configs}:
                        present_summary_configs = set(comparison_summary_df['config'].astype(str))
                        missing_configs = [name for name in selected_configs if name not in present_summary_configs]
                        if len(missing_configs) > 0:
                            available_result_configs = _scan_result_config_names(results_path)
                            missing_in_analysis = [name for name in missing_configs if name in available_result_configs]
                            if len(missing_in_analysis) > 0:
                                print(
                                    'WARNING: some selected comparison configs are present in the results root but missing from the '
                                    f'loaded analysis data: {", ".join(missing_in_analysis)}. '
                                    'This usually means the analysis is stale or was generated with narrower filters.')

                request_grouping_df = _build_master_request_grouping_metrics(comparison_master_df, results_path)
                if len(request_grouping_df) > 0:
                    comparison_summary_df = comparison_summary_df.merge(
                        request_grouping_df,
                        on=['config', 'group', 'instance'],
                        how='left')
                else:
                    comparison_summary_df['mean_multi_request_patients_per_iteration'] = np.nan
                    comparison_summary_df['mean_patient_day_group_size'] = np.nan

                comparison_core_iteration_detail_df, _ = _build_core_generation_iteration_metrics(
                    comparison_master_df,
                    comparison_sub_df,
                    results_path)
                optimal_scatter_df = comparison_core_iteration_detail_df
                if len(comparison_core_iteration_detail_df) > 0:
                    optimal_instance_df = comparison_summary_df[
                        comparison_summary_df['is_optimal'] == True
                    ][['config', 'group', 'instance']].drop_duplicates()
                    optimal_scatter_df = comparison_core_iteration_detail_df.merge(
                        optimal_instance_df,
                        on=['config', 'group', 'instance'],
                        how='inner')

                pairs = list({(str(row['config']), str(row['group'])) for _, row in comparison_summary_df.iterrows()})
                present_configs = sorted({pair[0] for pair in pairs})
                ordered_config_names = _resolve_config_order(present_configs, config)
                config_aliases = _resolve_config_aliases(config)
                group_rank_map = _resolve_group_rank_map(config)
                row_split_priority = _resolve_row_split_priority(config)
                pair_styles = _build_pair_style_map(pairs)
                layout_rows = _build_grouped_layout_rows(
                    pairs,
                    ordered_config_names,
                    group_rank_map,
                    row_split_priority,
                    config_aliases)

                save_path = _get_experiment_comparison_save_path(results_path, config)

                data_iteration_count: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_avg_cores: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_total_time: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_gap_pct: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_final_objective_value: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_duration_ratio: dict[tuple[str, str], list[float]] = defaultdict(list)
                data_scheduled_ratio: dict[tuple[str, str], list[float]] = defaultdict(list)

                for _, row in comparison_summary_df.iterrows():
                    pair = (str(row['config']), str(row['group']))
                    data_iteration_count[pair].append(float(row['iteration_count']))
                    data_avg_cores[pair].append(float(row['avg_cores_per_iteration']))
                    data_total_time[pair].append(float(row['total_solving_time']))
                    data_gap_pct[pair].append(float(row['final_gap_pct']))
                    data_final_objective_value[pair].append(float(row['final_objective_value']))
                    data_duration_ratio[pair].append(float(row['scheduled_duration_over_capacity_ratio']))
                    data_scheduled_ratio[pair].append(float(row['scheduled_number_over_total_ratio']))

                if 'comparison_box_lbbd_iterations' in selected_comparison_plots:
                    _save_boxplot(
                        data_iteration_count,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='LBBD iterations',
                        title='LBBD iterations distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_lbbd_iterations.png'))

                if 'comparison_box_avg_cores_per_iteration' in selected_comparison_plots:
                    _save_boxplot(
                        data_avg_cores,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Average generated cores per iteration',
                        title='Average generated cores per iteration (instance distribution)',
                        save_path=save_path.joinpath('comparison_box_avg_cores_per_iteration.png'))

                if 'comparison_box_total_solving_time' in selected_comparison_plots:
                    _save_boxplot(
                        data_total_time,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Tracked elapsed time (s)',
                        title='Tracked elapsed time distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_total_solving_time.png'))

                if 'comparison_performance_profile_total_time' in selected_comparison_plots:
                    _plot_total_time_performance_profiles(
                        comparison_summary_df,
                        ordered_config_names,
                        config_aliases,
                        save_path=save_path.joinpath('comparison_performance_profile_total_time.png'))

                if 'comparison_box_final_gap_pct' in selected_comparison_plots:
                    _save_boxplot(
                        data_gap_pct,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Final gap % (master upper bound vs final feasible)',
                        title='Final gap % distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_final_gap_pct.png'))

                if 'comparison_box_final_objective_value' in selected_comparison_plots:
                    _save_boxplot(
                        data_final_objective_value,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Final objective value',
                        title='Final objective value distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_final_objective_value.png'))

                if 'comparison_box_scheduled_duration_over_capacity_ratio' in selected_comparison_plots:
                    _save_boxplot(
                        data_duration_ratio,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Scheduled duration / total capacity',
                        title='Satisfied duration ratio distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_scheduled_duration_over_capacity_ratio.png'),
                        hide_labels_above_unit=True)

                if 'comparison_box_scheduled_services_ratio' in selected_comparison_plots:
                    _save_boxplot(
                        data_scheduled_ratio,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        ylabel='Scheduled services / total services',
                        title='Scheduled services ratio distribution by test/group',
                        save_path=save_path.joinpath('comparison_box_scheduled_services_ratio.png'))

                if 'comparison_bubble_duration_ratio_summary' in selected_comparison_plots:
                    _plot_duration_ratio_bubble_aggregate(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_bubble_duration_ratio_summary.png'))

                if 'comparison_bubble_core_count_vs_core_size' in selected_comparison_plots:
                    _plot_core_count_vs_core_size_bubble_aggregate(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_bubble_core_count_vs_core_size.png'))

                if 'comparison_scatter_core_generation_progress' in selected_comparison_plots:
                    if len(optimal_scatter_df) == 0:
                        print('No optimal instances available for comparison_scatter_core_generation_progress.')
                    else:
                        _plot_core_generation_progress_scatter_aggregate(
                            optimal_scatter_df,
                            ordered_config_names,
                            config_aliases,
                            pair_styles,
                            save_path=save_path.joinpath('comparison_scatter_core_generation_progress.png'))

                if 'comparison_bar_optimal_count_with_mean_gap' in selected_comparison_plots:
                    _plot_optimal_count_and_mean_gap(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_bar_optimal_count_with_mean_gap.png'))

                if 'comparison_bar_master_vs_subproblem_total_time' in selected_comparison_plots:
                    _plot_master_subproblem_totals(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        save_path=save_path.joinpath('comparison_bar_master_vs_subproblem_total_time.png'))

                if 'comparison_bar_master_vs_subproblem_time_share_pct' in selected_comparison_plots:
                    _plot_master_subproblem_time_share_pct(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        save_path=save_path.joinpath('comparison_bar_master_vs_subproblem_time_share_pct.png'))

                if 'comparison_master_bar_and_subproblem_iteration_box' in selected_comparison_plots:
                    _plot_master_mean_bar_and_subproblem_iteration_box(
                        comparison_master_df,
                        comparison_sub_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_master_bar_and_subproblem_iteration_box.png'))

                if 'comparison_master_timeout_feasible_and_mean_gap_boxplots' in selected_comparison_plots:
                    _plot_timeout_feasible_and_mean_master_gap(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_master_timeout_feasible_and_mean_gap_boxplots.png'))

                if 'comparison_master_same_day_request_grouping_boxplots' in selected_comparison_plots:
                    _plot_master_request_grouping_boxplots(
                        comparison_summary_df,
                        layout_rows,
                        config_aliases,
                        pair_styles,
                        save_path=save_path.joinpath('comparison_master_same_day_request_grouping_boxplots.png'))

    if len(selected_group_plots) > 0:
        group_master_df = _filter_rows(master_result_df, config)
        group_sub_df = _filter_rows(subproblem_result_df, config)
        group_instance_df = _filter_rows(raw_instance_df, config)

        if len(group_master_df) == 0:
            print('No master rows after filters for group-level experiment plots.')
        else:
            group_summary_df = _instance_summary(group_master_df, group_sub_df, group_instance_df)
            group_core_iteration_detail_df, group_core_day_detail_df = _build_core_generation_iteration_metrics(
                group_master_df,
                group_sub_df,
                results_path)

            if 'bubble_duration_ratio_group' in selected_group_plots and len(group_summary_df) > 0:
                _plot_duration_ratio_bubble_per_group(
                    group_summary_df,
                    results_path)

            if 'core_generation_profile_group' in selected_group_plots and len(group_core_iteration_detail_df) > 0:
                _plot_core_generation_group_profiles(
                    group_core_iteration_detail_df,
                    group_core_day_detail_df,
                    results_path)
