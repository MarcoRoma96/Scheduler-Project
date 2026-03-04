from argparse import ArgumentParser
from pathlib import Path
import logging
import json
import subprocess
import sys
import traceback
import yaml
import time

# Soppressione dell'output a terminale degli avvertimenti di Pyomo
logging.getLogger('pyomo.core').setLevel(logging.ERROR)

from src.common.custom_types import MasterInstance, SlimMasterResult
from src.common.custom_types import FatSubproblemResult, SlimSubproblemResult, FinalResult
from src.common.custom_types import FatMasterResult, FatSubproblemInstance, SlimSubproblemInstance
from src.common.config_merge import merge_group_config
from src.common.tools import is_combination_to_do
from src.common.isolated_worker import (
    RUN_STATUS_FILENAME,
    write_run_status,
    build_worker_preexec,
    describe_worker_returncode,
)
from src.common.solver_factory import (
    get_solver_name,
    build_solver,
    has_usable_solution,
    describe_solver_result,
    load_usable_solution,
)
from src.common.file_load_and_dump import decode_master_instance, encode_master_instance, encode_master_result
from src.common.file_load_and_dump import decode_subproblem_instance
from src.common.file_load_and_dump import encode_subproblem_instance, encode_subproblem_result
from src.common.file_load_and_dump import encode_final_result

from src.checkers.check_master_instance import check_master_instance
from src.checkers.check_master_result import check_fat_master_result, check_slim_master_result
from src.checkers.check_subproblem_instance import check_fat_subproblem_instance, check_slim_subproblem_instance
from src.checkers.check_subproblem_result import check_subproblem_result
from src.checkers.check_final_result import check_final_result

from src.milp_models.master_model import get_fat_master_model, get_slim_master_model
from src.milp_models.master_model import get_result_from_fat_master_model, get_result_from_slim_master_model
from src.milp_models.subproblem_model import get_fat_subproblem_model, get_slim_subproblem_model
from src.milp_models.subproblem_model import get_result_from_fat_subproblem_model, get_result_from_slim_subproblem_model
from src.milp_models.monolithic_model import get_monolithic_model, get_result_from_monolithic_model


# Questo script può essere chiamato solo direttamente dalla linea di comando
if __name__ != '__main__':
    exit(0)


def get_preliminary_solving_info(
        config,
        input_path: Path,
        can_overwrite: bool) -> dict[tuple[str, str], int]:
    '''Funzione che stampa a video le informazioni delle istanza che saranno
    effettivamente risolte, data la configurazione corrente. Ritorna un
    dizionario contenente i numeri di istanze da risolvere, indicizzate per
    nome della configurazione e nome del gruppo.'''

    infos: dict[tuple[str, str], int] = {}

    print('\n************************** [PRELIMINARY INFORMATIONS] **************************')

    total_instances_to_solve = 0
    total_groups_to_solve = 0

    base_config = config['base']

    # Calcolo di ogni configurazione
    for config_name, config_diff_from_base in config['groups'].items():

        # Creazione della configurazione del gruppo corrente, sovrascrivendo
        # alcuni parametri
        group_config = merge_group_config(base_config, config_diff_from_base)

        # Controllo se la configurazione deve essere esclusa dalla risoluzione
        if not is_combination_to_do(config_name, None, None, group_config):
            continue
        
        total_groups_to_solve += 1
        instances_number_to_solve_by_config = 0
        group_number_to_solve_by_config = 0
        
        # Iterazione dei gruppi di istanze
        for input_group_path in input_path.iterdir():
            if not input_group_path.is_dir():
                continue

            # Controllo se il gruppo deve essere escluso dalla risoluzione
            group_name = input_group_path.name
            if not is_combination_to_do(config_name, group_name, None, group_config):
                continue

            instance_number_of_group = 0
            group_number_to_solve_by_config += 1

            # Iterazione di ogni istanza del gruppo
            for input_instance_path in input_group_path.iterdir():
                if input_instance_path.suffix != '.json':
                    continue

                # Controllo se l'istanza deve essere esclusa dalla risoluzione
                instance_name = input_instance_path.stem
                if not is_combination_to_do(config_name, group_name, instance_name, group_config):
                    continue
                
                # Controllo sulla precedente presenza della cartella dei
                # risultati correnti
                group_path = output_path.joinpath(f'{config_name}__{group_name}__{input_instance_path.stem}')
                if not can_overwrite and group_path.exists():
                    print(f'WARNING: directory {group_path.name} already exists, will not be considered.')
                else:
                    instance_number_of_group += 1
            
            # Stampa di un avvertiento se il gruppo non ha istanza valide
            if instance_number_of_group == 0:
                print(f'WARNING: instance group {input_group_path.name} has no valid instances to solve')
            
            total_instances_to_solve += instance_number_of_group
            instances_number_to_solve_by_config += instance_number_of_group

            infos[config_name, group_name] = instance_number_of_group
        
        # Stampa di un avvertiento se la configurazione non ha gruppi validi
        if group_number_to_solve_by_config == 0:
            print(f'WARNING: configuration {config_name} has no valid groups to solve')
        else:
            print(f'Configuration \'{config_name}\' will be solving {instances_number_to_solve_by_config} instances in {group_number_to_solve_by_config} groups')
    
    # Stampa di un avvertimento se non viene risolto nulla
    if total_groups_to_solve == 0:
        print(f'WARNING: no configuration found')
    else:
        print(f'{total_groups_to_solve} configurations will be solving {total_instances_to_solve} instances overall; some may be the same, repeated in different groups')

    print('*********************** [END OF PRELIMINARY INFORMATIONS] **********************\n')

    return infos


def solve_instance(
        instance: MasterInstance | FatSubproblemInstance | SlimSubproblemInstance,
        config,
        output_path: Path,
        summary_lines: list[str],
        verbose: bool = False) -> int:
    '''Funzione che risolve un'istanza con la configurazione fornita.'''

    print('********************************************************************************')
    for line in summary_lines:
        print(line)

    solver_name = get_solver_name(config)
    opt = build_solver(
        solver_name,
        config['solver']['time_limit'],
        config['solver']['memory_limit'],
        config['solver'])

    # Copia dell'istanza nella cartella dei risultati
    with open(output_path.joinpath('instance.json'), 'w') as file:
        if isinstance(instance, MasterInstance):
            json.dump(encode_master_instance(instance), file, indent=4)
        else:
            json.dump(encode_subproblem_instance(instance), file, indent=4)
    
    # Copia della configurazione nella cartella dei risultati
    with open(output_path.joinpath('config.yaml'), 'w') as file:
        yaml.dump(config, file, indent=4, sort_keys=False)

    # Controlli di validità dell'istanza
    if isinstance(instance, MasterInstance):
        errors = check_master_instance(instance)
    elif isinstance(instance, FatSubproblemInstance):
        errors = check_fat_subproblem_instance(instance)
    else:
        errors = check_slim_subproblem_instance(instance)
    if len(errors) > 0:
        for error in errors:
            print(f'ERROR: {error}')
        return 1

    # Creazione del modello MILP
    print('Start model creation...', end='')
    start = time.perf_counter()
    if isinstance(instance, MasterInstance):
        if config['problem_type'] == 'monolithic':
            model = get_monolithic_model(instance, config['solver']['additional_info'])
        elif config['problem_type'] == 'fat-master':
            model = get_fat_master_model(instance, config['solver']['additional_info'])
        elif config['problem_type'] == 'slim-master':
            model = get_slim_master_model(instance, config['solver']['additional_info'])
    elif isinstance(instance, FatSubproblemInstance):
        model = get_slim_subproblem_model(instance)
    else:
        model = get_fat_subproblem_model(instance, config['solver']['additional_info'])
    end = time.perf_counter()
    print(f'done ({end - start:.04}s)')

    # Risoluzione del problema
    print(f'Start solving...', end='')
    start = time.perf_counter()
    solve_kwargs = {
        'logfile': output_path.joinpath('solver_log.log'),
        'load_solutions': False,
    }
    if verbose:
        solve_kwargs['tee'] = True
    solve_result = opt.solve(model, **solve_kwargs) # type: ignore
    end = time.perf_counter()
    print(f'done ({end - start:.04}s)', end='')
    if end - start >= config['solver']['time_limit']:
        print(' [TIME LIMIT]')
    else:
        print('')
    if not has_usable_solution(solve_result):
        print(f'ERROR: solver returned no usable solution ({describe_solver_result(solve_result)})')
        return 3
    if not load_usable_solution(model, solve_result):
        print(f'ERROR: solver returned an incumbent but Pyomo could not load it ({describe_solver_result(solve_result)})')
        return 3

    # Ottenimento dei risultati
    if config['problem_type'] == 'monolithic':
        result = get_result_from_monolithic_model(model) # type: ignore
    elif config['problem_type'] == 'fat-master':
        result = get_result_from_fat_master_model(model) # type: ignore
    elif config['problem_type'] == 'slim-master':
        result = get_result_from_slim_master_model(model) # type: ignore
    elif config['problem_type'] == 'fat-subproblem':
        result = get_result_from_fat_subproblem_model(model) # type: ignore
    else:
        result = get_result_from_slim_subproblem_model(model) # type: ignore

    # Salvataggio dei risultati
    with open(output_path.joinpath('result.json'), 'w') as file:
        if isinstance(result, FatMasterResult) or isinstance(result, SlimMasterResult):
            json.dump(encode_master_result(result), file, indent=4)
        elif isinstance(result, FatSubproblemResult) or isinstance(result, SlimSubproblemResult):
            json.dump(encode_subproblem_result(result), file, indent=4)
        else:
            json.dump(encode_final_result(result), file, indent=4)

    # Controllo dei risultati
    if isinstance(result, FatMasterResult) and isinstance(instance, MasterInstance):
        errors = check_fat_master_result(instance, result)
    elif isinstance(result, SlimMasterResult) and isinstance(instance, MasterInstance):
        errors = check_slim_master_result(instance, result)
    elif isinstance(result, FatSubproblemResult) and isinstance(instance, FatSubproblemInstance):
        errors = check_subproblem_result(instance, result)
    elif isinstance(result, SlimSubproblemResult) and isinstance(instance, SlimSubproblemInstance):
        errors = check_subproblem_result(instance, result)
    elif isinstance(result, FinalResult) and isinstance(instance, MasterInstance):
        errors = check_final_result(instance, result)
    if len(errors) > 0:
        for error in errors:
            print(f'ERROR: {error}')
        return 2
    
    print('********************************************************************************\n')

    return 0


def _build_worker_command(
        config_path: Path,
        input_path: Path,
        output_path: Path,
        config_name: str,
        group_name: str,
        instance_path: Path,
        solving_path: Path,
        verbose: bool) -> list[str]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        '-c', str(config_path),
        '-i', str(input_path),
        '-o', str(output_path),
        '--_worker',
        '--_worker-config-name', config_name,
        '--_worker-group-name', group_name,
        '--_worker-instance-path', str(instance_path),
        '--_worker-output-path', str(solving_path),
    ]
    if verbose:
        cmd.append('--verbose')
    return cmd


def _run_instance_worker(
        config_path: Path,
        input_path: Path,
        output_path: Path,
        config_name: str,
        group_name: str,
        instance_name: str,
        instance_path: Path,
        solving_path: Path,
        group_config,
        verbose: bool) -> int:
    worker_command = _build_worker_command(
        config_path,
        input_path,
        output_path,
        config_name,
        group_name,
        instance_path,
        solving_path,
        verbose,
    )

    worker_kwargs: dict = {
        'cwd': str(Path(__file__).resolve().parent),
    }
    preexec_fn = build_worker_preexec(group_config.get('solver', {}).get('memory_limit'))
    if preexec_fn is not None and (sys.platform.startswith('linux') or sys.platform == 'darwin'):
        worker_kwargs['preexec_fn'] = preexec_fn

    result = subprocess.run(worker_command, **worker_kwargs)
    if result.returncode == 0:
        return 0

    message = describe_worker_returncode(result.returncode)

    if not solving_path.joinpath(RUN_STATUS_FILENAME).exists():
        write_run_status(
            solving_path,
            status='failed',
            config_name=config_name,
            group_name=group_name,
            instance_name=instance_name,
            message=message,
            return_code=result.returncode,
            stage='worker',
        )

    print(f'[FAIL] {config_name} / {group_name} / {instance_name}: {message}')
    return result.returncode


def _run_worker_mode(args) -> int:
    config_path = Path(args.config).resolve()
    instance_path = Path(args._worker_instance_path).resolve()
    solving_path = Path(args._worker_output_path).resolve()
    config_name = str(args._worker_config_name)
    group_name = str(args._worker_group_name)
    instance_name = instance_path.stem
    verbose = bool(args.verbose)

    with open(config_path, 'r') as file:
        config = yaml.load(file, yaml.CLoader)

    base_config = config['base']
    group_config = merge_group_config(base_config, config['groups'][config_name])

    solving_path.mkdir(exist_ok=True)

    with open(instance_path, 'r') as file:
        if group_config['problem_type'] in ['monolithic', 'fat-master', 'slim-master']:
            instance = decode_master_instance(json.load(file))
        else:
            instance = decode_subproblem_instance(json.load(file))

    summary_lines = [
        f"Solving instance '{instance_name}' of group '{group_name}' with config '{config_name}'",
        'Worker mode',
    ]

    try:
        error_code = solve_instance(
            instance,
            group_config,
            solving_path,
            summary_lines,
            verbose=verbose)
        if error_code == 0:
            write_run_status(
                solving_path,
                status='success',
                config_name=config_name,
                group_name=group_name,
                instance_name=instance_name,
                message='Instance solved successfully.',
                error_code=0,
                stage='completed',
            )
        else:
            write_run_status(
                solving_path,
                status='failed',
                config_name=config_name,
                group_name=group_name,
                instance_name=instance_name,
                message=f'Solver returned error code {error_code}.',
                error_code=error_code,
                stage='solver',
            )
        return error_code
    except MemoryError as exc:
        write_run_status(
            solving_path,
            status='failed',
            config_name=config_name,
            group_name=group_name,
            instance_name=instance_name,
            message=f'MemoryError: {exc}',
            error_code=90,
            stage='memory',
        )
        print(f'ERROR: MemoryError while solving {instance_name}')
        return 90
    except Exception as exc:
        write_run_status(
            solving_path,
            status='failed',
            config_name=config_name,
            group_name=group_name,
            instance_name=instance_name,
            message=f'{type(exc).__name__}: {exc}',
            error_code=91,
            stage='exception',
        )
        traceback.print_exc()
        return 91


# Definizione dei parametri a linea di comando
parser = ArgumentParser(prog='Iterative instance solver')
parser.add_argument('-c', '--config', help='Location of the solving configuration', type=Path, required=True)
parser.add_argument('-i', '--input', help='Location of instance groups', type=Path, required=True)
parser.add_argument('-o', '--output', help='Where the output will be written', type=Path, required=True)
parser.add_argument('--overwrite', help='If output can overwrite previous files', action='store_true')
parser.add_argument(
    '--verbose',
    help='Stream the underlying solver output (GLPK/Gurobi) live to stdout.',
    action='store_true')
parser.add_argument('--_worker', action='store_true', help='Internal flag: solve a single instance in an isolated subprocess.')
parser.add_argument('--_worker-config-name', type=str)
parser.add_argument('--_worker-group-name', type=str)
parser.add_argument('--_worker-instance-path', type=Path)
parser.add_argument('--_worker-output-path', type=Path)
args = parser.parse_args()

config_path = Path(args.config).resolve()
input_path = Path(args.input).resolve()
output_path = Path(args.output).resolve()
can_overwrite = bool(args.overwrite)
verbose = bool(args.verbose)

output_path.mkdir(exist_ok=True)

if args._worker:
    exit(_run_worker_mode(args))

# Lettura della configurazione
with open(config_path, 'r') as file:
    config = yaml.load(file, yaml.CLoader)

infos = get_preliminary_solving_info(config, input_path, can_overwrite)
total_instance_attempted = 0
total_instance_succeeded = 0
total_instances_to_solve = sum(infos.values())

base_config = config['base']

# Iterazione di ogni configurazione
for config_name, config_diff_from_base in config['groups'].items():

    # Creazione della configurazione del gruppo corrente, sovrascrivendo alcuni
    # parametri
    group_config = merge_group_config(base_config, config_diff_from_base)
    
    # Controllo se la configurazione deve essere esclusa dall'analisi
    if not is_combination_to_do(config_name, None, None, group_config):
        continue
    
    instance_solved_of_this_config = 0

    # Iterazione dei gruppi di istanze
    for input_group_path in input_path.iterdir():
        if not input_group_path.is_dir():
            continue

        # Controllo se il gruppo deve essere escluso dall'analisi
        group_name = input_group_path.name
        if not is_combination_to_do(config_name, group_name, None, group_config):
            continue
    
        instance_solved_of_this_group = 0

        # Iterazione di ogni istanza del gruppo
        for input_instance_path in input_group_path.iterdir():
            if input_instance_path.suffix != '.json':
                continue

            # Controllo se l'istanza deve essere esclusa dall'analisi
            instance_name = input_instance_path.stem
            if not is_combination_to_do(config_name, group_name, instance_name, group_config):
                continue

            # Eventuale creazione della cartella dei risultati dell'istanza
            # corrente
            solving_path = output_path.joinpath(f'{config_name}__{group_name}__{instance_name}')
            if not can_overwrite and solving_path.exists():
                print(f'Directory {solving_path} already exists.')
                continue
            solving_path.mkdir(exist_ok=True)

            # Aggiornamento dei contatori
            instance_solved_of_this_group += 1
            instance_solved_of_this_config += 1
            total_instance_attempted += 1

            print('********************************************************************************')
            print(f"Solving instance '{instance_name}' of group '{group_name}' with config '{config_name}'")
            print(f'{instance_solved_of_this_group}/{infos[config_name, group_name]} instance of this group, {instance_solved_of_this_config}/{sum(n for cg, n in infos.items() if cg[0] == config_name)} instance of this config')
            print(f'{total_instance_attempted}/{total_instances_to_solve} instance solving in total')
            print('Launching isolated worker...')

            return_code = _run_instance_worker(
                config_path,
                input_path,
                output_path,
                config_name,
                group_name,
                instance_name,
                input_instance_path,
                solving_path,
                group_config,
                verbose,
            )
            if return_code != 0:
                print(f'Error code: {return_code}')
            else:
                total_instance_succeeded += 1
            print('********************************************************************************\n')

print(
    f'End of tests. Attempted {total_instance_attempted} instances, '
    f'solved successfully {total_instance_succeeded}.')
