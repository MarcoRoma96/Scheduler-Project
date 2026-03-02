from argparse import ArgumentParser
import pyomo.environ as pyo
from pathlib import Path
import logging
import shutil
import json
import yaml
import time

# Soppressione dell'output a terminale degli avvertimenti di Pyomo
logging.getLogger('pyomo.core').setLevel(logging.ERROR)

from src.common.custom_types import MasterInstance, Cache, FatCore, SlimCore, DayName
from src.common.custom_types import FatSubproblemResult, SlimSubproblemResult, FinalResult
from src.common.custom_types import FatMasterResult, FatSubproblemInstance, SlimSubproblemInstance
from src.common.custom_types import CacheMatch, PatientServiceOperator, IterationName
from src.common.tools import get_subproblem_instance_from_master_result, compose_final_result
from src.common.tools import is_combination_to_do, get_slim_subproblem_instance_from_fat
from src.common.tools import get_all_possible_fat_master_requests, get_all_possible_slim_master_requests
from src.common.tools import remove_requests_not_present
from src.common.config_merge import merge_group_config
from src.common.solver_factory import get_solver_name, build_solver, has_usable_solution, describe_solver_result
from src.common.file_load_and_dump import decode_master_instance, encode_master_instance, encode_master_result
from src.common.file_load_and_dump import encode_subproblem_instance, encode_subproblem_result
from src.common.file_load_and_dump import encode_final_result, decode_subproblem_result, encode_cores, encode_cache_matching

from src.checkers.check_master_instance import check_master_instance
from src.checkers.check_master_result import check_fat_master_result, check_slim_master_result
from src.checkers.check_subproblem_instance import check_fat_subproblem_instance, check_slim_subproblem_instance
from src.checkers.check_subproblem_result import check_subproblem_result
from src.checkers.check_final_result import check_final_result
from src.checkers.check_cores import check_cores

from src.milp_models.master_model import get_fat_master_model, get_slim_master_model
from src.milp_models.master_model import get_result_from_fat_master_model, get_result_from_slim_master_model
from src.milp_models.master_model import add_core_constraints_to_fat_master_model, add_core_constraints_to_slim_master_model
from src.milp_models.subproblem_model import get_fat_subproblem_model, get_slim_subproblem_model
from src.milp_models.subproblem_model import get_result_from_fat_subproblem_model, get_result_from_slim_subproblem_model
from src.milp_models.cache_model import get_cache_model, get_result_from_cache_model

from src.cache.cache import add_final_result_to_cache, fix_cache_final_result
from src.cache.cache import get_previous_cache_day_iterations

from src.cores.generalist_cores import get_generalist_cores
from src.cores.basic_cores import get_basic_fat_cores, get_basic_slim_cores
from src.cores.reduced_cores import get_reduced_fat_cores, get_reduced_slim_cores
from src.cores.pruned_cores import get_pruned_fat_cores, get_pruned_slim_cores
from src.cores.core_expansion import expand_cores, get_subsumptions
from src.cores.tools import aggregate_core_lists

from src.analyzers.tools import get_result_value, get_day_number_used_by_patients


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


def exhume_result_from_matching(
        matching: CacheMatch,
        output_path: Path) -> FinalResult:
    '''Funzione che ritorna i risultati finali relativi al matching della cache,
    leggendoli da file.'''

    final_result = FinalResult()

    # Ciclo per ogni giorno
    for day_name, iteration_name in matching.items():
        
        # Lettura del sottoproblema specificato dal matching
        subproblem_result_path = output_path.joinpath(f'iter_{iteration_name}').joinpath(f'subproblem_day_{day_name}_result.json')
        with open(subproblem_result_path, 'r') as file:
            subproblem_result = decode_subproblem_result(json.load(file))
        
        final_result.scheduled[day_name] = subproblem_result.scheduled
    
    return final_result


def solve_instance(
        master_instance: MasterInstance,
        config,
        output_path: Path,
        iteration_summary_lines: list[str]) -> int:
    '''Funzione che esegue il ciclo di iterazioni necessario per risolvere una
    istanza del problema master con la configurazione fornita.'''
    
    cache: Cache = {}

    # Contatori per i valori dei migliori risultati finora incontrati
    best_final_result_value_so_far = None
    cache_final_result_value = None
    best_cache_result_value_so_far = None
    best_subproblem_result_value_so_far = None

    solver_name = get_solver_name(config)

    master_opt = build_solver(
        solver_name,
        config['master']['time_limit'],
        config['master']['memory_limit'])

    subproblem_opt = build_solver(
        solver_name,
        config['subproblem']['time_limit'],
        config['subproblem']['memory_limit'])

    cache_opt = build_solver(
        solver_name,
        config['cache']['time_limit'],
        config['cache']['memory_limit'])

    # Copia dell'istanza master nella cartella dei risultati
    with open(output_path.joinpath('master_instance.json'), 'w') as file:
        json.dump(encode_master_instance(master_instance), file, indent=4)
    
    # Copia della configurazione nella cartella dei risultati
    with open(output_path.joinpath('config.yaml'), 'w') as file:
        yaml.dump(config, file, indent=4, sort_keys=False)

    errors = check_master_instance(master_instance)
    if len(errors) > 0:
        for error in errors:
            print(f'[MASTER] ERROR: {error}')
        return 1

    # Valore che accumula i soli tempi di risoluzione dele varie fasi.
    # Necessario per lo stop relativo al tempo totale
    total_time_elapsed = 0

    # Ottenimento di tutte le possibili richieste ottenibili per ogni giorno.
    # Dati utilizzati nell'espansione dei core
    if config['structure_type'] in ['fat-slim', 'fat-fat']:
        all_possible_master_requests = get_all_possible_fat_master_requests(master_instance)
    else:
        all_possible_master_requests = get_all_possible_slim_master_requests(master_instance)
    
    # Numero totale massimo di giorni in cui i pazienti potrebbero avere
    # richieste. Utilizzato nel caso si voglia minimizzare i trip ospedalieri
    # ('minimize_hospital_accesses' nella configurazione del master)
    worst_case_day_number = get_day_number_used_by_patients(all_possible_master_requests)

    # Creazione del modello MILP del master
    print('[MASTER] Start master model creation...', end='')
    start = time.perf_counter()
    if config['structure_type'] in ['fat-slim', 'fat-fat']:
        master_model = get_fat_master_model(master_instance, config['master']['additional_info'])
    else:
        master_model = get_slim_master_model(master_instance, config['master']['additional_info'])
    end = time.perf_counter()
    print(f'done ({end - start:.04}s)')

    # Ottenimento delle relazioni di minore o uguale sui giorni, per espanderli
    if config['core_day_expansion']:
        print('[CORE] Start subsumption computation...', end='')
        subsumptions = get_subsumptions(master_instance, config)
        print('ended')
    else:
        subsumptions = None

    ############################ INIZIO ITERAZIONI #############################

    iteration_index = 0
    while iteration_index < config['max_iteration']:
        iteration_index += 1
        
        # Creazione della cartella con i risultati di questa iterazione
        iteration_path = output_path.joinpath(f'iter_{iteration_index}')
        if iteration_path.exists():
            shutil.rmtree(iteration_path)
        iteration_path.mkdir()

        print(f'\n*************************** [START OF ITERATION {iteration_index:03}] ***************************')
        if len(iteration_summary_lines) > 0:
            for line in iteration_summary_lines:
                print(line)
            print(f'********************************************************************************')

        # Risoluzione del problema master
        print(f'[iter {iteration_index}] [MASTER] Starting master solving...', end='')
        start = time.perf_counter()
        solve_kwargs = {
            'logfile': iteration_path.joinpath('master_log.log'),
            'tee': True}
        if solver_name == 'gurobi':
            solve_kwargs['warmstart'] = True
        master_solve_result = master_opt.solve(master_model, **solve_kwargs)
        end = time.perf_counter()
        total_time_elapsed += end - start
        print(f'done ({end - start:.04}s)', end='')
        if end - start >= config['master']['time_limit']:
            print(' [TIME LIMIT]')
        else:
            print('')
        if not has_usable_solution(master_solve_result):
            print(f'[iter {iteration_index}] [MASTER] ERROR: solver returned no usable solution ({describe_solver_result(master_solve_result)})')
            return 7

        if config['structure_type'] in ['fat-slim', 'fat-fat']:
            master_result = get_result_from_fat_master_model(master_model)
        else:
            master_result = get_result_from_slim_master_model(master_model)

        # Salvataggio dei risultati del master
        with open(iteration_path.joinpath('master_result.json'), 'w') as file:
            json.dump(encode_master_result(master_result), file, indent=4)

        if isinstance(master_result, FatMasterResult):
            errors = check_fat_master_result(master_instance, master_result)
        else:
            errors = check_slim_master_result(master_instance, master_result)
        if len(errors) > 0:
            for error in errors:
                print(f'[iter {iteration_index}] [MASTER] ERROR: {error}')
            return 2
        
        master_result_value = get_result_value(
            master_instance, master_result,
            config['master']['additional_info'], worst_case_day_number)
        print(f'[iter {iteration_index}] [MASTER] Optimistic master result value: {master_result_value}')
        
        ############################# INIZIO CACHE #############################

        if config['use_cache_selection_model'] and iteration_index > 2:

            # Creazione del modello MILP della cache
            print(f'[iter {iteration_index}] [CACHE] Start cache model creation...', end='')
            start = time.perf_counter()
            cache_model = get_cache_model(master_instance, cache, best_cache_result_value_so_far)
            end = time.perf_counter()
            print(f'done ({end - start:.04}s) ', end='')

            # Risoluzione del modello MILP della cache
            print(f'Start solving...', end='')
            start = time.perf_counter()
            cache_solve_result = cache_opt.solve(
                cache_model,
                logfile=iteration_path.joinpath('cache_log.log'),
                tee=True)
            end = time.perf_counter()
            total_time_elapsed += end - start
            print(f'done ({end - start:.04}s)', end='')
            if end - start >= config['cache']['time_limit']:
                print(' [TIME LIMIT]')
            else:
                print('')
            if not has_usable_solution(cache_solve_result):
                print(f'[iter {iteration_index}] [CACHE] ERROR: solver returned no usable solution ({describe_solver_result(cache_solve_result)})')
                return 3

            matching = get_result_from_cache_model(cache_model)
            cache_final_result = exhume_result_from_matching(matching, output_path)
            fix_cache_final_result(master_instance, cache_final_result)
            
            # Salvataggio del matching della cache
            with open(iteration_path.joinpath(f'cache_matching.json'), 'w') as file:
                json.dump(encode_cache_matching(matching), file, indent=4)
            
            # Salvataggio dei risultati finali della cache
            with open(iteration_path.joinpath(f'cache_final_result.json'), 'w') as file:
                json.dump(encode_final_result(cache_final_result), file, indent=4)

            errors = check_final_result(master_instance, cache_final_result)
            if len(errors) > 0:
                for error in errors:
                    print(f'[iter {iteration_index}] [CACHE] ERROR: {error}')
                return 3
            
            # Eventuali salvataggi dei valori migliori finora incontrati
            cache_final_result_value = get_result_value(
                master_instance, cache_final_result,
                config['master']['additional_info'], worst_case_day_number)
            print(f'[iter {iteration_index}] [CACHE] Cache objective function value: {pyo.value(cache_model.objective_function)}, true value: {cache_final_result_value}')
            
            if best_cache_result_value_so_far is None or cache_final_result_value > best_cache_result_value_so_far:
                best_cache_result_value_so_far = cache_final_result_value
                print(f'[iter {iteration_index}] [CACHE] Found new cache best solution of value {best_cache_result_value_so_far}')

            if best_final_result_value_so_far is None or cache_final_result_value > best_final_result_value_so_far:
                best_final_result_value_so_far = cache_final_result_value
                
                print(f'[iter {iteration_index}] Found new best solution of value {best_final_result_value_so_far}')
                with open(output_path.joinpath(f'best_final_result_so_far.json'), 'w') as file:
                    json.dump(encode_final_result(cache_final_result), file, indent=4)
            
            # Se il risultato della cache è uguale a quello del master abbiamo
            # l'ottimo
            if cache_final_result_value >= master_result_value:
                print(f'[iter {iteration_index}] [STOP] Reached optimum of value: {cache_final_result_value}')
                print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')
                break
        
        if config['use_true_cache'] and iteration_index > 1:
            previous_cache_day_iterations = get_previous_cache_day_iterations(cache, master_result)
            if len(previous_cache_day_iterations) > 0:
                print(f'[iter {iteration_index}] [CACHE] Found {len(previous_cache_day_iterations)} days already solved in cache')
                with open(iteration_path.joinpath(f'true_cache_finds.json'), 'w') as file:
                    json.dump(previous_cache_day_iterations, file, indent=4)
            else: 
                print(f'[iter {iteration_index}] [CACHE] No already solved days found in cache')

        ############################## FINE CACHE ##############################
        
        ######################### INIZIO SOTTOPROBLEMA #########################

        all_subproblem_instances: dict[DayName, FatSubproblemInstance] | dict[DayName, SlimSubproblemInstance] = {}
        all_subproblem_result:  dict[DayName, SlimSubproblemResult] | dict[DayName, FatSubproblemResult] = {}
        
        for day_name in master_result.scheduled.keys():
            
            # Ottenimento dell'istanza del sottoproblema del giorno corrente
            subproblem_instance = get_subproblem_instance_from_master_result(master_instance, master_result, day_name)
            all_subproblem_instances[day_name] = subproblem_instance # type: ignore

            # Salvataggio del sottoproblema del giorno corrente
            with open(iteration_path.joinpath(f'subproblem_day_{day_name}_instance.json'), 'w') as file:
                json.dump(encode_subproblem_instance(subproblem_instance), file, indent=4)

            if isinstance(subproblem_instance, FatSubproblemInstance):
                errors = check_fat_subproblem_instance(subproblem_instance)
            else:
                errors = check_slim_subproblem_instance(subproblem_instance)
            if len(errors) > 0:
                for error in errors:
                    print(f'[iter {iteration_index}] [SUB] ERROR: {error}')
                return 4

            # Copia del risultato del giorno corrente se trovato nella cache
            if config['use_true_cache'] and iteration_index > 1 and day_name in previous_cache_day_iterations: # type: ignore

                iteration_name: IterationName = previous_cache_day_iterations[day_name] # type: ignore

                print(f'[iter {iteration_index}] [CACHE] Found day {day_name} already in cache (iter {iteration_name})')
                
                previous_iteration_path = output_path.joinpath(f'iter_{iteration_name}') # type: ignore
                with open(previous_iteration_path.joinpath(f'subproblem_day_{day_name}_result.json'), 'r') as file:
                    subproblem_result = decode_subproblem_result(json.load(file))
                
                remove_requests_not_present(subproblem_result, master_result, day_name)
            
            # Se il risultato non è già presente nella cache in una qualche
            # iterazione precedente, risolvi il sottoproblema normalmente
            else:
                # Creazione del modello MILP del giorno corrente
                print(f'[iter {iteration_index}] [SUB] Start day {day_name} model creation...', end='')
                start = time.perf_counter()

                # Se la struttura risolutiva è 'fat-fat' ed è selezionata l'opzione
                # 'preemptive_forbidding'allora bisogna costruire l'istanza del
                # sottoproblema dimenticandosi dei nomi degli operatori
                if config['structure_type'] == 'fat-fat' and 'preemptive_forbidding' in config['subproblem']['additional_info']:
                    
                    forgetful_subproblem_instance = get_slim_subproblem_instance_from_fat(subproblem_instance) # type: ignore
                    subproblem_model = get_fat_subproblem_model(forgetful_subproblem_instance, config['subproblem']['additional_info'], master_result.scheduled[day_name]) # type: ignore
                
                elif config['structure_type'] in ['slim-fat', 'fat-fat']:
                    subproblem_model = get_fat_subproblem_model(subproblem_instance, config['subproblem']['additional_info']) # type: ignore
                else:
                    subproblem_model = get_slim_subproblem_model(subproblem_instance) # type: ignore
                
                end = time.perf_counter()
                print(f'done ({end - start:.04}s) ', end='')

                # Risoluzione del modello MILP del giorno corrente
                print('Start solving...', end='')
                start = time.perf_counter()
                subproblem_solve_result = subproblem_opt.solve(
                    subproblem_model,
                    logfile=iteration_path.joinpath(f'subproblem_day_{day_name}_log.log'),
                    tee=True)
                end = time.perf_counter()
                total_time_elapsed += end - start
                print(f'done ({end - start:.04}s)', end='')
                if end - start >= config['subproblem']['time_limit']:
                    print(' [TIME LIMIT]')
                else:
                    print('')
                if not has_usable_solution(subproblem_solve_result):
                    print(
                        f'[iter {iteration_index}] [SUB] ERROR: day {day_name} has no usable solution '
                        f'({describe_solver_result(subproblem_solve_result)})')
                    return 5

                if config['structure_type'] in ['slim-fat', 'fat-fat']:
                    subproblem_result = get_result_from_fat_subproblem_model(subproblem_model)
                else:
                    subproblem_result = get_result_from_slim_subproblem_model(subproblem_model)

            # Salvataggio dei risultati del giorno corrente
            with open(iteration_path.joinpath(f'subproblem_day_{day_name}_result.json'), 'w') as file:
                json.dump(encode_subproblem_result(subproblem_result), file, indent=4)

            errors = check_subproblem_result(subproblem_instance, subproblem_result)
            if len(errors) > 0:
                for error in errors:
                    print(f'[iter {iteration_index}] [SUB] ERROR: {error}')
                return 5
            
            all_subproblem_result[day_name] = subproblem_result # type: ignore
        
        ########################## FINE SOTTOPROBLEMA ##########################
        
        #################### COMPOSIZIONE RISULTATI FINALI #####################

        print(f'[iter {iteration_index}] Subproblem finished. Composing final result')
        final_result = compose_final_result(master_instance, master_result, all_subproblem_result)

        # Salvataggio su file dei risultati finali
        with open(iteration_path.joinpath(f'final_result.json'), 'w') as file:
            json.dump(encode_final_result(final_result), file, indent=4)

        errors = check_final_result(master_instance, final_result)
        if len(errors) > 0:
            for error in errors:
                print(f'[iter {iteration_index}] ERROR: {error}')
            return 6
        
        # Aggiunta dei core 'preemptive' nel caso 'fat-fat'. Questi core vietano
        # il ripetersi di proposte del master che sono state comunque
        # soddisfatte dal sottoproblema nella loro interezza, ma da una
        # combinazione diversa di operatori. Questi core sono ridondanti in
        # quanto non agiscono sulla soddisfacibilità (quindi non sarebbero
        # 'corretti'), ma riducono le simmetrie
        if config['structure_type'] == 'fat-fat' and 'preemptive_forbidding' in config['subproblem']['additional_info']:
            print(f'[iter {iteration_index}] [CORE] Start of preemptive cores search')

            preemptive_cores: list[FatCore] = []

            # Iterazione dei giorni
            for day_name, subproblem_result in all_subproblem_result.items():

                scheduled_requests = subproblem_result.scheduled
                master_scheduled_requests: list[PatientServiceOperator] = master_result.scheduled[day_name] # type: ignore
            
                # Il giorno deve essere risolto all'ottimo
                if len(subproblem_result.rejected) > 0:
                    continue
                if len(scheduled_requests) != len(master_scheduled_requests):
                    continue

                # Controllo sull'uguaglianza perfetta delle soluzioni
                are_equal_solutions = True
                for request in master_scheduled_requests:
                    
                    is_request_present = False
                    for other_request in scheduled_requests:
                        if request.patient_name == other_request.patient_name and request.service_name == other_request.service_name and request.operator_name == other_request.operator_name:
                            is_request_present = True
                            break
            
                    if not is_request_present:
                        are_equal_solutions = False
                        break
            
                # Aggiungi un core se le soluzioni differiscono per
                # l'assegnamento degli operatori
                if not are_equal_solutions:
                    print(f'[iter {iteration_index}] [CORE] Day {day_name} has no rejected request but a different solution')
                    preemptive_cores.append(FatCore(
                        day=day_name,
                        reason=[master_scheduled_requests[0]],
                        components=master_scheduled_requests))
            
            if len(preemptive_cores) == 0:
                print(f'[iter {iteration_index}] [CORE] No preemptive core found')
            else:

                # Salvataggio su file dei core preemptive
                with open(iteration_path.joinpath(f'preemptive_cores.json'), 'w') as file:
                        json.dump(encode_cores(preemptive_cores), file, indent=4) # type: ignore

                add_core_constraints_to_fat_master_model(master_model, preemptive_cores) # type: ignore
                print(f'[iter {iteration_index}] [CORE] Added {len(preemptive_cores)} preemptive cores')

        final_result_value = get_result_value(
            master_instance, final_result,
            config['master']['additional_info'], worst_case_day_number)
        print(f'[iter {iteration_index}] Combined subproblem result value: {final_result_value}')

        # Eventuale salvataggio dei valori migliori finora incontrati
        if best_subproblem_result_value_so_far is None or final_result_value > best_subproblem_result_value_so_far:
            best_subproblem_result_value_so_far = final_result_value

        if best_final_result_value_so_far is None or final_result_value > best_final_result_value_so_far:
            best_final_result_value_so_far = final_result_value

            print(f'[iter {iteration_index}] Found new best solution of value {best_final_result_value_so_far}')
            with open(output_path.joinpath(f'best_final_result_so_far.json'), 'w') as file:
                json.dump(encode_final_result(final_result), file, indent=4)

        # Se il risultato ottimistico del master è uguale a quello reale abbiamo l'ottimo
        if final_result_value >= master_result_value:
            print(f'[iter {iteration_index}] [STOP] Reached optimum of value: {final_result_value}')
            print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')
            break

        day_names_with_rejected: list[DayName] = []
        for day_name, result in all_subproblem_result.items():
            if len(result.rejected) > 0:
                day_names_with_rejected.append(day_name)
        
        # Se ogni giorno è completamente risolto abbiamo l'ottimo
        if len(day_names_with_rejected) == 0:
            print(f'[iter {iteration_index}] [STOP] All days are satisfied! (value: {final_result_value})')
            print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')
            break

        print(f'[iter {iteration_index}] Days [ ', end='')
        for day_name in day_names_with_rejected:
            print(f'{day_name} ', end='')
        print('] are not completely satisfied')

        ############################# INIZIO CORE ##############################
        
        print(f'[iter {iteration_index}] [CORE] Starting core creation')

        cores: list[FatCore] | list[SlimCore] = []

        # Riallinea le richieste che ha assegnato il sottoproblema fat con
        # quelle che il master aveva proposto. I core devono ovviamente avere a
        # che fare con le richieste reali del master
        if config['structure_type'] == 'fat-fat':

            # Ogni risultato del sottoproblema, se ha qualcosa di non assegnato,
            # viene corretto ripristinando la richiesta originaria del master
            for day_name, subproblem_result in all_subproblem_result.items():
                if len(subproblem_result.rejected) == 0:
                    continue

                daily_master_result: list[PatientServiceOperator] = master_result.scheduled[day_name] # type: ignore

                # Ogni richiesta copia il suo operatore dal master
                for subproblem_request in subproblem_result.scheduled:
                    for master_request in daily_master_result:
                        if (master_request.patient_name == subproblem_request.patient_name
                            and master_request.service_name == subproblem_request.service_name):
                            subproblem_request.operator_name = master_request.operator_name
                            break

        # Ottenimento dei core
        if config['core_type'] == 'generalist':
            start = time.perf_counter()
            cores = get_generalist_cores(all_subproblem_result)
            end = time.perf_counter()
            total_time_elapsed += end - start
            print(f'[iter {iteration_index}] [CORE] {len(cores)} \'generalist\' cores found ({end - start:.04}s)')

            with open(iteration_path.joinpath(f'generalist_cores.json'), 'w') as file:
                json.dump(encode_cores(cores), file, indent=4)
            
            errors = check_cores(master_instance, cores)
            if len(errors) > 0:
                for error in errors:
                    print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                return 7
        else:
            # Master fat
            if config['structure_type'] in ['fat-slim', 'fat-fat']:
                
                if config['core_type'] in ['basic', 'reduced', 'pruned']:
                    start = time.perf_counter()
                    cores = get_basic_fat_cores(all_subproblem_result)
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'basic\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'basic_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)
                    
                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 8
                
                if config['core_type'] in ['reduced', 'pruned']:
                    start = time.perf_counter()
                    cores = get_reduced_fat_cores(cores) # type: ignore
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'reduced\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'reduced_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)
                    
                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 9
                
                if config['core_type'] in ['pruned']:
                    start = time.perf_counter()
                    cores = get_pruned_fat_cores(all_subproblem_instances, cores, config) # type: ignore
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'pruned\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'pruned_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)
                    
                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 10
            
            # Master slim
            else:
                
                if config['core_type'] in ['basic', 'reduced', 'pruned']:
                    start = time.perf_counter()
                    cores = get_basic_slim_cores(all_subproblem_result) # type: ignore
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'basic\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'basic_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)

                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 11
                
                if config['core_type'] in ['reduced', 'pruned']:
                    start = time.perf_counter()
                    cores = get_reduced_slim_cores(master_instance.services, cores) # type: ignore
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'reduced\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'reduced_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)
                    
                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 12
                
                if config['core_type'] in ['pruned']:
                    start = time.perf_counter()
                    cores = get_pruned_slim_cores(all_subproblem_result, all_subproblem_instances, cores, config) # type: ignore
                    end = time.perf_counter()
                    total_time_elapsed += end - start
                    print(f'[iter {iteration_index}] [CORE] {len(cores)} \'pruned\' cores found ({end - start:.04}s)')

                    with open(iteration_path.joinpath(f'pruned_cores.json'), 'w') as file:
                        json.dump(encode_cores(cores), file, indent=4)
                    
                    errors = check_cores(master_instance, cores)
                    if len(errors) > 0:
                        for error in errors:
                            print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                        return 13
        
        print(f'[iter {iteration_index}] [CORE] Cores creation done')

        # Espansione dei core
        if config['core_patient_expansion'] or config['core_service_expansion'] or config['core_operator_expansion'] or config['core_day_expansion']:
            print(f'[iter {iteration_index}] [CORE] Starting core expansion')
            expanded_cores = expand_cores(cores, all_possible_master_requests, master_instance.services, config, subsumptions)
            print(f'[iter {iteration_index}] [CORE] End of core expansion. Found {len(expanded_cores)} cores from starting with {len(cores)} cores')

            # Se per qualche motivo l'espansione non ha prodotto il caso
            # denegenere a->a, aggiungilo e rimuovi eventuali duplicati
            cores = aggregate_core_lists(cores, expanded_cores)
            print(f'[iter {iteration_index}] [CORE] {len(cores)} cores remaining after aggregate and duplicate removal')

            with open(iteration_path.joinpath(f'expanded_cores.json'), 'w') as file:
                json.dump(encode_cores(cores), file, indent=4)
            
            errors = check_cores(master_instance, cores)
            if len(errors) > 0:
                for error in errors:
                    print(f'[iter {iteration_index}] [CORE] ERROR: {error}')
                return 14

        # Aggiunta dei vincoli dei core nel master
        if config['structure_type'] in ['fat-slim', 'fat-fat']:
            add_core_constraints_to_fat_master_model(master_model, cores) # type: ignore
        else:
            add_core_constraints_to_slim_master_model(master_model, cores) # type: ignore

        ############################## FINE CORE ###############################

        # Aggiunta dei risultati finali nella cache
        if config['use_true_cache'] or config['use_cache_selection_model']:
            print(f'[iter {iteration_index}] [CACHE] Adding final result to cache')
            add_final_result_to_cache(cache, master_instance, final_result, iteration_index)

        # Stampa delle informazioni dell'iterazione corrente appena terminata
        print(f'[iter {iteration_index}] Elapsed {int(total_time_elapsed)}/{config["total_time_limit"]}s in total')
        print(f'[iter {iteration_index}] Master value: {master_result_value}, current subproblem value: {final_result_value}')
        print(f'[iter {iteration_index}] Best solution value so far: {best_final_result_value_so_far}, best subproblem value so far: {best_subproblem_result_value_so_far}')
        if (config['use_true_cache'] or config['use_cache_selection_model']) and iteration_index > 2 and best_cache_result_value_so_far is not None:
            print(f'[iter {iteration_index}] [CACHE] Current cache solution: {cache_final_result_value}, best cache so far: {best_cache_result_value_so_far} ({best_cache_result_value_so_far - best_subproblem_result_value_so_far} more than the best subproblem)')
        print(f'[iter {iteration_index}] [CORE] Added {len(cores)} \'{config["core_type"]}\' cores in this iteration.')
        
        # Controllo sul raggiungimento dell'approssimazione dell'ottimo
        if config['early_stop_optimum_approximation_percentage'] != 1.0:
            if master_result_value * config['percentage_of_optimum_approach'] >= final_result_value:
                print(f'[iter {iteration_index}] [STOP] Reached the optimum approximation (final\'s {final_result_value} vs master\'s {master_result_value})')
                print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')
                break
        
        # Controllo sul raggiungimento del limite temporale totale
        if total_time_elapsed >= config['total_time_limit']:
            print(f'[iter {iteration_index}] [STOP] Reached maximum time limit ({int(total_time_elapsed)}s elapsed)')
            print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')
            break

        # Controllo sul raggiungimento del numero massimo di iterazioni
        if iteration_index == config['max_iteration']:
            print(f'[iter {iteration_index}] [STOP] Maximum iteration reached')
        
        print(f'**************************** [END OF ITERATION {iteration_index:03}] ****************************')

        ########################### FINE ITERAZIONI ############################

    print('')

    return 0

# Definizione dei parametri a linea di comando
parser = ArgumentParser(prog='Iterative instance solver')
parser.add_argument('-c', '--config', help='Location of the solving configuration', type=Path, required=True)
parser.add_argument('-i', '--input', help='Location of master instance groups', type=Path, required=True)
parser.add_argument('-o', '--output', help='Where the output will be written', type=Path, required=True)
parser.add_argument('--overwrite', help='If output can overwrite previous files', action='store_true')
args = parser.parse_args()

config_path = Path(args.config).resolve()
input_path = Path(args.input).resolve()
output_path = Path(args.output).resolve()
can_overwrite = bool(args.overwrite)

output_path.mkdir(exist_ok=True)

# Lettura della configurazione
with open(config_path, 'r') as file:
    config = yaml.load(file, yaml.CLoader)

infos = get_preliminary_solving_info(config, input_path, can_overwrite)
total_instance_solved = 0
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

            # Lettura dell'istanza master di input
            with open(input_instance_path, 'r') as file:
                master_instance = decode_master_instance(json.load(file))
            
            # Aggiornamento dei contatori
            instance_solved_of_this_group += 1
            instance_solved_of_this_config += 1
            total_instance_solved += 1

            iteration_summary_lines = [
                f'Solving instance \'{instance_name}\' of group \'{group_name}\' with config \'{config_name}\'',
                f'{instance_solved_of_this_group}/{infos[config_name, group_name]} instance of this group, {instance_solved_of_this_config}/{sum(n for cg, n in infos.items() if cg[0] == config_name)} instance of this config',
                f'{total_instance_solved}/{total_instances_to_solve} instance solving in total'
            ]

            # Risoluzione dell'istanza corrente
            error_code = solve_instance(master_instance, group_config, solving_path, iteration_summary_lines)
            if error_code != 0:
                print(f'Error code: {error_code}')

print(f'End of tests. Solved {total_instance_solved} instances.')
