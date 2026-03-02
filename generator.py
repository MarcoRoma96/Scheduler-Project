from argparse import ArgumentParser
from pathlib import Path
import random
import json
import yaml

from src.common.config_merge import merge_group_config
from src.generators.master_generator import generate_master_instance
from src.generators.subproblem_generator import generate_subproblem_instance


# Questo script può essere chiamato solo direttamente dalla linea di comando
if __name__ != '__main__':
    exit(0)

# Definizione dei parametri a linea di comando
parser = ArgumentParser(prog='Instance generator')
parser.add_argument('-c', '--config', help='Location of the generator configuration', type=Path, required=True)
parser.add_argument('-o', '--output', help='Where the output will be written', type=Path, required=True)
parser.add_argument('--overwrite', help='If output can overwrite previous files', action='store_true')
args = parser.parse_args()

config_path = Path(args.config).resolve()
output_path = Path(args.output).resolve()
can_overwrite = bool(args.overwrite)

# Eventuale creazione della cartella di output
output_path.mkdir(exist_ok=True)

# Lettura della configurazione
with open(config_path, 'r') as file:
    config = yaml.load(file, yaml.CLoader)

base_config = config['base']

total_instance_generated = 0

# Ogni gruppo genera le prorpie istanze
for group_name, config_diff_from_base in config['groups'].items():

    # Creazione della configurazione del gruppo corrente, sovrascrivendo alcuni
    # parametri
    group_config = merge_group_config(base_config, config_diff_from_base)
    
    # Eventuale creazione della cartella di output del gruppo
    group_path = output_path.joinpath(group_name)
    if not can_overwrite and group_path.exists():
        print(f'Directory {group_path} already exists, skipping it.')
        continue
    group_path.mkdir(exist_ok=True)
    
    # Inizializzazione del seme di generazione pseudocasuale delle istanze del
    # gruppo corrente
    random.seed(group_config['seed'])

    group_instance_number = 0

    # Generazione di ogni istanza
    for instance_index in range(group_config['instance_number']):
        
        # Se l'opzione 'day_number' è presente allora si tratta di un'istanza
        # del problema master, altrimenti è un sottoproblema
        if 'day_number' in group_config:
            instance = generate_master_instance(group_config)
        else:
            instance = generate_subproblem_instance(group_config)

        # Salvataggio dell'istanza
        instance_path = group_path.joinpath(f'inst_{instance_index:02}.json')
        with open(instance_path, 'w') as file:
            json.dump(instance, file, indent=4)
        
        group_instance_number += 1
    
    print(f'Generated {group_instance_number} instances in group \'{group_name}\'')
    total_instance_generated += group_instance_number

print(f'End of generation. Generated {total_instance_generated} instances in total.')
