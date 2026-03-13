from argparse import ArgumentParser
from pathlib import Path
import yaml
import json
import time
import pandas as pd

from src.common.custom_types import FinalResult
from src.common.analysis_constants import FINAL_GAP_OPTIMAL_TOLERANCE_PCT
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

def _get_latest_iteration_path(result_directory: Path) -> Path | None:
    iteration_paths = [
        path for path in result_directory.iterdir()
        if path.is_dir() and path.name.startswith('iter_')
    ]
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

# Elenco dei dati da trasformare in DataFrame per ogni istanza di input,
# risultato delle iterazioni e risultato dei sottoproblemi
instance_data: list[dict[str, str | int | float]] = []
master_result_data: list[dict[str, str | int | float]] = []
subproblem_result_data: list[dict[str, str | int | float]] = []

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

    print(f'Analyzing directory {result_directory.name}... ', end='')
    start = time.perf_counter()

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
            'instance': instance_name
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

# Se non è stato analizzato niente
if len(instance_data) == 0 and len(master_result_data) == 0 and len(subproblem_result_data) == 0:
    print('No data to write')
    exit(0)

# Eventuale creazione della cartella di analisi
analysis_path = input_path.joinpath('analysis')
if not analysis_path.exists():
    print('\'analysis\' directory does not exist, creating it')
    analysis_path.mkdir()

print(f'Writing Excel files ({len(instance_data)} instances, {len(master_result_data)} master results and {len(subproblem_result_data)} subproblems)... ', end='')
start = time.perf_counter()

# Scrittura su file delle analisi delle istanze di input
if len(instance_data) > 0:
    df = pd.DataFrame(instance_data)
    data_file_path = analysis_path.joinpath('instance_analysis.xlsx')
    with pd.ExcelWriter(data_file_path, engine='xlsxwriter') as writer:
        write_excel_sheet(df, writer, 'Master instance data')

# Scrittura su file delle analisi dei risultati
if len(master_result_data) > 0:
    df = pd.DataFrame(master_result_data)
    data_file_path = analysis_path.joinpath('master_result_analysis.xlsx')
    with pd.ExcelWriter(data_file_path, engine='xlsxwriter') as writer:
        write_excel_sheet(df, writer, 'Master result data')

# Scrittura su file delle analisi dei sottoproblemi
if len(subproblem_result_data) > 0:
    df = pd.DataFrame(subproblem_result_data)
    data_file_path = analysis_path.joinpath('subproblem_result_analysis.xlsx')
    with pd.ExcelWriter(data_file_path, engine='xlsxwriter') as writer:
        write_excel_sheet(df, writer, 'Subproblem result data')

end = time.perf_counter()
print(f'done ({end - start:.04}s)')
