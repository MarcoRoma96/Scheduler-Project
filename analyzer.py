from argparse import ArgumentParser
from pathlib import Path
import yaml
import json
import time
import pandas as pd

from src.common.custom_types import FinalResult
from src.common.file_load_and_dump import decode_master_instance, decode_master_result, decode_subproblem_result
from src.common.file_load_and_dump import decode_final_result, decode_cores, decode_subproblem_instance
from src.common.tools import is_combination_to_do
from src.analyzers.master_instance_analyzer import analyze_master_instance
from src.analyzers.master_result_analyzer import analyze_master_result
from src.analyzers.subproblem_instance_analyzer import analyze_subproblem_instance
from src.analyzers.subproblem_result_analyzer import analyze_subproblem_result
from src.analyzers.final_result_analyzer import analyze_final_result
from src.analyzers.cores_analyzer import analyze_cores
from src.analyzers.tools import analyze_log


# Questo script può essere chiamato solo direttamente dalla linea di comando
if __name__ != '__main__':
    exit(0)


def write_excel_sheet(df: pd.DataFrame, writer: pd.ExcelWriter, sheet_name: str):
    '''Funzione che crea una pagina Excel con i dati forniti dal DataFrame.'''

    df.to_excel(writer, sheet_name=sheet_name, index=False, na_rep='NaN')

    # Aggiustamento dell'ampiezza delle colonne
    for column_name, column in df.items():
        column_length = max(column.astype(str).map(len).max(), len(column_name)) # type: ignore
        col_idx = df.columns.get_loc(column_name)
        writer.sheets[sheet_name].set_column(col_idx, col_idx, column_length)

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
        print(f'Master instance not found in directory {result_directory.name}')
        continue
    with open(master_instance_path, 'r') as file:
        master_instance = decode_master_instance(json.load(file))
    
    # Analisi dell'istanza di input
    if config['do_instance_analysis']:

        instance_analysis: dict[str, str | int | float] = {
            'config': config_name,
            'group': group_name,
            'instance': instance_name
        }

        instance_analysis.update(analyze_master_instance(master_instance))
        instance_data.append(instance_analysis)

        # Eventuale lettura ed analisi del risultato migliore finora ottenuto
        best_final_result_path = result_directory.joinpath('best_final_result_so_far.json')
        if best_final_result_path.exists():
            with open(best_final_result_path, 'r') as file:
                best_final_result = decode_final_result(json.load(file))
            instance_analysis.update(analyze_final_result(master_instance, best_final_result))

    # Ciclo che analizza ogni iterazione
    for iteration_path in result_directory.iterdir():
        if not iteration_path.is_dir():
            continue
        if iteration_path.name in ['analysis', 'plots']:
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
