from argparse import ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import yaml
import json
import pandas as pd
import gc
from textwrap import dedent

from src.common.custom_types import SlimSubproblemResult, DayName, FatSubproblemResult
from src.common.file_load_and_dump import decode_master_instance, decode_final_result, decode_master_result
from src.common.file_load_and_dump import decode_subproblem_instance, decode_subproblem_result, decode_cores
from src.common.tools import get_slim_subproblem_instance_from_final_result, is_combination_to_do
from src.plotters.instance_plotter import plot_master_results, plot_subproblem_results
from src.plotters.result_value_vs_time import plot_result_value_vs_time
from src.plotters.cores import plot_core_info, plot_core_gantt
from src.plotters.solving_times import plot_solving_times
from src.plotters.solving_times_by_day import plot_solving_times_by_day
from src.plotters.requests_per_patient import plot_requests_per_patient
from src.plotters.aggregate_best_solution_value import plot_aggregate_best_solution_value
from src.plotters.equal_requests_between_iterations import plot_equal_requests_between_iterations
from src.plotters.experiment_group_comparison import plot_experiment_group_comparison

if __name__ != '__main__':
    exit(0)

ANALYTICAL_PLOTS = {
    'result_value_vs_time',
    'core_info',
    'solving_times',
    'solving_times_by_day',
    'requests_per_patient',
    'aggregate_best_solution_value',
    'experiment_group_comparison',
}


def _read_analysis_sheet(file_path: Path, sheet_name: str, required_columns: set[str]) -> pd.DataFrame:
    if len(required_columns) == 0:
        return pd.DataFrame()
    ordered_required_columns = sorted(required_columns)
    header_df = pd.read_excel(file_path, sheet_name=sheet_name, nrows=0)
    use_columns = [column_name for column_name in header_df.columns if column_name in required_columns]
    if len(use_columns) == 0:
        return pd.DataFrame(columns=ordered_required_columns)
    data_df = pd.read_excel(file_path, sheet_name=sheet_name, usecols=use_columns)
    return data_df.reindex(columns=ordered_required_columns)


def _get_required_master_columns(plots_to_do: list[str]) -> set[str]:
    columns = {'config', 'group', 'instance', 'iteration'}

    if 'result_value_vs_time' in plots_to_do:
        columns.update({
            'master_time',
            'master_objective_value',
            'cache_time',
            'cache_objective_value',
            'final_objective_value',
        })

    if 'core_info' in plots_to_do:
        columns.add('master_average_scheduled_request_duration_per_day')
        for core_type in ['generalist', 'basic', 'reduced', 'pruned', 'expanded']:
            columns.update({
                f'{core_type}_core_number',
                f'{core_type}_average_core_size',
                f'{core_type}_min_core_size',
                f'{core_type}_max_core_size',
                f'{core_type}_average_total_duration_per_core',
                f'{core_type}_min_total_duration_per_core',
                f'{core_type}_max_total_duration_per_core',
                f'{core_type}_average_care_unit_number_per_core',
                f'{core_type}_min_care_unit_number_per_core',
                f'{core_type}_max_care_unit_number_per_core',
            })

    if 'solving_times' in plots_to_do:
        columns.update({'master_time', 'cache_time'})

    if 'requests_per_patient' in plots_to_do:
        columns.update({
            'master_average_request_number_per_patient_same_day',
            'master_min_request_number_per_patient_same_day',
            'master_max_request_number_per_patient_same_day',
            'final_average_request_number_per_patient_same_day',
            'final_min_request_number_per_patient_same_day',
            'final_max_request_number_per_patient_same_day',
            'master_average_care_unit_used_per_patient_same_day',
            'master_min_care_unit_used_per_patient_same_day',
            'master_max_care_unit_used_per_patient_same_day',
            'final_average_care_unit_used_per_patient_same_day',
            'final_min_care_unit_used_per_patient_same_day',
            'final_max_care_unit_used_per_patient_same_day',
            'master_total_operator_used_per_patient',
            'master_min_operator_used_per_patient',
            'master_max_operator_used_per_patient',
            'final_total_operator_used_per_patient',
            'final_min_operator_used_per_patient',
            'final_max_operator_used_per_patient',
        })

    if 'aggregate_best_solution_value' in plots_to_do:
        columns.add('final_objective_value')

    if 'experiment_group_comparison' in plots_to_do:
        columns.update({
            'master_time',
            'master_status',
            'master_gap',
            'master_objective_value',
            'master_upper_bound',
            'final_objective_value',
            'final_total_scheduled_request_duration',
            'final_total_time_slots_remaining',
            'final_total_scheduled_request_number',
            'final_total_rejected_request_number',
            'expanded_core_number',
            'pruned_core_number',
            'reduced_core_number',
            'basic_core_number',
            'generalist_core_number',
            'preemptive_core_number',
            'expanded_average_core_size',
            'pruned_average_core_size',
            'reduced_average_core_size',
            'basic_average_core_size',
            'generalist_average_core_size',
            'preemptive_average_core_size',
        })

    return columns


def _get_required_subproblem_columns(plots_to_do: list[str]) -> set[str]:
    columns: set[str] = set()

    if any(plot_name in plots_to_do for plot_name in [
            'result_value_vs_time',
            'solving_times',
            'experiment_group_comparison']):
        columns.update({'config', 'group', 'instance', 'iteration', 'time'})

    if 'solving_times_by_day' in plots_to_do:
        columns.update({'config', 'group', 'instance', 'iteration', 'day', 'time', 'rejected_request_number'})

    return columns

def plot_instance(input_path: Path, output_path: Path, iteration_index: int):

    if not output_path.exists():
        print(f'\'{output_path.name}\' directory does not exist, creating it')
        output_path.mkdir()

    master_instance_path = input_path.joinpath('master_instance.json')
    if not master_instance_path.exists():
        print(f'Master instance not found in directory {input_path.name}')
        return
    with open(master_instance_path, 'r') as file:
        master_instance = decode_master_instance(json.load(file))
        
    final_result_path = input_path.joinpath(f'iter_{iteration_index}', 'final_result.json')
    if not final_result_path.exists():
        print(f'Final result not found in directory {final_result_path.name}')
        return
    with open(final_result_path, 'r') as file:
        final_result = decode_final_result(json.load(file))
    
    master_result_path = input_path.joinpath(f'iter_{iteration_index}', 'master_result.json')
    if not master_result_path.exists():
        print(f'Master result not found in directory {master_result_path.name}')
        return
    with open(master_result_path, 'r') as file:
        master_result = decode_master_result(json.load(file))

    plot_master_results(master_instance, master_result,
        output_path.joinpath(f'master_result.png'),
        f'Master result of iteration {iteration_index} of \'{input_path.name}\'')
    plot_master_results(master_instance, final_result,
        output_path.joinpath(f'final_result.png'),
        f'Final result of iteration {iteration_index} of \'{input_path.name}\'')

    for day_name in final_result.scheduled.keys():
        subproblem_instance_path = input_path.joinpath(f'iter_{iteration_index}', f'subproblem_day_{day_name}_instance.json')
        with open(subproblem_instance_path, 'r') as file:
            subproblem_instance = decode_subproblem_instance(json.load(file))
        subproblem_result_path = input_path.joinpath(f'iter_{iteration_index}', f'subproblem_day_{day_name}_result.json')
        with open(subproblem_result_path, 'r') as file:
            subproblem_result = decode_subproblem_result(json.load(file))
        plot_subproblem_results(subproblem_instance, subproblem_result,
            output_path.joinpath(f'subproblem_day_{day_name}.png'), 
            f'Day {day_name} of iteration {iteration_index} of \'{input_path.name}\'')

parser = ArgumentParser(
    prog='plotter.py',
    formatter_class=RawTextHelpFormatter,
    description=dedent(
        """\
        Generate plots from solver results.

        Modes:
          all       Batch plotting over a results root using a plotter YAML config.
          instance  Detailed plotting for a single solved instance and one iteration.

        Notes:
          - Batch analytical plots require analyzer outputs in <results>/analysis/.
          - Structural plots (best_instance, core_gantt, instance mode) read raw JSON results.
        """),
    epilog=dedent(
        """\
        Examples:
          python plotter.py all -c configs/plotter_config.yaml -i results
          python plotter.py all -c configs/plotter_config.yaml -i prova_results
          python plotter.py instance -i results/test__group__inst_00 -o plots_single --iter 3

        Batch plots currently recognized in plots_to_do:
          best_instance
          best_instance_subproblems
          core_gantt
          result_value_vs_time
          core_info
          solving_times
          solving_times_by_day
          requests_per_patient
          equal_requests_between_iterations
          aggregate_best_solution_value  (currently incomplete)
          experiment_group_comparison
        """))
sub_parsers = parser.add_subparsers(dest='command', metavar='{all,instance}')
sub_parsers.required = True

parser_all = sub_parsers.add_parser(
    'all',
    formatter_class=RawTextHelpFormatter,
    help='Generate all requested batch plots for a results root.',
    description=dedent(
        """\
        Generate batch plots under each result directory selected by the YAML config.

        Expected input layout:
          <results_root>/
            analysis/
              instance_analysis.xlsx
              master_result_analysis.xlsx
              subproblem_result_analysis.xlsx
            <config>__<group>__<instance>/
              master_instance.json
              best_final_result_so_far.json
              iter_1/
              ...

        The exact plots produced depend on plots_to_do in the config file.
        """),
    epilog=dedent(
        """\
        Example:
          python plotter.py all -c configs/plotter_config.yaml -i results
        """))
parser_all.add_argument(
    '-c', '--config',
    help='Path to the plotter YAML config (filters + plots_to_do).',
    type=Path,
    required=True)
parser_all.add_argument(
    '-i', '--input',
    help='Root directory containing solver results, e.g. results/ or prova_results/.',
    type=Path,
    required=True)

parser_single = sub_parsers.add_parser(
    'instance',
    formatter_class=RawTextHelpFormatter,
    help='Generate detailed plots for one solved instance and one iteration.',
    description=dedent(
        """\
        Generate detailed plots for a single solved instance directory.

        Expected input layout:
          <result_dir>/
            master_instance.json
            iter_<k>/
              master_result.json
              final_result.json
              subproblem_day_<d>_instance.json
              subproblem_day_<d>_result.json

        Output files:
          master_result.png
          final_result.png
          subproblem_day_<d>.png
        """),
    epilog=dedent(
        """\
        Example:
          python plotter.py instance -i results/test__group__inst_00 -o plots_single --iter 3
        """))
parser_single.add_argument(
    '-i', '--input',
    help='Path to one result directory <config>__<group>__<instance>.',
    type=Path,
    required=True)
parser_single.add_argument(
    '-o', '--output',
    help='Directory where the generated PNG files will be written.',
    type=Path,
    required=True)
parser_single.add_argument(
    '--iter',
    help='Iteration index to visualize, e.g. 1, 2, 3...',
    type=int,
    required=True)

args = parser.parse_args()

if args.command == 'instance':
    
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    iteration_index = int(args.iter)
    
    plot_instance(input_path, output_path, iteration_index)
    
    exit(0)

config_path = Path(args.config).resolve()
input_path = Path(args.input).resolve()

with open(config_path, 'r') as file:
    config = yaml.load(file, yaml.CLoader)

if len(config['plots_to_do']) == 0:
    exit(0)

if 'best_instance' in config['plots_to_do'] or 'best_instance_subproblems' in config['plots_to_do'] or 'core_gantt' in config['plots_to_do']:

    for result_directory in input_path.iterdir():
        if not result_directory.is_dir():
            continue
        if result_directory.name in ['analysis', 'plots']:
            continue
        
        tokens = result_directory.name.split('__')
        if len(tokens) != 3:
            continue

        config_name = tokens[0]
        group_name = tokens[1]
        instance_name = tokens[2]

        if not is_combination_to_do(config_name, group_name, instance_name, config):
            continue

        plots_path = result_directory.joinpath('plots')
        if not plots_path.exists():
            print('\'plots\' directory does not exist, creating it')
            plots_path.mkdir()

        print(f'Plotting instance in {result_directory.name}... ', end='', flush=True)

        master_instance_path = result_directory.joinpath('master_instance.json')
        if not master_instance_path.exists():
            print(f'Master instance not found in directory {result_directory.name}, no instance plots')
            continue
        with open(master_instance_path, 'r') as file:
            master_instance = decode_master_instance(json.load(file))
        
        if 'best_instance' in config['plots_to_do'] or 'best_instance_subproblems' in config['plots_to_do']:
            
            best_final_result_path = result_directory.joinpath('best_final_result_so_far.json')
            if not best_final_result_path.exists():
                print(f'Final result not found in directory {result_directory.name}, no instance plots')
                continue
            with open(best_final_result_path, 'r') as file:
                best_final_result = decode_final_result(json.load(file))
            
            best_plot_path = plots_path.joinpath(f'best_result')
            best_plot_path.mkdir(exist_ok=True)

        if 'best_instance' in config['plots_to_do']:
            plot_master_results(master_instance, best_final_result, # type: ignore
                best_plot_path.joinpath(f'final_result.png'), # type: ignore
                f'Final result of instance \'{instance_name}\' of group \'{group_name}\' solved with \'{config_name}\'')

        if 'best_instance_subproblems' in config['plots_to_do']:
            for day_name in best_final_result.scheduled.keys(): # type: ignore
                subproblem_instance = get_slim_subproblem_instance_from_final_result(master_instance, best_final_result, day_name) # type: ignore
                plot_subproblem_results(
                    subproblem_instance, SlimSubproblemResult(best_final_result.scheduled[day_name]), # type: ignore
                    best_plot_path.joinpath(f'subproblem_day_{day_name}.png'), f'Best result day {day_name}') # type: ignore

        if 'core_gantt' in config['plots_to_do']:

            core_plot_path = plots_path.joinpath(f'cores')
            core_plot_path.mkdir(exist_ok=True)

            iteration_index = 1
            iteration_path = result_directory.joinpath(f'iter_{iteration_index}')
            
            while iteration_path.exists():
                cores_path = iteration_path.joinpath('pruned_cores.json')
                if not cores_path.exists():
                    cores_path = iteration_path.joinpath('reduced_cores.json')
                    if not cores_path.exists():
                        cores_path = iteration_path.joinpath('basic_cores.json')
                        if not cores_path.exists():
                            cores_path = iteration_path.joinpath('generalist_cores.json')

                if not cores_path.exists():
                    print(f'Core file not found in iteration {iteration_index - 1} in directory {result_directory.name}, no core plots')
                    iteration_index += 1
                    iteration_path = result_directory.joinpath(f'iter_{iteration_index}')
                    continue
                
                with open(cores_path, 'r') as file:
                    cores = decode_cores(json.load(file))
                
                core_days = set([
                    int(core.day[0]) if isinstance(core.day, (list, tuple)) else int(core.day)
                    for core in cores
                ])
                all_subproblem_result: dict[DayName, FatSubproblemResult] | dict[DayName, SlimSubproblemResult] = {}

                for day_name in core_days:
                    subproblem_result_path = iteration_path.joinpath(f'subproblem_day_{day_name}_result.json')
                    if not subproblem_result_path.exists():
                        continue
                    with open(subproblem_result_path, 'r') as file:
                        all_subproblem_result[day_name] = decode_subproblem_result(json.load(file)) # type: ignore
                
                iteration_plots_path = core_plot_path.joinpath(f'iter_{iteration_index - 1}')
                iteration_plots_path.mkdir(exist_ok=True)
                
                plot_core_gantt(master_instance, cores, iteration_plots_path, all_subproblem_result,
                    f'Core of instance \'{instance_name}\' of group \'{group_name}\' solved with \'{config_name}\'')
                
                iteration_index += 1
                iteration_path = result_directory.joinpath(f'iter_{iteration_index}')

        print(f'done')

master_result_df = pd.DataFrame()
subproblem_result_df = pd.DataFrame()

if any(plot_name in config['plots_to_do'] for plot_name in ANALYTICAL_PLOTS):
    print('Loading Excel data...', end='')
    master_required_columns = _get_required_master_columns(config['plots_to_do'])
    subproblem_required_columns = _get_required_subproblem_columns(config['plots_to_do'])

    master_result_df = _read_analysis_sheet(
        input_path.joinpath('analysis', 'master_result_analysis.xlsx'),
        'Master result data',
        master_required_columns)
    subproblem_result_df = _read_analysis_sheet(
        input_path.joinpath('analysis', 'subproblem_result_analysis.xlsx'),
        'Subproblem result data',
        subproblem_required_columns)
    print('done')

if 'result_value_vs_time' in config['plots_to_do']:
    print('Plotting \'result_value_vs_time\'')
    plot_result_value_vs_time(master_result_df, subproblem_result_df, input_path, config)
    gc.collect()

if 'core_info' in config['plots_to_do']:
    print('Plotting \'core_info\'')
    plot_core_info(master_result_df, input_path, config)
    gc.collect()

if 'solving_times' in config['plots_to_do']:
    print('Plotting \'solving_times\'')
    plot_solving_times(master_result_df, subproblem_result_df, input_path, config)
    gc.collect()

if 'solving_times_by_day' in config['plots_to_do']:
    print('Plotting \'solving_times_by_day\'')
    plot_solving_times_by_day(subproblem_result_df, input_path, config)
    gc.collect()

if 'requests_per_patient' in config['plots_to_do']:
    print('Plotting \'requests_per_patient\'')
    plot_requests_per_patient(master_result_df, input_path, config)
    gc.collect()

if 'equal_requests_between_iterations' in config['plots_to_do']:
    print('Plotting \'equal_requests_between_iterations\'')
    plot_equal_requests_between_iterations(input_path, config)
    gc.collect()

if 'aggregate_best_solution_value' in config['plots_to_do']:
    print('Plotting \'aggregate_best_solution_value\'')
    plot_aggregate_best_solution_value(master_result_df, input_path, config)
    gc.collect()

if 'experiment_group_comparison' in config['plots_to_do']:
    print('Plotting \'experiment_group_comparison\'')
    plot_experiment_group_comparison(master_result_df, subproblem_result_df, input_path, config)
    gc.collect()

print('Plotting process done')
