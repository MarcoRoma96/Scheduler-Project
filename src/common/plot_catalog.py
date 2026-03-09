RESULT_PLOT_NAMES = [
    "best_instance",
    "best_instance_subproblems",
    "core_gantt",
    "result_value_vs_time",
    "core_info",
    "solving_times",
    "solving_times_by_day",
    "requests_per_patient",
    "equal_requests_between_iterations",
    "aggregate_best_solution_value",
    "experiment_group_comparison",
]

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
]

MASTER_INSTANCE_PLOT_NAMES = [
    *MASTER_INSTANCE_PER_INSTANCE_PLOT_NAMES,
    *MASTER_INSTANCE_AGGREGATE_PLOT_NAMES,
]
