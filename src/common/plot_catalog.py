from __future__ import annotations

from typing import TypedDict


class ResultPlotSpec(TypedDict, total=False):
    key: str
    level: str
    display_name: str
    short_description: str
    default_enabled: bool
    family: str
    visible: bool


RESULT_PLOT_LEVELS = [
    'comparison',
    'group',
    'run',
]

LEGACY_EXPERIMENT_COMPARISON_KEY = 'experiment_group_comparison'


RESULT_PLOT_SPECS: list[ResultPlotSpec] = [
    {
        'key': 'comparison_box_lbbd_iterations',
        'level': 'comparison',
        'display_name': 'LBBD iterations',
        'short_description': 'Distribution of total LBBD iterations across instances.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_avg_cores_per_iteration',
        'level': 'comparison',
        'display_name': 'Average cores / iteration',
        'short_description': 'Distribution of average generated cores per iteration across instances.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_total_solving_time',
        'level': 'comparison',
        'display_name': 'Tracked elapsed time',
        'short_description': 'Distribution of tracked total elapsed time across instances.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_performance_profile_total_time',
        'level': 'comparison',
        'display_name': 'Performance profile: tracked time',
        'short_description': 'Per-group performance profiles on tracked total time, arranged by patients x care units.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_final_gap_pct',
        'level': 'comparison',
        'display_name': 'Final gap %',
        'short_description': 'Distribution of final LBBD gap percentage across instances.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_final_objective_value',
        'level': 'comparison',
        'display_name': 'Final objective value',
        'short_description': 'Distribution of final objective values across instances.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_scheduled_duration_over_capacity_ratio',
        'level': 'comparison',
        'display_name': 'Scheduled duration / capacity',
        'short_description': 'Distribution of scheduled-duration saturation ratio.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_box_scheduled_services_ratio',
        'level': 'comparison',
        'display_name': 'Scheduled services ratio',
        'short_description': 'Distribution of scheduled-services share over total services.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_bubble_duration_ratio_summary',
        'level': 'comparison',
        'display_name': 'Bubble: duration ratio vs elapsed time',
        'short_description': 'Summary bubble plot with duration ratio on Y and tracked elapsed time as bubble size.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_bubble_core_count_vs_core_size',
        'level': 'comparison',
        'display_name': 'Bubble: cores vs core/SP share',
        'short_description': 'Summary bubble plot with generated cores on Y and core/SP share as bubble size.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_scatter_core_generation_progress',
        'level': 'comparison',
        'display_name': 'Scatter: core generation progress',
        'short_description': 'Normalized-iteration scatter of generated cores, split by test and styled by group.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_bar_optimal_count_with_mean_gap',
        'level': 'comparison',
        'display_name': 'Optimal count + mean gap',
        'short_description': 'Optimal instance counts with mean final gap annotation.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_bar_master_vs_subproblem_total_time',
        'level': 'comparison',
        'display_name': 'MP vs SP vs other time',
        'short_description': 'Mean tracked elapsed time split between master, subproblems and other tracked processing.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_bar_master_vs_subproblem_time_share_pct',
        'level': 'comparison',
        'display_name': 'MP vs SP vs other share %',
        'short_description': 'Percentage split of tracked elapsed time between master, subproblems and other tracked processing.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_master_bar_and_subproblem_iteration_box',
        'level': 'comparison',
        'display_name': 'Master bar + SP iteration box',
        'short_description': 'Master mean iteration time with subproblem iteration-time distribution.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_master_timeout_feasible_and_mean_gap_boxplots',
        'level': 'comparison',
        'display_name': 'Master timeout + mean gap',
        'short_description': 'Timeout/non-opt iteration counts and mean master gap across iterations.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'comparison_master_same_day_request_grouping_boxplots',
        'level': 'comparison',
        'display_name': 'Same-day request grouping',
        'short_description': 'Same-day concentration metrics for master requests.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'aggregate_best_solution_value',
        'level': 'comparison',
        'display_name': 'Aggregate best solution value',
        'short_description': 'Incomplete plot kept only for legacy compatibility.',
        'default_enabled': False,
        'visible': False,
    },
    {
        'key': 'bubble_duration_ratio_group',
        'level': 'group',
        'display_name': 'Group bubble: duration ratio',
        'short_description': 'Per-group bubble detail across instances with duration ratio, tracked elapsed time, gap and iterations.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'core_generation_profile_group',
        'level': 'group',
        'display_name': 'Group core-generation profile',
        'short_description': 'Per-group figure with instance curves of generated cores and per-day core-count boxplots.',
        'default_enabled': False,
        'family': LEGACY_EXPERIMENT_COMPARISON_KEY,
        'visible': True,
    },
    {
        'key': 'best_instance',
        'level': 'run',
        'display_name': 'Best final result',
        'short_description': 'Compact final-result schedule for the best solution of each run.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'best_instance_subproblems',
        'level': 'run',
        'display_name': 'Best subproblem gantt',
        'short_description': 'Daily gantt views of the best final result.',
        'default_enabled': False,
        'visible': True,
    },
    {
        'key': 'core_gantt',
        'level': 'run',
        'display_name': 'Core gantt',
        'short_description': 'Gantt plots for the generated cores of each run iteration.',
        'default_enabled': False,
        'visible': True,
    },
    {
        'key': 'result_value_vs_time',
        'level': 'run',
        'display_name': 'Result value vs time',
        'short_description': 'Evolution of master/final solution value over solving time.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'core_info',
        'level': 'run',
        'display_name': 'Core info',
        'short_description': 'Iteration-by-iteration statistics about generated cores.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'solving_times',
        'level': 'run',
        'display_name': 'Solving times',
        'short_description': 'Per-iteration solving times for master, cache and subproblems.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'solving_times_by_day',
        'level': 'run',
        'display_name': 'Solving times by day',
        'short_description': 'Day-by-iteration heatmap of subproblem solving times.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'requests_per_patient',
        'level': 'run',
        'display_name': 'Requests per patient',
        'short_description': 'Evolution of request and resource concentration per patient.',
        'default_enabled': True,
        'visible': True,
    },
    {
        'key': 'equal_requests_between_iterations',
        'level': 'run',
        'display_name': 'Stable requests between iterations',
        'short_description': 'Tracks how many scheduled requests stay equal between consecutive iterations.',
        'default_enabled': True,
        'visible': True,
    },
]


RESULT_PLOT_SPECS_BY_KEY = {
    spec['key']: spec
    for spec in RESULT_PLOT_SPECS
}

RESULT_PLOT_KEYS_BY_LEVEL = {
    level: [
        spec['key']
        for spec in RESULT_PLOT_SPECS
        if spec['level'] == level
    ]
    for level in RESULT_PLOT_LEVELS
}

VISIBLE_RESULT_PLOT_KEYS_BY_LEVEL = {
    level: [
        spec['key']
        for spec in RESULT_PLOT_SPECS
        if spec['level'] == level and bool(spec.get('visible', True))
    ]
    for level in RESULT_PLOT_LEVELS
}

RESULT_PLOT_NAMES = [
    'best_instance',
    'best_instance_subproblems',
    'core_gantt',
    'result_value_vs_time',
    'core_info',
    'solving_times',
    'solving_times_by_day',
    'requests_per_patient',
    'equal_requests_between_iterations',
    'aggregate_best_solution_value',
    LEGACY_EXPERIMENT_COMPARISON_KEY,
]


def get_result_plot_specs(
        level: str | None = None,
        *,
        include_hidden: bool = False) -> list[ResultPlotSpec]:
    specs = RESULT_PLOT_SPECS
    if level is not None:
        specs = [spec for spec in specs if spec['level'] == level]
    if not include_hidden:
        specs = [spec for spec in specs if bool(spec.get('visible', True))]
    return list(specs)


def get_result_plot_keys(
        level: str | None = None,
        *,
        include_hidden: bool = False) -> list[str]:
    return [spec['key'] for spec in get_result_plot_specs(level, include_hidden=include_hidden)]


def parse_result_plots_to_do(raw_value) -> dict[str, list[str]]:
    selected = {level: [] for level in RESULT_PLOT_LEVELS}

    def _append(level: str, key: str):
        if key not in selected[level]:
            selected[level].append(key)

    if isinstance(raw_value, dict):
        for level in RESULT_PLOT_LEVELS:
            raw_level_values = raw_value.get(level, [])
            if not isinstance(raw_level_values, list):
                continue
            for item in raw_level_values:
                key = str(item).strip()
                spec = RESULT_PLOT_SPECS_BY_KEY.get(key)
                if spec is None or spec['level'] != level:
                    continue
                _append(level, key)
        return selected

    if isinstance(raw_value, list):
        for item in raw_value:
            key = str(item).strip()
            if key == '':
                continue
            if key == LEGACY_EXPERIMENT_COMPARISON_KEY:
                for level in ['comparison', 'group']:
                    for plot_key in VISIBLE_RESULT_PLOT_KEYS_BY_LEVEL[level]:
                        _append(level, plot_key)
                continue

            spec = RESULT_PLOT_SPECS_BY_KEY.get(key)
            if spec is None:
                continue
            _append(spec['level'], key)
        return selected

    return selected


def serialize_result_plots_to_do(selected_by_level: dict[str, list[str]] | None) -> dict[str, list[str]]:
    normalized = parse_result_plots_to_do(selected_by_level or {})
    return {
        level: list(normalized[level])
        for level in RESULT_PLOT_LEVELS
    }


def flatten_result_plot_selection(selected_by_level: dict[str, list[str]] | None) -> list[str]:
    normalized = parse_result_plots_to_do(selected_by_level or {})
    flattened: list[str] = []
    for level in RESULT_PLOT_LEVELS:
        flattened.extend(normalized[level])
    return flattened


def has_any_selected_result_plot(selected_by_level: dict[str, list[str]] | None) -> bool:
    normalized = parse_result_plots_to_do(selected_by_level or {})
    return any(len(normalized[level]) > 0 for level in RESULT_PLOT_LEVELS)


MASTER_INSTANCE_PER_INSTANCE_PLOT_NAMES = [
    "patient_windows_gantt",
    "average_window_overlap_by_day",
    "weighted_window_overlap_by_day",
    "spread_capacity_heatmap",
]

MASTER_INSTANCE_AGGREGATE_PLOT_NAMES = [
    "instance_daily_median_window_overlap_distribution",
    "instance_daily_weighted_window_overlap_distribution",
    "instance_daily_average_spread_capacity_distribution",
    "instance_request_count_distribution",
    "instance_duration_weighted_request_count_distribution",
    "instance_same_service_overlapping_window_distribution",
]

MASTER_INSTANCE_PLOT_NAMES = [
    *MASTER_INSTANCE_PER_INSTANCE_PLOT_NAMES,
    *MASTER_INSTANCE_AGGREGATE_PLOT_NAMES,
]
