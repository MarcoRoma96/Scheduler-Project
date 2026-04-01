from argparse import ArgumentParser
from pathlib import Path
import yaml
import json
import time
import re
import hashlib
import pandas as pd

from src.common.custom_types import FinalResult
from src.common.analysis_constants import FINAL_GAP_OPTIMAL_TOLERANCE_PCT
from src.common.analysis_paths import get_analysis_output_path, normalize_filter_values, is_all_filter_values
from src.common.file_load_and_dump import decode_master_instance, decode_master_result, decode_subproblem_result
from src.common.file_load_and_dump import decode_final_result, decode_cores, decode_subproblem_instance
from src.common.tools import is_combination_to_do
from src.analyzers.master_instance_analyzer import analyze_master_instance
from src.analyzers.master_result_analyzer import analyze_master_result
from src.analyzers.subproblem_instance_analyzer import analyze_subproblem_instance
from src.analyzers.subproblem_result_analyzer import analyze_subproblem_result
from src.analyzers.final_result_analyzer import analyze_final_result
from src.analyzers.cores_analyzer import analyze_cores
from src.analyzers.tools import analyze_log, get_gap_metrics, get_lbbd_final_gap_metrics, get_total_operator_duration


# Questo script può essere chiamato solo direttamente dalla linea di comando
if __name__ != '__main__':
    exit(0)


def write_excel_sheet(df: pd.DataFrame, writer: pd.ExcelWriter, sheet_name: str):
    '''Funzione che crea una pagina Excel con i dati forniti dal DataFrame.'''

    df.to_excel(writer, sheet_name=sheet_name, index=False, na_rep='NaN')

    # Aggiustamento dell'ampiezza delle colonne
    for column_name, column in df.items():
        def _safe_text_len(value) -> int:
            try:
                missing = pd.isna(value)
                if isinstance(missing, bool) and missing:
                    return 3  # 'NaN'
            except Exception:
                pass
            return len(str(value))

        max_cell_length = int(column.map(_safe_text_len).max()) if len(column) > 0 else 0
        column_length = max(max_cell_length, len(str(column_name)))
        col_idx = df.columns.get_loc(column_name)
        writer.sheets[sheet_name].set_column(col_idx, col_idx, min(column_length + 1, 120))

def _get_iteration_index(iteration_path: Path) -> int:
    return int(iteration_path.name.split('_')[-1])

_GROUP_ORDER_REGEX = re.compile(r'(?P<patients>\d+)pat_(?P<care_units>\d+)cu')
_TRAILING_NUMBER_REGEX = re.compile(r'(\d+)$')
ANALYSIS_CACHE_VERSION = 2
ANALYSIS_CACHE_FILENAME = '.analysis_cache_manifest.json'
INSTANCE_ANALYSIS_BASENAME = 'instance_analysis'
MASTER_RESULT_ANALYSIS_BASENAME = 'master_result_analysis'
SUBPROBLEM_RESULT_ANALYSIS_BASENAME = 'subproblem_result_analysis'

def _get_iteration_paths(result_directory: Path) -> list[Path]:
    return sorted(
        [
            path for path in result_directory.iterdir()
            if path.is_dir() and path.name.startswith('iter_')
        ],
        key=_get_iteration_index)

def _get_latest_iteration_path(result_directory: Path) -> Path | None:
    iteration_paths = _get_iteration_paths(result_directory)
    if len(iteration_paths) == 0:
        return None
    return max(iteration_paths, key=_get_iteration_index)

def _read_run_config(result_directory: Path) -> dict:
    config_path = result_directory.joinpath('config.yaml')
    if not config_path.exists():
        return {}
    with open(config_path, 'r') as file:
        loaded = yaml.load(file, yaml.CLoader)
    return loaded if isinstance(loaded, dict) else {}

def _read_run_status(result_directory: Path) -> dict:
    run_status_path = result_directory.joinpath('run_status.json')
    if not run_status_path.exists():
        return {}
    with open(run_status_path, 'r') as file:
        loaded = json.load(file)
    return loaded if isinstance(loaded, dict) else {}

def _extract_group_order_values(group_name: str | object) -> tuple[int | None, int | None]:
    match = _GROUP_ORDER_REGEX.search(str(group_name))
    if match is None:
        return None, None
    return int(match.group('patients')), int(match.group('care_units'))

def _extract_trailing_number(value: str | object) -> int | None:
    match = _TRAILING_NUMBER_REGEX.search(str(value))
    if match is None:
        return None
    return int(match.group(1))

def _sort_analysis_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) == 0:
        return df

    sorted_df = df.copy()
    sentinel = 10 ** 9

    patient_order = pd.Series([sentinel] * len(sorted_df), index=sorted_df.index, dtype='int64')
    care_unit_order = pd.Series([sentinel] * len(sorted_df), index=sorted_df.index, dtype='int64')

    if 'group' in sorted_df.columns:
        extracted = sorted_df['group'].map(_extract_group_order_values)
        patient_order = extracted.map(lambda pair: sentinel if pair[0] is None else pair[0]).astype('int64')
        care_unit_order = extracted.map(lambda pair: sentinel if pair[1] is None else pair[1]).astype('int64')

    if 'patient_number' in sorted_df.columns:
        patient_fallback = pd.to_numeric(sorted_df['patient_number'], errors='coerce').fillna(sentinel).astype('int64')
        patient_order = patient_order.where(patient_order != sentinel, patient_fallback)

    for care_unit_column in ['care_unit_number', 'average_care_unit_per_day', 'care_unit_total_number']:
        if care_unit_column not in sorted_df.columns:
            continue
        care_unit_fallback = pd.to_numeric(sorted_df[care_unit_column], errors='coerce').fillna(sentinel).astype('int64')
        care_unit_order = care_unit_order.where(care_unit_order != sentinel, care_unit_fallback)
        break

    instance_order = pd.Series([sentinel] * len(sorted_df), index=sorted_df.index, dtype='int64')
    if 'instance' in sorted_df.columns:
        instance_order = sorted_df['instance'].map(
            lambda value: sentinel if _extract_trailing_number(value) is None else _extract_trailing_number(value)
        ).astype('int64')

    iteration_order = pd.Series([sentinel] * len(sorted_df), index=sorted_df.index, dtype='int64')
    if 'iteration' in sorted_df.columns:
        iteration_order = pd.to_numeric(sorted_df['iteration'], errors='coerce').fillna(sentinel).astype('int64')

    day_order = pd.Series([sentinel] * len(sorted_df), index=sorted_df.index, dtype='int64')
    if 'day' in sorted_df.columns:
        day_order = pd.to_numeric(sorted_df['day'], errors='coerce').fillna(sentinel).astype('int64')

    sorted_df['__sort_patient_number'] = patient_order
    sorted_df['__sort_care_unit_number'] = care_unit_order
    sorted_df['__sort_instance'] = instance_order
    sorted_df['__sort_iteration'] = iteration_order
    sorted_df['__sort_day'] = day_order

    sort_columns = ['__sort_patient_number', '__sort_care_unit_number', '__sort_instance']
    for optional_column in ['config', 'group', 'instance']:
        if optional_column in sorted_df.columns:
            sort_columns.append(optional_column)
    if 'iteration' in sorted_df.columns:
        sort_columns.append('__sort_iteration')
    if 'day' in sorted_df.columns:
        sort_columns.append('__sort_day')

    sorted_df = sorted_df.sort_values(sort_columns, kind='mergesort').drop(
        columns=[
            '__sort_patient_number',
            '__sort_care_unit_number',
            '__sort_instance',
            '__sort_iteration',
            '__sort_day',
        ])
    return sorted_df.reset_index(drop=True)

def _resolve_existing_analysis_file_path(analysis_path: Path, base_name: str) -> Path | None:
    csv_path = analysis_path.joinpath(f'{base_name}.csv')
    if csv_path.exists():
        return csv_path
    xlsx_path = analysis_path.joinpath(f'{base_name}.xlsx')
    if xlsx_path.exists():
        return xlsx_path
    return None


def _load_existing_analysis_dataframe(analysis_path: Path, base_name: str, sheet_name: str | None = None) -> pd.DataFrame:
    file_path = _resolve_existing_analysis_file_path(analysis_path, base_name)
    if file_path is None:
        return pd.DataFrame()
    try:
        if file_path.suffix.lower() == '.csv':
            return pd.read_csv(file_path, low_memory=False)
        if sheet_name is None:
            return pd.read_excel(file_path)
        return pd.read_excel(file_path, sheet_name=sheet_name)
    except Exception:
        return pd.DataFrame()


def write_csv_table(df: pd.DataFrame, file_path: Path):
    df.to_csv(file_path, index=False, na_rep='NaN')

def _load_analysis_cache_manifest(analysis_path: Path) -> dict:
    manifest_path = analysis_path.joinpath(ANALYSIS_CACHE_FILENAME)
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, 'r') as file:
            loaded = json.load(file)
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    cache_version = loaded.get('cache_version')
    if cache_version not in {1, ANALYSIS_CACHE_VERSION}:
        return {}
    entries = loaded.get('entries', {})
    if not isinstance(entries, dict):
        return {}

    normalized_entries: dict[str, dict] = {}
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        normalized_entry = dict(value)
        normalized_entry.setdefault('has_instance_analysis', int(normalized_entry.get('instance_count', 0)) > 0)
        normalized_entry.setdefault('has_master_result_analysis', int(normalized_entry.get('master_count', 0)) > 0)
        normalized_entry.setdefault('has_subproblem_result_analysis', int(normalized_entry.get('subproblem_count', 0)) > 0)
        normalized_entries[str(key)] = normalized_entry
    return normalized_entries

def _write_analysis_cache_manifest(analysis_path: Path, entries: dict[str, dict]):
    manifest_path = analysis_path.joinpath(ANALYSIS_CACHE_FILENAME)
    with open(manifest_path, 'w') as file:
        json.dump({
            'cache_version': ANALYSIS_CACHE_VERSION,
            'entries': entries,
        }, file, indent=4, sort_keys=True)


def _build_cache_manifest_entry(
        fingerprint: str,
        instance_count: int,
        master_count: int,
        subproblem_count: int,
        do_instance_analysis: bool,
        do_master_result_analysis: bool,
        do_subproblem_result_analysis: bool) -> dict[str, str | int | bool]:
    return {
        'fingerprint': fingerprint,
        'instance_count': int(instance_count),
        'master_count': int(master_count),
        'subproblem_count': int(subproblem_count),
        'has_instance_analysis': bool(do_instance_analysis),
        'has_master_result_analysis': bool(do_master_result_analysis),
        'has_subproblem_result_analysis': bool(do_subproblem_result_analysis),
    }

def _compute_result_directory_fingerprint(result_directory: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(result_directory.rglob('*')):
        if not path.is_file():
            continue
        relative_path = path.relative_to(result_directory)
        if len(relative_path.parts) > 0 and relative_path.parts[0] in {'analysis', 'plots'}:
            continue
        stat = path.stat()
        digest.update(relative_path.as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(stat.st_size).encode('ascii'))
        digest.update(b'\0')
        digest.update(str(stat.st_mtime_ns).encode('ascii'))
        digest.update(b'\0')
    return digest.hexdigest()

def _slice_existing_rows(
        df: pd.DataFrame,
        config_name: str,
        group_name: str,
        instance_name: str) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0].copy()
    required_columns = {'config', 'group', 'instance'}
    if not required_columns.issubset(set(df.columns)):
        return df.iloc[0:0].copy()
    mask = (
        df['config'].astype(str).eq(config_name)
        & df['group'].astype(str).eq(group_name)
        & df['instance'].astype(str).eq(instance_name))
    return df[mask].copy()


def _build_selected_scope_mask(df: pd.DataFrame, config: dict) -> pd.Series:
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    required_columns = {'config', 'group', 'instance'}
    if not required_columns.issubset(set(df.columns)):
        return pd.Series(False, index=df.index, dtype=bool)

    mask = pd.Series(True, index=df.index, dtype=bool)
    filter_specs = [
        ('config', 'configs_to_do', 'configs_to_avoid'),
        ('group', 'groups_to_do', 'groups_to_avoid'),
        ('instance', 'instances_to_do', 'instances_to_avoid'),
    ]

    for column_name, include_key, exclude_key in filter_specs:
        series = df[column_name].astype(str)
        excluded_values = {
            str(value).strip()
            for value in config.get(exclude_key, [])
            if str(value).strip() != ''
        }
        if len(excluded_values) > 0:
            mask &= ~series.isin(excluded_values)

        included_values = normalize_filter_values(config.get(include_key, ['all']))
        if not is_all_filter_values(included_values):
            mask &= series.isin({str(value) for value in included_values})

    return mask


def _is_result_directory_in_selected_scope(result_directory_name: str, config: dict) -> bool:
    tokens = str(result_directory_name).split('__')
    if len(tokens) != 3:
        return False
    return is_combination_to_do(tokens[0], tokens[1], tokens[2], config)


def _merge_cache_manifest_entry(
        existing_entry: dict | None,
        fingerprint: str,
        instance_count: int,
        master_count: int,
        subproblem_count: int,
        do_instance_analysis: bool,
        do_master_result_analysis: bool,
        do_subproblem_result_analysis: bool) -> dict[str, str | int | bool]:
    current_entry = _build_cache_manifest_entry(
        fingerprint,
        instance_count,
        master_count,
        subproblem_count,
        do_instance_analysis,
        do_master_result_analysis,
        do_subproblem_result_analysis)
    merged_entry = dict(existing_entry) if isinstance(existing_entry, dict) else {}
    merged_entry['fingerprint'] = fingerprint

    if do_instance_analysis:
        merged_entry['instance_count'] = current_entry['instance_count']
        merged_entry['has_instance_analysis'] = current_entry['has_instance_analysis']
    else:
        merged_entry.setdefault('instance_count', 0)
        merged_entry.setdefault('has_instance_analysis', bool(merged_entry['instance_count']))

    if do_master_result_analysis:
        merged_entry['master_count'] = current_entry['master_count']
        merged_entry['has_master_result_analysis'] = current_entry['has_master_result_analysis']
    else:
        merged_entry.setdefault('master_count', 0)
        merged_entry.setdefault('has_master_result_analysis', bool(merged_entry['master_count']))

    if do_subproblem_result_analysis:
        merged_entry['subproblem_count'] = current_entry['subproblem_count']
        merged_entry['has_subproblem_result_analysis'] = current_entry['has_subproblem_result_analysis']
    else:
        merged_entry.setdefault('subproblem_count', 0)
        merged_entry.setdefault('has_subproblem_result_analysis', bool(merged_entry['subproblem_count']))

    return merged_entry


def _replace_selected_scope_rows(
        existing_df: pd.DataFrame,
        selected_rows: list[dict[str, str | int | float]],
        config: dict) -> pd.DataFrame:
    preserved_df = existing_df.copy()
    if not existing_df.empty:
        preserved_df = existing_df[~_build_selected_scope_mask(existing_df, config)].copy()

    selected_df = pd.DataFrame(selected_rows)
    if preserved_df.empty:
        combined_df = selected_df
    elif selected_df.empty:
        combined_df = preserved_df
    else:
        combined_df = pd.concat([preserved_df, selected_df], ignore_index=True, sort=False)

    if combined_df.empty:
        return combined_df
    return _sort_analysis_dataframe(combined_df)


def _remove_analysis_file_if_exists(analysis_path: Path, base_name: str):
    for suffix in ['.xlsx', '.csv']:
        candidate_path = analysis_path.joinpath(f'{base_name}{suffix}')
        if candidate_path.exists():
            candidate_path.unlink()

def _can_reuse_cached_result(
        manifest_entry: dict | None,
        fingerprint: str,
        instance_rows: pd.DataFrame,
        master_rows: pd.DataFrame,
        subproblem_rows: pd.DataFrame,
        do_instance_analysis: bool,
        do_master_result_analysis: bool,
        do_subproblem_result_analysis: bool) -> bool:
    if not isinstance(manifest_entry, dict):
        return False
    if str(manifest_entry.get('fingerprint', '')) != fingerprint:
        return False

    if do_instance_analysis and not bool(manifest_entry.get('has_instance_analysis', False)):
        return False
    if do_master_result_analysis and not bool(manifest_entry.get('has_master_result_analysis', False)):
        return False
    if do_subproblem_result_analysis and not bool(manifest_entry.get('has_subproblem_result_analysis', False)):
        return False

    expected_instance_count = int(manifest_entry.get('instance_count', 0))
    expected_master_count = int(manifest_entry.get('master_count', 0))
    expected_subproblem_count = int(manifest_entry.get('subproblem_count', 0))

    if do_instance_analysis and len(instance_rows) != expected_instance_count:
        return False
    if do_master_result_analysis and len(master_rows) != expected_master_count:
        return False
    if do_subproblem_result_analysis and len(subproblem_rows) != expected_subproblem_count:
        return False
    return True


def _get_cache_reuse_reasons(
        manifest_entry: dict | None,
        fingerprint: str,
        instance_rows: pd.DataFrame,
        master_rows: pd.DataFrame,
        subproblem_rows: pd.DataFrame,
        do_instance_analysis: bool,
        do_master_result_analysis: bool,
        do_subproblem_result_analysis: bool) -> list[str]:
    reasons: list[str] = []

    if not isinstance(manifest_entry, dict):
        return ['no cache entry']

    cached_fingerprint = str(manifest_entry.get('fingerprint', ''))
    if cached_fingerprint != fingerprint:
        reasons.append('fingerprint changed')

    if do_instance_analysis and not bool(manifest_entry.get('has_instance_analysis', False)):
        reasons.append('cache lacks instance analysis')
    if do_master_result_analysis and not bool(manifest_entry.get('has_master_result_analysis', False)):
        reasons.append('cache lacks master-result analysis')
    if do_subproblem_result_analysis and not bool(manifest_entry.get('has_subproblem_result_analysis', False)):
        reasons.append('cache lacks subproblem-result analysis')

    expected_instance_count = int(manifest_entry.get('instance_count', 0))
    expected_master_count = int(manifest_entry.get('master_count', 0))
    expected_subproblem_count = int(manifest_entry.get('subproblem_count', 0))

    if do_instance_analysis and len(instance_rows) != expected_instance_count:
        reasons.append(
            f'instance rows mismatch (cache={expected_instance_count}, found={len(instance_rows)})')
    if do_master_result_analysis and len(master_rows) != expected_master_count:
        reasons.append(
            f'master rows mismatch (cache={expected_master_count}, found={len(master_rows)})')
    if do_subproblem_result_analysis and len(subproblem_rows) != expected_subproblem_count:
        reasons.append(
            f'subproblem rows mismatch (cache={expected_subproblem_count}, found={len(subproblem_rows)})')

    return reasons


def _collect_instance_final_gap_analysis(
        result_directory: Path,
        run_config: dict,
        operator_total_duration: int,
        instance_analysis: dict[str, str | int | float]) -> dict[str, str | int | float]:
    analysis: dict[str, str | int | float] = {}

    problem_type = run_config.get('problem_type')

    # Single-pass / monolithic style output: take the global gap directly from
    # the top-level solver log produced by Gurobi.
    if problem_type == 'monolithic':
        solver_log_path = result_directory.joinpath('solver_log.log')
        if solver_log_path.exists():
            solver_log_analysis = analyze_log(solver_log_path)
            for key, value in solver_log_analysis.items():
                analysis[f'solver_{key}'] = value

            analysis['final_gap_source'] = 'monolithic_solver_log'
            analysis['final_gap_feasible_value'] = solver_log_analysis.get('objective_value', float('nan'))
            analysis['final_gap_upper_bound'] = solver_log_analysis.get('upper_bound', float('nan'))
            analysis.update(get_gap_metrics(
                solver_log_analysis.get('upper_bound'),
                solver_log_analysis.get('objective_value'),
                operator_total_duration,
                prefix='final_gap',
                pct_override=solver_log_analysis.get('gap')))

        return analysis

    # Iterative LBBD style output: the global feasible value is the best final
    # result found across the run, while the global upper bound is the one from
    # the last master iteration.
    latest_iteration_path = _get_latest_iteration_path(result_directory)
    if latest_iteration_path is None:
        return analysis

    latest_master_log_path = latest_iteration_path.joinpath('master_log.log')
    if not latest_master_log_path.exists():
        return analysis

    final_master_log_analysis = analyze_log(latest_master_log_path)
    for key, value in final_master_log_analysis.items():
        analysis[f'final_master_{key}'] = value

    final_objective_value = instance_analysis.get('objective_value')
    if final_objective_value is None:
        return analysis

    analysis['final_gap_source'] = 'lbbd_master_vs_best_final'
    analysis['final_gap_feasible_value'] = final_objective_value
    analysis['final_gap_upper_bound'] = final_master_log_analysis.get('upper_bound', float('nan'))
    analysis.update(get_gap_metrics(
        final_master_log_analysis.get('upper_bound'),
        final_objective_value,
        operator_total_duration,
        prefix='final_gap'))

    return analysis

def _collect_instance_run_status_analysis(run_status_payload: dict) -> dict[str, str | int | float]:
    if len(run_status_payload) == 0:
        return {}

    analysis: dict[str, str | int | float] = {}
    for key in [
            'status',
            'stage',
            'message',
            'error_code',
            'return_code',
            'timestamp',
            'stop_reason',
            'stop_iteration',
            'total_time_elapsed',
            'last_master_status']:
        if key in run_status_payload:
            analysis[f'run_{key}'] = run_status_payload[key]
    return analysis

def _classify_instance_final_status(
        run_config: dict,
        run_status_payload: dict,
        result_directory: Path,
        instance_analysis: dict[str, str | int | float]) -> dict[str, str]:
    accepted_stop_reasons = {
        'accepted_master_equals_final',
        'accepted_all_days_satisfied',
        'accepted_cache_equals_master',
        'accepted_optimum_approximation',
    }

    raw_run_status = str(run_status_payload.get('status', '')).strip().lower()
    raw_run_stage = str(run_status_payload.get('stage', '')).strip().lower()
    raw_run_stop_reason = str(run_status_payload.get('stop_reason', '')).strip().lower()

    if raw_run_stop_reason == 'exception' or raw_run_stage == 'exception':
        return {
            'status': 'failed_exception',
            'status_reason': 'exception',
        }

    if raw_run_stop_reason == 'memory_limit' or raw_run_stage == 'memory':
        return {
            'status': 'failed_memory_limit',
            'status_reason': 'memory_limit',
        }

    if raw_run_status not in ['', 'success']:
        reason = raw_run_stage if raw_run_stage != '' else 'process_failed'
        return {
            'status': 'failed_exception',
            'status_reason': reason,
        }

    final_gap_pct = pd.to_numeric(instance_analysis.get('final_gap_pct', float('nan')), errors='coerce')
    final_gap_feasible_value = pd.to_numeric(instance_analysis.get('final_gap_feasible_value', float('nan')), errors='coerce')
    final_gap_upper_bound = pd.to_numeric(instance_analysis.get('final_gap_upper_bound', float('nan')), errors='coerce')
    run_total_time_elapsed = pd.to_numeric(instance_analysis.get('run_total_time_elapsed', float('nan')), errors='coerce')
    total_time_limit = pd.to_numeric(run_config.get('total_time_limit', float('nan')), errors='coerce')
    problem_type = str(run_config.get('problem_type', '')).strip().lower()

    if problem_type == 'monolithic':
        solver_status = str(instance_analysis.get('solver_status', '')).strip().lower()
        if solver_status == 'optimal':
            return {
                'status': 'optimal',
                'status_reason': 'solver_optimal',
            }
        if solver_status == 'time_limit':
            if pd.notna(final_gap_feasible_value):
                return {
                    'status': 'time_limit_feasible',
                    'status_reason': 'solver_time_limit',
                }
            return {
                'status': 'time_limit_no_solution',
                'status_reason': 'solver_time_limit',
            }
        if solver_status == 'memory_limit':
            if pd.notna(final_gap_feasible_value):
                return {
                    'status': 'memory_limit_feasible',
                    'status_reason': 'solver_memory_limit',
                }
            return {
                    'status': 'memory_limit_no_solution',
                    'status_reason': 'solver_memory_limit',
                }
        if pd.notna(final_gap_feasible_value):
            return {
                'status': 'completed_nonoptimal',
                'status_reason': f'solver_{solver_status or "finished"}',
            }
        return {
            'status': 'unknown',
            'status_reason': 'missing_solver_result',
        }

    latest_iteration_path = _get_latest_iteration_path(result_directory)
    latest_iteration_index = _get_iteration_index(latest_iteration_path) if latest_iteration_path is not None else None
    max_iteration = pd.to_numeric(run_config.get('max_iteration', float('nan')), errors='coerce')

    effective_last_master_status = str(
        instance_analysis.get('run_last_master_status', instance_analysis.get('final_master_status', ''))
    ).strip().lower()
    gap_closed = bool(
        pd.notna(final_gap_pct)
        and float(final_gap_pct) <= FINAL_GAP_OPTIMAL_TOLERANCE_PCT)
    has_feasible_solution = pd.notna(final_gap_feasible_value)
    time_limit_reached = bool(
        pd.notna(run_total_time_elapsed)
        and pd.notna(total_time_limit)
        and float(run_total_time_elapsed) >= float(total_time_limit))

    if raw_run_stop_reason == 'total_time_limit':
        return {
            'status': 'total_time_limit_feasible' if has_feasible_solution else 'total_time_limit_no_solution',
            'status_reason': 'global_total_time_limit_reached',
        }

    if raw_run_stop_reason == 'max_iteration':
        return {
            'status': 'max_iteration_feasible' if has_feasible_solution else 'max_iteration_no_solution',
            'status_reason': 'maximum_iteration_reached',
        }

    if raw_run_stop_reason in accepted_stop_reasons:
        if effective_last_master_status == 'time_limit':
            return {
                'status': 'optimality_uncertain_master_time_limit',
                'status_reason': 'last_master_time_limit',
            }
        if time_limit_reached:
            return {
                'status': 'optimality_uncertain_total_time_limit',
                'status_reason': 'global_total_time_limit_reached',
            }
        return {
            'status': 'optimal',
            'status_reason': raw_run_stop_reason,
        }

    if gap_closed:
        if effective_last_master_status == 'time_limit':
            return {
                'status': 'optimality_uncertain_master_time_limit',
                'status_reason': 'last_master_time_limit',
            }
        return {
            'status': 'optimal',
            'status_reason': 'legacy_gap_closed_inference',
        }

    if latest_iteration_index is not None and pd.notna(max_iteration) and latest_iteration_index >= int(max_iteration):
        return {
            'status': 'max_iteration_feasible',
            'status_reason': 'maximum_iteration_reached',
        }

    if effective_last_master_status == 'time_limit' and has_feasible_solution:
        return {
            'status': 'master_time_limit_feasible',
            'status_reason': 'last_master_time_limit',
        }

    if has_feasible_solution:
        return {
            'status': 'completed_nonoptimal',
            'status_reason': 'completed_without_gap_closure',
        }

    if raw_run_status == 'success':
        return {
            'status': 'success_without_solution',
            'status_reason': 'process_completed_but_no_final_solution_detected',
        }

    return {
        'status': 'unknown',
        'status_reason': 'insufficient_information',
    }

# Definizione dei parametri a linea di comando
parser = ArgumentParser(prog='Analyzer')
parser.add_argument('-c', '--config', help='Location of the analysis configuration', type=Path, required=True)
parser.add_argument('-i', '--input', help='Location of the results', type=Path, required=True)
parser.add_argument('--overwrite', help='Force a full rebuild of the selected analysis outputs.', action='store_true')
args = parser.parse_args()

config_path = Path(args.config).resolve()
input_path = Path(args.input).resolve()

# Lettura della configurazione
with open(config_path, 'r') as file:
    config = yaml.load(file, yaml.CLoader)

# Backward-compatible defaults for older analyzer configs that only specify
# the *_to_do / *_to_avoid filters.
config.setdefault('do_instance_analysis', True)
config.setdefault('do_master_result_analysis', True)
config.setdefault('do_subproblem_result_analysis', True)

analysis_path = get_analysis_output_path(input_path, config)
incremental_reuse_enabled = not args.overwrite

existing_instance_df = pd.DataFrame()
existing_master_df = pd.DataFrame()
existing_subproblem_df = pd.DataFrame()
existing_cache_entries: dict[str, dict] = {}

if analysis_path.exists():
    existing_cache_entries = _load_analysis_cache_manifest(analysis_path)
    if config['do_instance_analysis']:
        existing_instance_df = _load_existing_analysis_dataframe(
            analysis_path,
            INSTANCE_ANALYSIS_BASENAME,
            'Master instance data')
    if config['do_master_result_analysis']:
        existing_master_df = _load_existing_analysis_dataframe(
            analysis_path,
            MASTER_RESULT_ANALYSIS_BASENAME)
    if config['do_subproblem_result_analysis']:
        existing_subproblem_df = _load_existing_analysis_dataframe(
            analysis_path,
            SUBPROBLEM_RESULT_ANALYSIS_BASENAME)

if incremental_reuse_enabled:
    if analysis_path.exists():
        print(
            f'[CACHE] Incremental reuse enabled on {analysis_path} | '
            f'cached dirs={len(existing_cache_entries)}, '
            f'instance rows={len(existing_instance_df)}, '
            f'master rows={len(existing_master_df)}, '
            f'subproblem rows={len(existing_subproblem_df)}')
    else:
        print(f'[CACHE] Incremental reuse enabled, but no previous analysis found at {analysis_path}')
else:
    print(
        f'[CACHE] Selected-scope rebuild requested with --overwrite on centralized analysis '
        f'{analysis_path}')

# Elenco dei dati da trasformare in DataFrame per ogni istanza di input,
# risultato delle iterazioni e risultato dei sottoproblemi
instance_data: list[dict[str, str | int | float]] = []
master_result_data: list[dict[str, str | int | float]] = []
subproblem_result_data: list[dict[str, str | int | float]] = []
updated_cache_entries: dict[str, dict] = {}
reused_directory_count = 0
reanalyzed_directory_count = 0
selected_directory_names_on_disk: set[str] = set()
selected_scope_changed = not analysis_path.exists()

# Iterazione di ogni cartella con i risultati
for result_directory in input_path.iterdir():
    if not result_directory.is_dir():
        continue
    if result_directory.name in ['analysis', 'plots']:
        continue
    
    # Il nome della cartella contiene le informazioni dell'istanza risolta al
    # suo interno (config__group__instance)
    tokens = result_directory.name.split('__')
    if len(tokens) != 3:
        continue

    config_name = tokens[0]
    group_name = tokens[1]
    instance_name = tokens[2]

    # Controllo se l'istanza deve essere esclusa dall'analisi
    if not is_combination_to_do(config_name, group_name, instance_name, config):
        continue

    selected_directory_names_on_disk.add(result_directory.name)

    result_directory_fingerprint = _compute_result_directory_fingerprint(result_directory)
    existing_instance_rows = _slice_existing_rows(existing_instance_df, config_name, group_name, instance_name)
    existing_master_rows = _slice_existing_rows(existing_master_df, config_name, group_name, instance_name)
    existing_subproblem_rows = _slice_existing_rows(existing_subproblem_df, config_name, group_name, instance_name)
    manifest_entry = existing_cache_entries.get(result_directory.name)
    reuse_reasons = _get_cache_reuse_reasons(
        manifest_entry,
        result_directory_fingerprint,
        existing_instance_rows,
        existing_master_rows,
        existing_subproblem_rows,
        config['do_instance_analysis'],
        config['do_master_result_analysis'],
        config['do_subproblem_result_analysis'])

    if incremental_reuse_enabled and len(reuse_reasons) == 0 and _can_reuse_cached_result(
            manifest_entry,
            result_directory_fingerprint,
            existing_instance_rows,
            existing_master_rows,
            existing_subproblem_rows,
            config['do_instance_analysis'],
            config['do_master_result_analysis'],
            config['do_subproblem_result_analysis']):
        if config['do_instance_analysis'] and len(existing_instance_rows) > 0:
            instance_data.extend(existing_instance_rows.to_dict('records'))
        if config['do_master_result_analysis'] and len(existing_master_rows) > 0:
            master_result_data.extend(existing_master_rows.to_dict('records'))
        if config['do_subproblem_result_analysis'] and len(existing_subproblem_rows) > 0:
            subproblem_result_data.extend(existing_subproblem_rows.to_dict('records'))
        updated_cache_entries[result_directory.name] = _merge_cache_manifest_entry(
            manifest_entry,
            result_directory_fingerprint,
            len(existing_instance_rows) if config['do_instance_analysis'] else 0,
            len(existing_master_rows) if config['do_master_result_analysis'] else 0,
            len(existing_subproblem_rows) if config['do_subproblem_result_analysis'] else 0,
            config['do_instance_analysis'],
            config['do_master_result_analysis'],
            config['do_subproblem_result_analysis'],
        )
        reused_directory_count += 1
        print(
            f'[REUSE] {result_directory.name}: unchanged -> skipping analysis '
            f'(instance rows={len(existing_instance_rows)}, '
            f'master rows={len(existing_master_rows)}, '
            f'subproblem rows={len(existing_subproblem_rows)})')
        continue

    if incremental_reuse_enabled:
        print(
            f'Analyzing directory {result_directory.name}... '
            f'cache miss ({", ".join(reuse_reasons) if len(reuse_reasons) > 0 else "not reusable"}) ',
            end='')
    else:
        print(f'Analyzing directory {result_directory.name}... full rebuild ', end='')
    start = time.perf_counter()
    instance_count_before = len(instance_data)
    master_count_before = len(master_result_data)
    subproblem_count_before = len(subproblem_result_data)

    # Lettura dell'istanza master di input
    master_instance_path = result_directory.joinpath('master_instance.json')
    if not master_instance_path.exists():
        master_instance_path = result_directory.joinpath('instance.json')
    if not master_instance_path.exists():
        print(f'Master instance not found in directory {result_directory.name}')
        continue
    with open(master_instance_path, 'r') as file:
        master_instance = decode_master_instance(json.load(file))
    
    run_config = _read_run_config(result_directory)
    run_status_payload = _read_run_status(result_directory)
    operator_total_duration = get_total_operator_duration(master_instance)
    # Analisi dell'istanza di input
    if config['do_instance_analysis']:

        instance_analysis: dict[str, str | int | float] = {
            'config': config_name,
            'group': group_name,
            'instance': instance_name,
        }

        instance_analysis.update(analyze_master_instance(master_instance))

        # Eventuale lettura ed analisi del risultato globale finale della run.
        best_final_result_path = result_directory.joinpath('best_final_result_so_far.json')
        if best_final_result_path.exists():
            with open(best_final_result_path, 'r') as file:
                best_final_result = decode_final_result(json.load(file))
            instance_analysis.update(analyze_final_result(master_instance, best_final_result))
        elif run_config.get('problem_type') == 'monolithic':
            final_result_path = result_directory.joinpath('result.json')
            if final_result_path.exists():
                with open(final_result_path, 'r') as file:
                    final_result = decode_final_result(json.load(file))
                instance_analysis.update(analyze_final_result(master_instance, final_result))

        instance_analysis.update(_collect_instance_run_status_analysis(run_status_payload))
        instance_analysis.update(_collect_instance_final_gap_analysis(
            result_directory,
            run_config,
            operator_total_duration,
            instance_analysis))
        instance_analysis.update(_classify_instance_final_status(
            run_config,
            run_status_payload,
            result_directory,
            instance_analysis))
        instance_data.append(instance_analysis)

    # Ciclo che analizza ogni iterazione
    for iteration_path in result_directory.iterdir():
        if not iteration_path.is_dir():
            continue
        if iteration_path.name in ['analysis', 'plots']:
            continue
        if not iteration_path.name.startswith('iter_'):
            continue

        # Il numero dell'iterazione è ottenuto dal nome della cartella
        # (es: iter_4 -> 4)
        iteration_index = int(iteration_path.name.split('_')[-1])

        if config['do_master_result_analysis']:

            result_analysis: dict[str, str | int | float] = {
                'config': config_name,
                'group': group_name,
                'instance': instance_name,
                'iteration': iteration_index
            }

            # Eventuale lettura dei file JSON presenti nella carella dell'iterazione
            # corrente
            for result_type in ['master', 'final', 'cache_final']:
                result_path = iteration_path.joinpath(f'{result_type}_result.json')
                if not result_path.exists():
                    continue
                
                with open(result_path, 'r') as file:
                    if result_type == 'master':
                        result = decode_master_result(json.load(file))
                    else:
                        result = decode_final_result(json.load(file))

                if isinstance(result, FinalResult):
                    result_type_analysis = analyze_final_result(master_instance, result)
                else:
                    result_type_analysis = analyze_master_result(master_instance, result)
                
                # I nomi delle caratteristiche vengono prefissi dal tipo di
                # risultato appena letto
                for key, value in result_type_analysis.items():
                    result_analysis[f'{result_type}_{key}'] = value
            
            # Eventuale lettura ed analisi dei file relativi ai core nella carella
            # dell'iterazione corrente
            for core_type in ['generalist', 'basic', 'reduced', 'pruned', 'preemptive', 'expanded']:
                cores_path = iteration_path.joinpath(f'{core_type}_cores.json')
                if not cores_path.exists():
                    continue
                
                with open(cores_path, 'r') as file:
                    cores = decode_cores(json.load(file))
                
                core_type_analysis = analyze_cores(master_instance, cores)

                # I nomi delle caratteristiche vengono prefissi dal tipo di core
                # appena letto
                for key, value in core_type_analysis.items():
                    result_analysis[f'{core_type}_{key}'] = value

            optimality_cut_stats_path = iteration_path.joinpath('optimality_cut_stats.json')
            if optimality_cut_stats_path.exists():
                with open(optimality_cut_stats_path, 'r') as file:
                    optimality_cut_stats = json.load(file)
                for key, value in optimality_cut_stats.items():
                    result_analysis[key] = value

            # Eventuale lettura ed analisi dei file relativi ai log nella carella
            # dell'iterazione corrente
            for log_type in ['master', 'cache']:
                log_path = iteration_path.joinpath(f'{log_type}_log.log')
                if not log_path.exists():
                    continue
                log_type_analysis = analyze_log(log_path)

                # I nomi delle caratteristiche vengono prefissi dal tipo di log
                # appena letto
                for key, value in log_type_analysis.items():
                    result_analysis[f'{log_type}_{key}'] = value

            # Se almeno un risultato è stato letto ed analizzato
            result_analysis.update(get_lbbd_final_gap_metrics(
                result_analysis.get('master_upper_bound'),
                result_analysis.get('final_objective_value'),
                operator_total_duration))

            if len(result_analysis) > 4:
                master_result_data.append(result_analysis)

        # Analizza ogni sottoproblema dell'iterazione corrente
        if config['do_subproblem_result_analysis']:
            for day_name in master_instance.days.keys():

                subproblem_result_analysys: dict[str, str | int | float] = {
                    'config': config_name,
                    'group': group_name,
                    'instance': instance_name,
                    'iteration': iteration_index,
                    'day': day_name
                }

                # Leggi ed analizza l'istanza di ogni sottoproblema
                subproblem_instance_path = iteration_path.joinpath(f'subproblem_day_{day_name}_instance.json')
                if subproblem_instance_path.exists():
                    with open(subproblem_instance_path, 'r') as file:
                        subproblem_instance = decode_subproblem_instance(json.load(file))
                    subproblem_result_analysys.update(analyze_subproblem_instance(subproblem_instance))
                
                    # Leggi ed analizza i risultati di ogni sottoproblema (solo se
                    # è presente anche la sua istanza di input)
                    subproblem_result_path = iteration_path.joinpath(f'subproblem_day_{day_name}_result.json')
                    if subproblem_result_path.exists():
                        with open(subproblem_result_path, 'r') as file:
                            subproblem_result = decode_subproblem_result(json.load(file))
                        subproblem_result_analysys.update(analyze_subproblem_result(subproblem_instance, subproblem_result))
                
                # Leggi ed analizza i log di ogni sottoproblema
                subproblem_log_path = iteration_path.joinpath(f'subproblem_day_{day_name}_log.log')
                if subproblem_log_path.exists():
                    subproblem_result_analysys.update(analyze_log(subproblem_log_path))
            
                # Se almeno un risultato è stato letto ed analizzato
                if len(subproblem_result_analysys) > 5:
                    subproblem_result_data.append(subproblem_result_analysys)
    
    end = time.perf_counter()
    print(f'done ({end - start:.04}s)')
    updated_cache_entries[result_directory.name] = _merge_cache_manifest_entry(
        manifest_entry,
        result_directory_fingerprint,
        len(instance_data) - instance_count_before if config['do_instance_analysis'] else 0,
        len(master_result_data) - master_count_before if config['do_master_result_analysis'] else 0,
        len(subproblem_result_data) - subproblem_count_before if config['do_subproblem_result_analysis'] else 0,
        config['do_instance_analysis'],
        config['do_master_result_analysis'],
        config['do_subproblem_result_analysis'],
    )
    reanalyzed_directory_count += 1
    selected_scope_changed = True

selected_existing_entry_names = {
    entry_name for entry_name in existing_cache_entries.keys()
    if _is_result_directory_in_selected_scope(entry_name, config)
}
stale_selected_entry_names = selected_existing_entry_names - selected_directory_names_on_disk
if len(stale_selected_entry_names) > 0:
    selected_scope_changed = True
    print(
        f'[CACHE] Removing {len(stale_selected_entry_names)} stale cached directories from the '
        'selected scope because they no longer exist in the results input.')

final_cache_entries = {
    entry_name: entry
    for entry_name, entry in existing_cache_entries.items()
    if not _is_result_directory_in_selected_scope(entry_name, config)
}
final_cache_entries.update(updated_cache_entries)

if config['do_instance_analysis']:
    final_instance_df = _replace_selected_scope_rows(existing_instance_df, instance_data, config)
else:
    final_instance_df = pd.DataFrame()

if config['do_master_result_analysis']:
    final_master_df = _replace_selected_scope_rows(existing_master_df, master_result_data, config)
else:
    final_master_df = pd.DataFrame()

if config['do_subproblem_result_analysis']:
    final_subproblem_df = _replace_selected_scope_rows(existing_subproblem_df, subproblem_result_data, config)
else:
    final_subproblem_df = pd.DataFrame()

if (
        not analysis_path.exists()
        and len(selected_directory_names_on_disk) == 0
        and len(final_cache_entries) == 0
        and len(final_instance_df) == 0
        and len(final_master_df) == 0
        and len(final_subproblem_df) == 0):
    print('No selected data to analyze.')
    exit(0)

if not selected_scope_changed:
    print('Central analysis already up to date for the selected scope. Nothing to rewrite.')
    print(
        'Analyzer incremental summary: '
        f'reused/skipped {reused_directory_count} directories as unchanged, '
        f'reanalyzed {reanalyzed_directory_count}.')
    exit(0)

# Eventuale creazione della cartella di analisi
if not analysis_path.exists():
    print(f'\'{analysis_path.relative_to(input_path)}\' directory does not exist, creating it')
    analysis_path.mkdir(parents=True)

print(
    'Updating centralized analysis files '
    f'({len(selected_directory_names_on_disk)} selected runs scanned, '
    f'{len(final_cache_entries)} cached runs retained overall)... ',
    end='')
start = time.perf_counter()

# Scrittura su file delle analisi delle istanze di input
if config['do_instance_analysis']:
    data_file_path = analysis_path.joinpath(f'{INSTANCE_ANALYSIS_BASENAME}.xlsx')
    if len(final_instance_df) > 0:
        with pd.ExcelWriter(data_file_path, engine='xlsxwriter') as writer:
            write_excel_sheet(final_instance_df, writer, 'Master instance data')
    else:
        _remove_analysis_file_if_exists(analysis_path, INSTANCE_ANALYSIS_BASENAME)

# Scrittura su CSV delle analisi dei risultati master per iterazione
if config['do_master_result_analysis']:
    data_file_path = analysis_path.joinpath(f'{MASTER_RESULT_ANALYSIS_BASENAME}.csv')
    if len(final_master_df) > 0:
        write_csv_table(final_master_df, data_file_path)
    else:
        _remove_analysis_file_if_exists(analysis_path, MASTER_RESULT_ANALYSIS_BASENAME)

# Scrittura su CSV delle analisi dei sottoproblemi
if config['do_subproblem_result_analysis']:
    data_file_path = analysis_path.joinpath(f'{SUBPROBLEM_RESULT_ANALYSIS_BASENAME}.csv')
    if len(final_subproblem_df) > 0:
        write_csv_table(final_subproblem_df, data_file_path)
    else:
        _remove_analysis_file_if_exists(analysis_path, SUBPROBLEM_RESULT_ANALYSIS_BASENAME)

_write_analysis_cache_manifest(analysis_path, final_cache_entries)

end = time.perf_counter()
print(f'done ({end - start:.04}s)')
if incremental_reuse_enabled:
    print(
        'Analyzer incremental summary: '
        f'reused/skipped {reused_directory_count} directories as unchanged, '
        f'reanalyzed {reanalyzed_directory_count}.')
