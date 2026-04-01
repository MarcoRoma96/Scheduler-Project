from __future__ import annotations

from argparse import ArgumentParser, RawTextHelpFormatter
from itertools import combinations
from math import comb, exp, isfinite, lgamma, log, sqrt
from pathlib import Path
from textwrap import dedent

import numpy as np
import pandas as pd


DEFAULT_CONFIG_ORDER = [
    'generalist',
    'basic',
    'reduced',
    'pruned',
    'irreducible_pruned',
    'pruned_pat_expansion',
    'expanded',
    'monolithic',
]

KEY_COLUMNS = ['config', 'group', 'instance']
LOWER_IS_BETTER_METRICS = {
    'run_total_time_elapsed',
    'total_master_time',
    'total_subproblem_time',
    'other_tracked_time',
    'final_gap_pct',
}
HIGHER_IS_BETTER_METRICS = {
    'objective_value',
}


def _order_configs(config_names: list[str]) -> list[str]:
    rank = {name: index for index, name in enumerate(DEFAULT_CONFIG_ORDER)}
    return sorted(
        {str(name) for name in config_names if str(name).strip() != ''},
        key=lambda name: (rank.get(name, len(rank)), name))


def _read_instance_analysis(analysis_path: Path) -> pd.DataFrame:
    instance_path = analysis_path.joinpath('instance_analysis.xlsx')
    if not instance_path.exists():
        raise FileNotFoundError(f'Missing analyzer output: {instance_path}')
    df = pd.read_excel(instance_path)
    required = {'config', 'group', 'instance', 'status', 'run_total_time_elapsed', 'final_gap_pct', 'objective_value'}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f'instance_analysis.xlsx is missing required columns: {missing}')
    return df


def _aggregate_csv_sum(file_path: Path, value_column: str, chunksize: int = 250_000) -> pd.DataFrame:
    if not file_path.exists():
        raise FileNotFoundError(f'Missing analyzer output: {file_path}')

    grouped_chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(file_path, usecols=KEY_COLUMNS + [value_column], chunksize=chunksize, low_memory=False):
        chunk[value_column] = pd.to_numeric(chunk[value_column], errors='coerce').fillna(0.0)
        grouped = chunk.groupby(KEY_COLUMNS, as_index=False)[value_column].sum()
        grouped_chunks.append(grouped)

    if len(grouped_chunks) == 0:
        return pd.DataFrame(columns=KEY_COLUMNS + [value_column])

    return (
        pd.concat(grouped_chunks, ignore_index=True)
        .groupby(KEY_COLUMNS, as_index=False)[value_column]
        .sum()
    )


def _build_merged_instance_table(analysis_path: Path, configs: list[str] | None = None) -> pd.DataFrame:
    instance_df = _read_instance_analysis(analysis_path).copy()
    instance_df['config'] = instance_df['config'].astype(str)
    instance_df['group'] = instance_df['group'].astype(str)
    instance_df['instance'] = instance_df['instance'].astype(str)
    instance_df['status'] = instance_df['status'].astype(str)
    instance_df['status_reason'] = instance_df.get('status_reason', '').astype(str)
    instance_df['run_total_time_elapsed'] = pd.to_numeric(instance_df['run_total_time_elapsed'], errors='coerce')
    instance_df['final_gap_pct'] = pd.to_numeric(instance_df['final_gap_pct'], errors='coerce')
    instance_df['objective_value'] = pd.to_numeric(instance_df['objective_value'], errors='coerce')
    instance_df['optimal_flag'] = instance_df['status'].eq('optimal')

    if configs is not None and len(configs) > 0:
        config_set = set(configs)
        instance_df = instance_df[instance_df['config'].isin(config_set)].copy()

    master_totals = _aggregate_csv_sum(analysis_path.joinpath('master_result_analysis.csv'), 'master_time')
    master_totals = master_totals.rename(columns={'master_time': 'total_master_time'})
    subproblem_totals = _aggregate_csv_sum(analysis_path.joinpath('subproblem_result_analysis.csv'), 'time')
    subproblem_totals = subproblem_totals.rename(columns={'time': 'total_subproblem_time'})

    merged = (
        instance_df
        .merge(master_totals, on=KEY_COLUMNS, how='left')
        .merge(subproblem_totals, on=KEY_COLUMNS, how='left')
    )
    merged['total_master_time'] = pd.to_numeric(merged['total_master_time'], errors='coerce').fillna(0.0)
    merged['total_subproblem_time'] = pd.to_numeric(merged['total_subproblem_time'], errors='coerce').fillna(0.0)
    merged['other_tracked_time'] = np.maximum(
        0.0,
        merged['run_total_time_elapsed'].fillna(0.0)
        - merged['total_master_time']
        - merged['total_subproblem_time'])
    return merged


def _beta_continued_fraction(a: float, b: float, x: float, max_iter: int = 200, eps: float = 3e-14) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (
        lgamma(a + b)
        - lgamma(a)
        - lgamma(b)
        + a * log(x)
        + b * log(1.0 - x)
    )
    bt = exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _beta_continued_fraction(a, b, x) / a
    return 1.0 - bt * _beta_continued_fraction(b, a, 1.0 - x) / b


def _student_t_two_sided_pvalue(t_stat: float, degrees_of_freedom: int) -> float:
    if not isfinite(t_stat) or degrees_of_freedom <= 0:
        return float('nan')
    x = degrees_of_freedom / (degrees_of_freedom + t_stat * t_stat)
    return float(_regularized_incomplete_beta(degrees_of_freedom / 2.0, 0.5, x))


def _f_upper_tail_pvalue(f_stat: float, df_num: int, df_den: int) -> float:
    if not isfinite(f_stat) or f_stat < 0.0 or df_num <= 0 or df_den <= 0:
        return float('nan')
    x = (df_num * f_stat) / (df_num * f_stat + df_den)
    return float(1.0 - _regularized_incomplete_beta(df_num / 2.0, df_den / 2.0, x))


def _binomial_two_sided_pvalue(successes: int, trials: int, p: float = 0.5) -> float:
    if trials <= 0:
        return float('nan')
    if not (0.0 <= p <= 1.0):
        return float('nan')
    if p != 0.5:
        raise ValueError('Only p=0.5 is supported in this lightweight implementation.')

    k = min(successes, trials - successes)
    cumulative = 0.0
    denominator = 2 ** trials
    for value in range(0, k + 1):
        cumulative += comb(trials, value) / denominator
    return float(min(1.0, 2.0 * cumulative))


def _paired_t_test(left: pd.Series, right: pd.Series) -> dict[str, float | int]:
    paired = pd.DataFrame({'left': left, 'right': right}).dropna()
    n = int(len(paired))
    if n == 0:
        return {
            'n_common': 0,
            'mean_left': np.nan,
            'mean_right': np.nan,
            'median_left': np.nan,
            'median_right': np.nan,
            'std_left': np.nan,
            'std_right': np.nan,
            'mean_delta_right_minus_left': np.nan,
            'median_delta_right_minus_left': np.nan,
            'std_delta': np.nan,
            't_stat': np.nan,
            'p_value': np.nan,
            'cohen_dz': np.nan,
        }

    deltas = paired['right'] - paired['left']
    mean_delta = float(deltas.mean())
    std_delta = float(deltas.std(ddof=1)) if n > 1 else float('nan')
    t_stat = float('nan')
    p_value = float('nan')
    cohen_dz = float('nan')
    if n > 1 and isfinite(std_delta) and std_delta > 1e-15:
        t_stat = mean_delta / (std_delta / sqrt(n))
        p_value = _student_t_two_sided_pvalue(t_stat, n - 1)
        cohen_dz = mean_delta / std_delta

    return {
        'n_common': n,
        'mean_left': float(paired['left'].mean()),
        'mean_right': float(paired['right'].mean()),
        'median_left': float(paired['left'].median()),
        'median_right': float(paired['right'].median()),
        'std_left': float(paired['left'].std(ddof=1)) if n > 1 else float('nan'),
        'std_right': float(paired['right'].std(ddof=1)) if n > 1 else float('nan'),
        'mean_delta_right_minus_left': mean_delta,
        'median_delta_right_minus_left': float(deltas.median()),
        'std_delta': std_delta,
        't_stat': t_stat,
        'p_value': p_value,
        'cohen_dz': cohen_dz,
    }


def _paired_sign_test(left: pd.Series, right: pd.Series, lower_is_better: bool) -> dict[str, float | int]:
    paired = pd.DataFrame({'left': left, 'right': right}).dropna()
    if len(paired) == 0:
        return {
            'wins_left': 0,
            'wins_right': 0,
            'ties': 0,
            'sign_test_p_value': np.nan,
        }

    if lower_is_better:
        left_wins = int((paired['left'] < paired['right']).sum())
        right_wins = int((paired['right'] < paired['left']).sum())
    else:
        left_wins = int((paired['left'] > paired['right']).sum())
        right_wins = int((paired['right'] > paired['left']).sum())

    ties = int(len(paired) - left_wins - right_wins)
    discordant = left_wins + right_wins
    p_value = _binomial_two_sided_pvalue(min(left_wins, right_wins), discordant) if discordant > 0 else np.nan
    return {
        'wins_left': left_wins,
        'wins_right': right_wins,
        'ties': ties,
        'sign_test_p_value': p_value,
    }


def _mcnemar_exact_test(left_optimal: pd.Series, right_optimal: pd.Series) -> dict[str, float | int]:
    paired = pd.DataFrame({'left': left_optimal, 'right': right_optimal}).dropna()
    if len(paired) == 0:
        return {
            'n_common': 0,
            'both_optimal': 0,
            'left_only_optimal': 0,
            'right_only_optimal': 0,
            'neither_optimal': 0,
            'mcnemar_exact_p_value': np.nan,
        }

    left_bool = paired['left'].astype(bool)
    right_bool = paired['right'].astype(bool)
    both = int((left_bool & right_bool).sum())
    left_only = int((left_bool & ~right_bool).sum())
    right_only = int((~left_bool & right_bool).sum())
    neither = int((~left_bool & ~right_bool).sum())
    discordant = left_only + right_only
    p_value = _binomial_two_sided_pvalue(min(left_only, right_only), discordant) if discordant > 0 else np.nan
    return {
        'n_common': int(len(paired)),
        'both_optimal': both,
        'left_only_optimal': left_only,
        'right_only_optimal': right_only,
        'neither_optimal': neither,
        'mcnemar_exact_p_value': p_value,
    }


def _repeated_measures_anova(metric_matrix: pd.DataFrame) -> dict[str, float | int]:
    complete = metric_matrix.dropna(axis=0, how='any')
    subject_count, method_count = complete.shape
    if subject_count < 2 or method_count < 2:
        return {
            'n_subjects': int(subject_count),
            'n_methods': int(method_count),
            'f_stat': np.nan,
            'df_num': np.nan,
            'df_den': np.nan,
            'p_value': np.nan,
        }

    values = complete.to_numpy(dtype=float)
    grand_mean = float(values.mean())
    subject_means = values.mean(axis=1)
    method_means = values.mean(axis=0)

    ss_total = float(((values - grand_mean) ** 2).sum())
    ss_subjects = float(method_count * ((subject_means - grand_mean) ** 2).sum())
    ss_methods = float(subject_count * ((method_means - grand_mean) ** 2).sum())
    ss_error = ss_total - ss_subjects - ss_methods

    df_num = method_count - 1
    df_den = (method_count - 1) * (subject_count - 1)
    if df_num <= 0 or df_den <= 0:
        return {
            'n_subjects': int(subject_count),
            'n_methods': int(method_count),
            'f_stat': np.nan,
            'df_num': np.nan,
            'df_den': np.nan,
            'p_value': np.nan,
        }

    ms_methods = ss_methods / df_num
    ms_error = ss_error / df_den if abs(ss_error) > 1e-15 else 0.0
    if ms_error <= 1e-15:
        f_stat = float('inf') if ms_methods > 0.0 else 0.0
        p_value = 0.0 if ms_methods > 0.0 else 1.0
    else:
        f_stat = ms_methods / ms_error
        p_value = _f_upper_tail_pvalue(f_stat, df_num, df_den)

    return {
        'n_subjects': int(subject_count),
        'n_methods': int(method_count),
        'f_stat': float(f_stat),
        'df_num': int(df_num),
        'df_den': int(df_den),
        'p_value': float(p_value),
    }


def _build_config_summary(merged_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        merged_df
        .groupby('config')
        .agg(
            runs=('instance', 'size'),
            optimal_count=('optimal_flag', 'sum'),
            optimal_pct=('optimal_flag', lambda s: 100.0 * s.mean()),
            mean_tracked_time=('run_total_time_elapsed', 'mean'),
            median_tracked_time=('run_total_time_elapsed', 'median'),
            p90_tracked_time=('run_total_time_elapsed', lambda s: s.quantile(0.9)),
            mean_master_time=('total_master_time', 'mean'),
            mean_subproblem_time=('total_subproblem_time', 'mean'),
            mean_other_tracked_time=('other_tracked_time', 'mean'),
            median_other_tracked_time=('other_tracked_time', 'median'),
            mean_final_gap_pct=('final_gap_pct', 'mean'),
            median_final_gap_pct=('final_gap_pct', 'median'),
            mean_objective_value=('objective_value', 'mean'),
            median_objective_value=('objective_value', 'median'),
        )
        .reset_index()
    )
    return summary


def _build_group_summary(merged_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        merged_df
        .groupby(['group', 'config'])
        .agg(
            runs=('instance', 'size'),
            optimal_count=('optimal_flag', 'sum'),
            optimal_pct=('optimal_flag', lambda s: 100.0 * s.mean()),
            mean_tracked_time=('run_total_time_elapsed', 'mean'),
            median_tracked_time=('run_total_time_elapsed', 'median'),
            mean_master_time=('total_master_time', 'mean'),
            mean_subproblem_time=('total_subproblem_time', 'mean'),
            mean_other_tracked_time=('other_tracked_time', 'mean'),
            mean_final_gap_pct=('final_gap_pct', 'mean'),
            mean_objective_value=('objective_value', 'mean'),
        )
        .reset_index()
    )
    return summary


def _build_pairwise_optimality_tests(merged_df: pd.DataFrame, ordered_configs: list[str]) -> pd.DataFrame:
    pivot = (
        merged_df
        .pivot(index=['group', 'instance'], columns='config', values='optimal_flag')
    )
    rows = []
    for left_config, right_config in combinations(ordered_configs, 2):
        if left_config not in pivot.columns or right_config not in pivot.columns:
            continue
        result = _mcnemar_exact_test(pivot[left_config], pivot[right_config])
        result.update({
            'left_config': left_config,
            'right_config': right_config,
            'optimal_count_left': int(pd.Series(pivot[left_config]).dropna().astype(bool).sum()),
            'optimal_count_right': int(pd.Series(pivot[right_config]).dropna().astype(bool).sum()),
        })
        rows.append(result)
    if len(rows) == 0:
        return pd.DataFrame(columns=[
            'left_config',
            'right_config',
            'n_common',
            'optimal_count_left',
            'optimal_count_right',
            'both_optimal',
            'left_only_optimal',
            'right_only_optimal',
            'neither_optimal',
            'mcnemar_exact_p_value',
        ])
    df = pd.DataFrame(rows)
    return df[[
        'left_config',
        'right_config',
        'n_common',
        'optimal_count_left',
        'optimal_count_right',
        'both_optimal',
        'left_only_optimal',
        'right_only_optimal',
        'neither_optimal',
        'mcnemar_exact_p_value',
    ]]


def _build_pairwise_continuous_tests(
        merged_df: pd.DataFrame,
        ordered_configs: list[str],
        *,
        only_both_optimal: bool = False) -> pd.DataFrame:
    metrics = [
        'run_total_time_elapsed',
        'total_master_time',
        'total_subproblem_time',
        'other_tracked_time',
        'objective_value',
        'final_gap_pct',
    ]
    rows = []

    index_columns = ['group', 'instance']
    if only_both_optimal:
        optimal_pivot = merged_df.pivot(index=index_columns, columns='config', values='optimal_flag')
    else:
        optimal_pivot = None

    for metric_name in metrics:
        metric_pivot = merged_df.pivot(index=index_columns, columns='config', values=metric_name)
        for left_config, right_config in combinations(ordered_configs, 2):
            if left_config not in metric_pivot.columns or right_config not in metric_pivot.columns:
                continue

            left_series = metric_pivot[left_config]
            right_series = metric_pivot[right_config]

            if only_both_optimal and optimal_pivot is not None:
                if left_config not in optimal_pivot.columns or right_config not in optimal_pivot.columns:
                    continue
                both_optimal_mask = (
                    optimal_pivot[left_config].fillna(False).astype(bool)
                    & optimal_pivot[right_config].fillna(False).astype(bool)
                )
                left_series = left_series[both_optimal_mask]
                right_series = right_series[both_optimal_mask]

            t_test = _paired_t_test(left_series, right_series)
            lower_is_better = metric_name in LOWER_IS_BETTER_METRICS
            sign_test = _paired_sign_test(left_series, right_series, lower_is_better=lower_is_better)
            rows.append({
                'left_config': left_config,
                'right_config': right_config,
                'metric': metric_name,
                'comparison_scope': 'both_optimal_only' if only_both_optimal else 'all_common_instances',
                'improvement_direction': 'lower_is_better' if lower_is_better else 'higher_is_better',
                **t_test,
                **sign_test,
            })

    if len(rows) == 0:
        return pd.DataFrame(columns=[
            'left_config',
            'right_config',
            'metric',
            'comparison_scope',
            'improvement_direction',
            'n_common',
            'mean_left',
            'mean_right',
            'median_left',
            'median_right',
            'std_left',
            'std_right',
            'mean_delta_right_minus_left',
            'median_delta_right_minus_left',
            'std_delta',
            't_stat',
            'p_value',
            'cohen_dz',
            'wins_left',
            'wins_right',
            'ties',
            'sign_test_p_value',
        ])
    df = pd.DataFrame(rows)
    return df[[
        'left_config',
        'right_config',
        'metric',
        'comparison_scope',
        'improvement_direction',
        'n_common',
        'mean_left',
        'mean_right',
        'median_left',
        'median_right',
        'std_left',
        'std_right',
        'mean_delta_right_minus_left',
        'median_delta_right_minus_left',
        'std_delta',
        't_stat',
        'p_value',
        'cohen_dz',
        'wins_left',
        'wins_right',
        'ties',
        'sign_test_p_value',
    ]]


def _build_repeated_anova_table(merged_df: pd.DataFrame, ordered_configs: list[str]) -> pd.DataFrame:
    metrics = [
        'run_total_time_elapsed',
        'total_master_time',
        'total_subproblem_time',
        'other_tracked_time',
        'objective_value',
        'final_gap_pct',
    ]
    rows = []
    for metric_name in metrics:
        pivot = (
            merged_df
            .pivot(index=['group', 'instance'], columns='config', values=metric_name)
            .reindex(columns=[config for config in ordered_configs if config in merged_df['config'].unique()])
        )
        anova_result = _repeated_measures_anova(pivot)
        complete = pivot.dropna(axis=0, how='any')
        row = {
            'metric': metric_name,
            **anova_result,
        }
        for config_name in complete.columns:
            row[f'mean_{config_name}'] = float(complete[config_name].mean()) if len(complete) > 0 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _write_excel_sheet(df: pd.DataFrame, writer: pd.ExcelWriter, sheet_name: str):
    df.to_excel(writer, sheet_name=sheet_name, index=False, na_rep='NaN')
    worksheet = writer.sheets[sheet_name]
    for column_index, column_name in enumerate(df.columns):
        max_length = max(len(str(column_name)), 10)
        if len(df) > 0:
            series = df[column_name]
            max_cell_length = int(series.map(lambda value: len(str(value))).max())
            max_length = min(60, max(max_length, max_cell_length + 1))
        worksheet.set_column(column_index, column_index, max_length)


def _print_dataframe(title: str, df: pd.DataFrame, max_rows: int | None = None):
    print(f'\n{"=" * 100}')
    print(title)
    print(f'{"=" * 100}')
    if len(df) == 0:
        print('<empty>')
        return
    output_df = df if max_rows is None else df.head(max_rows)
    print(output_df.to_string(index=False, float_format=lambda value: f'{value:,.4f}' if isinstance(value, float) and isfinite(value) else str(value)))
    if max_rows is not None and len(df) > max_rows:
        print(f'... ({len(df) - max_rows} more rows)')


def build_method_comparison_report(results_root: Path, selected_configs: list[str] | None = None) -> tuple[Path, dict[str, pd.DataFrame]]:
    analysis_path = results_root.joinpath('analysis')
    merged_df = _build_merged_instance_table(analysis_path, selected_configs)
    ordered_configs = _order_configs(merged_df['config'].astype(str).unique().tolist())
    merged_df['config'] = pd.Categorical(merged_df['config'], categories=ordered_configs, ordered=True)
    merged_df = merged_df.sort_values(['config', 'group', 'instance']).reset_index(drop=True)

    config_summary_df = _build_config_summary(merged_df).sort_values('config').reset_index(drop=True)
    group_summary_df = _build_group_summary(merged_df).sort_values(['group', 'config']).reset_index(drop=True)
    optimality_tests_df = _build_pairwise_optimality_tests(merged_df, ordered_configs)
    continuous_tests_df = _build_pairwise_continuous_tests(merged_df, ordered_configs, only_both_optimal=False)
    continuous_tests_opt_df = _build_pairwise_continuous_tests(merged_df, ordered_configs, only_both_optimal=True)
    repeated_anova_df = _build_repeated_anova_table(merged_df, ordered_configs)

    output_path = analysis_path.joinpath('method_comparison_report.xlsx')
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        _write_excel_sheet(config_summary_df, writer, 'config_summary')
        _write_excel_sheet(group_summary_df, writer, 'group_summary')
        _write_excel_sheet(optimality_tests_df, writer, 'pairwise_optimality')
        _write_excel_sheet(continuous_tests_df, writer, 'pairwise_continuous_all')
        _write_excel_sheet(continuous_tests_opt_df, writer, 'pairwise_continuous_opt')
        _write_excel_sheet(repeated_anova_df, writer, 'repeated_anova')

    return output_path, {
        'config_summary': config_summary_df,
        'group_summary': group_summary_df,
        'pairwise_optimality': optimality_tests_df,
        'pairwise_continuous_all': continuous_tests_df,
        'pairwise_continuous_opt': continuous_tests_opt_df,
        'repeated_anova': repeated_anova_df,
    }


def main():
    parser = ArgumentParser(
        prog='method_comparison_report.py',
        formatter_class=RawTextHelpFormatter,
        description=dedent(
            """\
            Build a cross-method comparison report from analyzer outputs.

            Inputs expected in <results_root>/analysis/:
              - instance_analysis.xlsx
              - master_result_analysis.csv
              - subproblem_result_analysis.csv

            Output:
              - <results_root>/analysis/method_comparison_report.xlsx

            The report includes:
              - global summary by method
              - summary by group and method
              - pairwise optimality tests (exact McNemar)
              - pairwise paired t-tests + exact sign tests for continuous metrics
              - repeated-measures ANOVA across all methods on complete matched runs
            """))
    parser.add_argument(
        '-i', '--input',
        type=Path,
        required=True,
        help='Results root containing the centralized analysis folder, e.g. results_experiment/')
    parser.add_argument(
        '--configs',
        nargs='*',
        default=None,
        help='Optional subset of configs to compare. Default: all configs present in analysis.')
    args = parser.parse_args()

    results_root = args.input.resolve()
    output_path, tables = build_method_comparison_report(results_root, args.configs)

    _print_dataframe('Config summary', tables['config_summary'])
    _print_dataframe('Group summary', tables['group_summary'])
    _print_dataframe('Pairwise optimality tests', tables['pairwise_optimality'])
    _print_dataframe('Pairwise continuous tests (all common instances)', tables['pairwise_continuous_all'])
    _print_dataframe('Pairwise continuous tests (both-optimal common instances)', tables['pairwise_continuous_opt'])
    _print_dataframe('Repeated-measures ANOVA', tables['repeated_anova'])
    print(f'\nSaved Excel report to: {output_path}')


if __name__ == '__main__':
    main()
