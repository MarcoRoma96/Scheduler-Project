# Scheduler Project

Framework per la generazione di istanze e la risoluzione MILP (Pyomo + Gurobi) di un problema di scheduling sanitario multi-giorno, con pipeline completa:

1. generazione istanze (`generator.py`)
2. solving (`solver.py` iterativo con core/caching, oppure `single_pass_solver.py`)
3. analisi dei risultati (`analyzer.py`)
4. plotting (`plotter.py`)

Il README è scritto in stile tutorial e reference tecnica.

## 1) Prerequisiti

- Python `>= 3.12` (il codice usa `type X = ...` in `custom_types.py`)
- Gurobi installato e licenza attiva
- Pacchetti Python:
  - `pyomo`
  - `gurobipy`
  - `pyyaml`
  - `pandas`
  - `matplotlib`
  - `xlsxwriter`

Esempio setup veloce:

```bash
python -m venv .venv
source .venv/bin/activate
pip install pyomo gurobipy pyyaml pandas matplotlib xlsxwriter
```

### Usare GLPK al posto di Gurobi

Lo switch è configurabile via parametro YAML, senza modifiche al codice:

1. installa GLPK (`glpsol`) e verifica che sia disponibile nel `PATH`
2. imposta `solver_name: 'glpk'` nel blocco `base` della configurazione:
   - `configs/iterative_solver_config.yaml`
   - `configs/single_pass_solver_config.yaml`
3. lascia invariati `time_limit` e `memory_limit`: il codice mappa automaticamente le opzioni:
   - Gurobi: `TimeLimit`, `SoftMemLimit`
   - GLPK: `tmlim`, `memlim` (conversione GB -> MB)

Esempio (`iterative_solver_config.yaml`):

```yaml
base:
  solver_name: 'glpk'
```

Nota: i parser log in `analyzer.py` (`src/analyzers/tools.py`) sono scritti per il formato log di Gurobi; con GLPK alcune metriche di log possono non essere popolate.

## 2) Quickstart end-to-end

### 2.1 Genera istanze master

```bash
python generator.py -c configs/master_generator_config.yaml -o instances --overwrite
```

### 2.2 Risolvi in modalità iterativa (master + sottoproblemi + core)

```bash
python solver.py -c configs/iterative_solver_config.yaml -i instances -o results --overwrite
```

### 2.3 Analizza i risultati in Excel

```bash
python analyzer.py -c configs/analyzer_config.yaml -i results
```

### 2.4 Genera grafici

```bash
python plotter.py all -c configs/plotter_config.yaml -i results
```

### 2.5 Plot di una singola istanza (debug visivo)

```bash
python plotter.py instance -i results/<config>__<group>__<inst> -o debug_plots --iter 1
```

### 2.6 Avvio GUI control panel

E' disponibile una GUI desktop per gestire configurazioni, lanciare script e navigare i risultati:

```bash
python control_panel.py
```

Funzionalita principali:

- pagina `Generator`: preset master/subproblem, editor config, run con output live
- pagina `Solving`: pannelli separati per `solver.py` e `single_pass_solver.py`
- pagina `Analysis / Plot`:
  - run `analyzer.py`
  - run `plotter.py` (`all` e `instance`)
  - browser risultati con filtri `config/group/instance` e apertura file (`xlsx/png/json/log`)
- terminale integrato con status bar e stop del processo

## 3) Struttura del progetto

```text
.
├── generator.py
├── control_panel.py
├── solver.py
├── single_pass_solver.py
├── analyzer.py
├── plotter.py
├── configs/
└── src/
    ├── generators/
    ├── milp_models/
    ├── cores/
    ├── cache/
    ├── analyzers/
    ├── plotters/
    ├── checkers/
    └── common/
```

## 4) Cosa succede dietro le quinte (flusso tutorial)

### 4.1 Generazione (`generator.py`)

- Legge un file YAML con:
  - `base`: template comune
  - `groups`: override per gruppo
- Per ogni gruppo:
  - inizializza il seed
  - genera `instance_number` JSON (`inst_00.json`, `inst_01.json`, ...)
- Se in config è presente `day_number`, genera istanze **master**; altrimenti **subproblem**.

### Master generator (`src/generators/master_generator.py`)

- crea giorni, unità di cura, operatori
- crea servizi (durata triangolare)
- crea pazienti
- riempie progressivamente finestre di richiesta fino al target di saturazione (`request_over_disponibility_ratio`)
- opzionalmente copia finestre tra servizi dello stesso paziente (`same_window_percentage`)

### Subproblem generator (`src/generators/subproblem_generator.py`)

- genera una giornata singola (`day`)
- tipo `slim`: richieste senza operatore assegnato
- tipo `fat`: richieste con operatore assegnato
- la generazione punta a saturare la capacità disponibile

### 4.2 Solver iterativo (`solver.py`)

Per ogni istanza master:

1. valida input (`check_*`)
2. crea una volta sola il modello master (fat o slim, in base a `structure_type`)
3. loop di iterazioni:
   - solve master (warmstart attivo)
   - opzionale solve modello cache (`use_cache_selection_model`)
   - opzionale true cache lookup per giorni già visti (`use_true_cache`)
   - genera/risolve sottoproblemi giornalieri
   - compone `final_result`
   - valuta objective reale
   - genera core (`generalist/basic/reduced/pruned`)
   - opzionale espansione core (paziente/servizio/operatore/giorno)
   - aggiunge vincoli core al master per iterazione successiva
   - aggiorna cache
   - controlla stop condition

### Stop conditions principali

- `final_result_value >= master_result_value` (ottimo raggiunto)
- nessun giorno con richieste rifiutate
- tempo totale superato (`total_time_limit`)
- iterazioni massime (`max_iteration`)

### 4.3 Single-pass solver (`single_pass_solver.py`)

Risoluzione one-shot (senza loop iterativo), utile per baseline:

- `problem_type`:
  - `monolithic`
  - `fat-master`
  - `slim-master`
  - `fat-subproblem`
  - `slim-subproblem`

### 4.4 Analyzer (`analyzer.py`)

- percorre tutte le cartelle risultato `<config>__<group>__<instance>`
- estrae KPI da:
  - istanza master
  - risultati master/final/cache
  - risultati sottoproblemi
  - file core
  - log Gurobi
- salva Excel in `results/analysis/`

### 4.5 Plotter (`plotter.py`)

- modalità `all`: grafici batch usando risultati + Excel analysis
- modalità `instance`: plot dettagliato di una singola istanza/iterazione
- i plot aggregati (`result_value_vs_time`, `core_info`, `solving_times`, `solving_times_by_day`, `requests_per_patient`, `aggregate_best_solution_value`) richiedono prima l’esecuzione di `analyzer.py`

## 5) Parametri configurabili (reference completa)

### 5.1 Filtri comuni (`*_to_do`, `*_to_avoid`)

Usati in solver, single-pass, analyzer, plotter.

| Campo | Tipo | Significato |
|---|---|---|
| `configs_to_do` | `list[str]` | Configurazioni incluse (`['all']` per tutto) |
| `configs_to_avoid` | `list[str]` | Configurazioni escluse |
| `groups_to_do` | `list[str]` | Gruppi istanze inclusi |
| `groups_to_avoid` | `list[str]` | Gruppi istanze esclusi |
| `instances_to_do` | `list[str]` | Istanze incluse (`inst_00`, ...) |
| `instances_to_avoid` | `list[str]` | Istanze escluse |

### 5.2 Config generazione master (`configs/master_generator_config.yaml`)

Struttura:

```yaml
groups:
  nome_gruppo:
    # override parziale dei campi di base
base:
  # parametri completi
```

Parametri `base`:

| Campo | Tipo | Significato |
|---|---|---|
| `seed` | `int` | Seed random per il gruppo |
| `instance_number` | `int` | Numero istanze del gruppo |
| `day_number` | `int` | Numero giorni nel master |
| `care_unit_number` | `int` | Numero unità di cura per giorno |
| `operator_number` | `int` | Operatori per unità di cura |
| `operator_duration` | `int` | Durata turno operatore (slot) |
| `service_number` | `int` | Numero servizi totali |
| `service_duration.min` | `int` | Min triangolare durata servizio |
| `service_duration.max` | `int` | Max triangolare durata servizio |
| `service_duration.mode` | `int` | Moda triangolare durata servizio |
| `patient_number` | `int` | Numero pazienti |
| `request_over_disponibility_ratio` | `float` | Saturazione target rispetto capacità totale |
| `window_max_size` | `int` | Ampiezza massima finestra richiesta (giorni) |
| `same_window_percentage` | `float` in `[0,1]` | Probabilità di copiare finestre tra servizi dello stesso paziente |

### 5.3 Config generazione subproblem (`configs/subproblem_generator_config.yaml`)

Parametri `base`:

| Campo | Tipo | Significato |
|---|---|---|
| `seed` | `int` | Seed random per gruppo |
| `instance_number` | `int` | Numero istanze |
| `care_unit_number` | `int` | Numero unità di cura |
| `operator_number` | `int` | Operatori per unità |
| `operator_duration` | `int` | Durata turno operatore |
| `service_duration.min` | `int` | Min triangolare |
| `service_duration.max` | `int` | Max triangolare |
| `service_duration.mode` | `int` | Moda triangolare |
| `patient_number` | `int` | Numero pazienti |
| `type` | `'fat' \| 'slim'` | Rappresentazione richieste (con/senza operatore) |

### 5.4 Config solver iterativo (`configs/iterative_solver_config.yaml`)

Parametri `base` (top-level):

| Campo | Tipo / Valori | Significato |
|---|---|---|
| `structure_type` | `'slim-fat' \| 'fat-slim' \| 'fat-fat'` | Primo termine: master, secondo: sottoproblema |
| `solver_name` | `'gurobi' \| 'glpk'` | Solver MILP usato in tutte le fasi della pipeline iterativa |
| `core_type` | `generalist \| basic \| reduced \| pruned` | Livello costruzione core |
| `post_pruning_irreducibility` | `bool` | Verifica irriducibilità dopo pruning |
| `core_patient_expansion` | `bool` | Abilita rinomina pazienti in espansione core |
| `core_service_expansion` | `bool` | Abilita rinomina servizi |
| `core_operator_expansion` | `bool` | Abilita rinomina operatori (utile in fat) |
| `core_day_expansion` | `bool` | Estende core ad altri giorni via sussunzione |
| `max_single_core_expansion` | `int` | Massimo numero matchings/core espansi per core sorgente |
| `use_true_cache` | `bool` | Riusa direttamente giorni già risolti |
| `use_cache_selection_model` | `bool` | MILP di selezione combinazione giorni dalla cache |
| `max_iteration` | `int` | Iterazioni massime |
| `total_time_limit` | `int` secondi | Tempo massimo globale di solving |
| `early_stop_optimum_approximation_percentage` | `float` | Parametro di early stop per approssimazione ottimo |

Sezioni annidate:

### `master`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve master per iterazione |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `additional_info` | `list[str]` | Flag opzionali |

Flag `master.additional_info`:

- `minimize_hospital_accesses`: penalizza uso di molti giorni per paziente in obiettivo
- `use_optimality_cuts`: aggiunge tagli di ottimalità (implementato per master slim)

### `subproblem`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve di ciascun sottoproblema giornaliero |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `additional_info` | `list[str]` | Flag opzionali |

Flag `subproblem.additional_info`:

- `use_redundant_operator_cut`: vincolo ridondante sulla capacità operatore (modelli fat)
- `preemptive_forbidding`: solo `fat-fat`; forza preferenza per assegnamento operatori identico al master e abilita core preemptive

### `cache`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve modello cache |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |

### `core_pruning`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve di test soddisfacibilità nel pruning |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `additional_info` | `list[str]` | Flag passati al modello subproblem usato nel pruning |

### `core_expansion`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve per matching nell’espansione |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |

### `subsumption`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve per confronto giorni (day expansion) |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |

### 5.5 Config single-pass (`configs/single_pass_solver_config.yaml`)

Parametri `base`:

| Campo | Tipo / Valori | Significato |
|---|---|---|
| `problem_type` | `'monolithic' \| 'fat-master' \| 'slim-master' \| 'fat-subproblem' \| 'slim-subproblem'` | Modello da risolvere |
| `solver_name` | `'gurobi' \| 'glpk'` | Solver MILP usato dal single-pass |
| `solver.time_limit` | `int` secondi | Time limit solve |
| `solver.memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `solver.additional_info` | `list[str]` | Flag opzionali modello |

Flag `solver.additional_info`:

- `minimize_hospital_accesses`
- `use_redundant_operator_cut`
- `use_redundant_patient_cut`

### 5.6 Config analyzer (`configs/analyzer_config.yaml`)

| Campo | Tipo | Significato |
|---|---|---|
| `configs_to_do`, `configs_to_avoid` | `list[str]` | Filtri config |
| `groups_to_do`, `groups_to_avoid` | `list[str]` | Filtri gruppo |
| `instances_to_do`, `instances_to_avoid` | `list[str]` | Filtri istanza |
| `do_instance_analysis` | `bool` | Estrae KPI istanza master |
| `do_master_result_analysis` | `bool` | Estrae KPI risultati master/final/cache/core/log |
| `do_subproblem_result_analysis` | `bool` | Estrae KPI istanze+risultati+log sottoproblema |

Nota: i tre flag `do_*_analysis` devono essere presenti nel file di config.

### 5.7 Config plotter (`configs/plotter_config.yaml`)

| Campo | Tipo | Significato |
|---|---|---|
| `configs_to_do`, `configs_to_avoid` | `list[str]` | Filtri config |
| `groups_to_do`, `groups_to_avoid` | `list[str]` | Filtri gruppo |
| `instances_to_do`, `instances_to_avoid` | `list[str]` | Filtri istanza |
| `plots_to_do` | `list[str]` | Elenco grafici da produrre |

Valori `plots_to_do`:

- `best_instance`
- `best_instance_subproblems`
- `core_gantt`
- `result_value_vs_time`
- `core_info`
- `solving_times`
- `solving_times_by_day`
- `requests_per_patient`
- `equal_requests_between_iterations`
- `aggregate_best_solution_value` (attualmente incompleto)

## 6) Output: file e cartelle generate

### 6.1 Output generatore

```text
instances/
└── <group_name>/
    ├── inst_00.json
    ├── inst_01.json
    └── ...
```

### 6.2 Output solver iterativo

```text
results/
└── <config>__<group>__<instance>/
    ├── master_instance.json
    ├── config.yaml
    ├── best_final_result_so_far.json
    ├── iter_1/
    │   ├── master_log.log
    │   ├── master_result.json
    │   ├── subproblem_day_<d>_instance.json
    │   ├── subproblem_day_<d>_log.log
    │   ├── subproblem_day_<d>_result.json
    │   ├── final_result.json
    │   ├── [cache_log.log]
    │   ├── [cache_matching.json]
    │   ├── [cache_final_result.json]
    │   ├── [true_cache_finds.json]
    │   ├── [generalist_cores.json]
    │   ├── [basic_cores.json]
    │   ├── [reduced_cores.json]
    │   ├── [pruned_cores.json]
    │   ├── [expanded_cores.json]
    │   └── [preemptive_cores.json]
    ├── iter_2/
    └── ...
```

Le parentesi `[]` indicano file opzionali (dipendono dalla config e dall’iterazione).

### 6.3 Output single-pass

```text
single_pass_results/
└── <config>__<group>__<instance>/
    ├── instance.json
    ├── config.yaml
    ├── solver_log.log
    └── result.json
```

### 6.4 Output analyzer

```text
results/
└── analysis/
    ├── instance_analysis.xlsx
    ├── master_result_analysis.xlsx
    └── subproblem_result_analysis.xlsx
```

### 6.5 Output plotter

```text
results/
└── <config>__<group>__<instance>/
    └── plots/
        ├── result_value_vs_time.png
        ├── solving_times.png
        ├── solving_times_by_day.png
        ├── requests_per_patient.png
        ├── cores.png
        ├── equal_requests_between_iterations.png
        ├── best_result/
        └── cores/
```

## 7) KPI estratti dall’analyzer

Queste chiavi compaiono come colonne negli Excel.

### 7.1 Master instance

- `day_number`
- `care_unit_total_number`
- `operator_total_number`
- `patient_number`
- `total_window_number`
- `average_care_unit_per_day`
- `min_care_unit_duration`
- `max_care_unit_duration`
- `average_care_unit_duration`
- `min_operator_duration`
- `max_operator_duration`
- `average_operator_duration`
- `min_service_duration`
- `max_service_duration`
- `average_service_duration`
- `operator_total_duration`
- `total_time_slots_requested`
- `request_over_disponibility_ratio`
- `min_window_size`
- `max_window_size`
- `average_window_size`
- `min_patient_request_number`
- `max_patient_request_number`
- `average_patient_request_number`
- `total_overlapping_windows`
- `min_windows_overapping_per_patient`
- `max_windows_overapping_per_patient`
- `average_windows_overapping_per_patient`
- `min_day_number_used_per_patient`
- `max_day_number_used_per_patient`
- `average_day_number_used_per_patient`

### 7.2 Master/final result

- `day_number`
- `patient_number`
- `total_scheduled_request_number`
- `total_scheduled_request_duration`
- `total_rejected_request_number`
- `total_rejected_request_duration`
- `min_scheduled_request_number_per_day`
- `max_scheduled_request_number_per_day`
- `average_scheduled_request_number_per_day`
- `min_scheduled_request_duration_per_day`
- `max_scheduled_request_duration_per_day`
- `average_scheduled_request_duration_per_day`
- `min_patients_per_day`
- `max_patients_per_day`
- `average_patients_per_day`
- `min_day_number_used_per_patient`
- `max_day_number_used_per_patient`
- `average_day_number_used_per_patient`
- `min_request_number_per_patient_same_day`
- `max_request_number_per_patient_same_day`
- `average_request_number_per_patient_same_day`
- `min_request_duration_per_patient_same_day`
- `max_request_duration_per_patient_same_day`
- `average_request_duration_per_patient_same_day`
- `min_care_unit_used_per_patient_same_day`
- `max_care_unit_used_per_patient_same_day`
- `average_care_unit_used_per_patient_same_day`
- `total_operator_used_per_patient` (fat/final)
- `min_operator_used_per_patient` (fat/final)
- `max_operator_used_per_patient` (fat/final)
- `average_operator_used_per_patient` (fat/final)
- `total_patient_served_per_operator` (fat/final)
- `min_patient_served_per_operator` (fat/final)
- `max_patient_served_per_operator` (fat/final)
- `average_patient_served_per_operator` (fat/final)
- `objective_value` (final)
- `total_time_slots_remaining` (final)
- `min_time_slots_remaining_per_day` (final)
- `max_time_slots_remaining_per_day` (final)
- `average_time_slots_remaining_per_day` (final)

### 7.3 Subproblem instance

- `care_unit_number`
- `operator_total_number`
- `patient_number`
- `total_request_number`
- `min_care_unit_duration`
- `max_care_unit_duration`
- `average_care_unit_duration`
- `min_operator_duration`
- `max_operator_duration`
- `average_operator_duration`
- `min_service_duration`
- `max_service_duration`
- `average_service_duration`
- `operator_total_duration`
- `total_time_slots_requested`
- `request_over_disponibility_ratio`
- `min_patient_request_number`
- `max_patient_request_number`
- `average_patient_request_number`
- `min_care_units_used_per_patient`
- `max_care_units_used_per_patient`
- `average_care_units_used_per_patient`

### 7.4 Subproblem result

- `patient_number`
- `total_scheduled_request_number`
- `total_scheduled_request_duration`
- `rejected_request_number`
- `rejected_request_duration`
- `min_care_units_used_per_patient`
- `max_care_units_used_per_patient`
- `average_care_units_used_per_patient`
- `min_scheduled_request_duration`
- `max_scheduled_request_duration`
- `average_scheduled_request_duration`
- `min_request_number_per_patient`
- `max_request_number_per_patient`
- `average_request_number_per_patient`
- `min_request_duration_per_patient`
- `max_request_duration_per_patient`
- `average_request_duration_per_patient`
- `min_operator_used_per_patient`
- `max_operator_used_per_patient`
- `average_operator_used_per_patient`
- `min_patient_served_per_operator`
- `max_patient_served_per_operator`
- `average_patient_served_per_operator`

### 7.5 Core

- `core_number`
- `min_core_size`
- `max_core_size`
- `average_core_size`
- `min_core_reason_size`
- `max_core_reason_size`
- `average_core_reason_size`
- `min_patient_number_per_core`
- `max_patient_number_per_core`
- `average_patient_number_per_core`
- `min_care_unit_number_per_core`
- `max_care_unit_number_per_core`
- `average_care_unit_number_per_core`
- `min_total_duration_per_core`
- `max_total_duration_per_core`
- `average_total_duration_per_core`
- `min_core_day_saturation_percentage`
- `max_core_day_saturation_percentage`
- `average_core_day_saturation_percentage`

### 7.6 Log Gurobi

- `status`
- `objective_value`
- `upper_bound`
- `gap`
- `root_relaxation`
- `time`
- `constraint_number`
- `variable_number`
- `presolved_constraint_number`
- `presolved_variable_number`
- `best_solution_time`

## 8) Note importanti sul codice attuale

- In `solver.py`, l’early stop con approssimazione usa la chiave `percentage_of_optimum_approach`. Se `early_stop_optimum_approximation_percentage != 1.0` e la chiave non è presente, si ottiene errore.
- In `analyzer.py`, i flag `do_instance_analysis`, `do_master_result_analysis`, `do_subproblem_result_analysis` sono attesi come obbligatori.
- In `plotter.py`, il plot `aggregate_best_solution_value` è marcato ma il relativo modulo è incompleto.
- In `subproblem_generator.py`, con `type: 'fat'` è presente un problema nell’ordinamento finale delle richieste: verificare questo caso prima di usarlo in produzione.
- In `cores_analyzer.py`, le metriche su numero operatori per core non risultano popolate per un controllo di tipo non allineato all’implementazione corrente.

## 9) Comandi utili aggiuntivi

Generazione istanze subproblem:

```bash
python generator.py -c configs/subproblem_generator_config.yaml -o subproblem_instances --overwrite
```

Risoluzione single-pass:

```bash
python single_pass_solver.py -c configs/single_pass_solver_config.yaml -i instances -o single_pass_results --overwrite
```
