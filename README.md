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

In pratica il plotter copre due famiglie di grafici:

- grafici "strutturali" della singola istanza/soluzione:
  - `best_instance`
  - `best_instance_subproblems`
  - `core_gantt`
- grafici "analitici" batch costruiti dai KPI estratti dall'analyzer:
  - `result_value_vs_time`
  - `core_info`
  - `solving_times`
  - `solving_times_by_day`
  - `requests_per_patient`
  - `equal_requests_between_iterations`
  - `aggregate_best_solution_value` (stub/incompleto)

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

Nota: se un file config vecchio non contiene i tre flag `do_*_analysis`, `analyzer.py` usa fallback `True` per tutti.

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

Significato dei plot disponibili:

| Plot | Modalità | Input richiesti | Output | Cosa rappresenta | Stato |
|---|---|---|---|---|---|
| `best_instance` | `all` | `master_instance.json` + `best_final_result_so_far.json` | `plots/best_result/final_result.png` | Vista compatta giorno/care unit del miglior risultato finale trovato. Ogni rettangolo rappresenta una richiesta schedulata; il colore identifica la care unit. | Implementato |
| `best_instance_subproblems` | `all` | `master_instance.json` + `best_final_result_so_far.json` | `plots/best_result/subproblem_day_<d>.png` | Gantt per ogni giorno del miglior risultato: asse `x` sui time slot, asse `y` sugli operatori, rettangoli pieni per richieste assegnate. | Implementato |
| `core_gantt` | `all` | file core per iterazione + `subproblem_day_<d>_result.json` | `plots/cores/iter_<k>/core_<i>.png` | Gantt del singolo core: i `components` del core sono disegnati sul calendario operatori del giorno, la `reason` e' mostrata in overlay semi-trasparente. | Implementato |
| `result_value_vs_time` | `all` | `analysis/master_result_analysis.xlsx` + `analysis/subproblem_result_analysis.xlsx` | `plots/result_value_vs_time.png` | Evoluzione del valore soluzione nel tempo cumulato di solve: linea master, linea final/subproblem, linea cache se presente. | Implementato |
| `core_info` | `all` | `analysis/master_result_analysis.xlsx` | `plots/cores.png` | Evoluzione per iterazione delle proprieta' dei core: numero, dimensione, durata relativa e numero di care unit coinvolte, separate per tipo di core. | Implementato |
| `solving_times` | `all` | `analysis/master_result_analysis.xlsx` + `analysis/subproblem_result_analysis.xlsx` | `plots/solving_times.png` | Tempi per iterazione di master, cache e sottoproblemi. Include anche variabilita' dei tempi dei singoli sottoproblemi tramite bande/error bar. | Implementato |
| `solving_times_by_day` | `all` | `analysis/subproblem_result_analysis.xlsx` | `plots/solving_times_by_day.png` | Heatmap giorno x iterazione dei tempi di solve del sottoproblema; una `x` nera marca i giorni ancora con richieste rifiutate. | Implementato |
| `requests_per_patient` | `all` | `analysis/master_result_analysis.xlsx` | `plots/requests_per_patient.png` | Pannello 2x2 sull'evoluzione delle richieste per paziente e delle risorse usate per paziente, confrontando master e final lungo le iterazioni. | Implementato, ma le metriche sottostanti ereditano i naming talvolta fuorvianti dei KPI analyzer |
| `equal_requests_between_iterations` | `all` | `master_result.json` + `final_result.json` per iterazione | `plots/equal_requests_between_iterations.png` | Stabilita' inter-iterazione: confronta il numero totale di richieste e quante richieste restano uguali rispetto all'iterazione precedente, per master e final. | Implementato |
| `aggregate_best_solution_value` | `all` | `analysis/master_result_analysis.xlsx` | previsto `plots/...` | Doveva aggregare i migliori valori soluzione su piu' istanze/configurazioni, ma il modulo oggi si ferma dopo una stampa intermedia e non salva il grafico. | Incompleto |

Modalità `instance`:

- legge una singola cartella risultato `<config>__<group>__<instance>`
- richiede `master_instance.json`, `iter_<k>/master_result.json`, `iter_<k>/final_result.json`
- produce:
  - `master_result.png`
  - `final_result.png`
  - `subproblem_day_<d>.png`
- è pensata come modalità di debug visivo puntuale di una specifica iterazione

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
        │   ├── final_result.png
        │   └── subproblem_day_<d>.png
        └── cores/
            └── iter_<k>/
                └── core_<i>.png
```

Modalità `instance` scrive invece nella cartella di output specificata:

```text
<output_dir>/
├── master_result.png
├── final_result.png
└── subproblem_day_<d>.png
```

## 7) KPI estratti dall’analyzer

Queste chiavi compaiono come colonne negli Excel.

Regola generale: per ogni famiglia `min_*`, `max_*`, `average_*`, il codice costruisce prima una collezione di valori elementari `X = {x_1, ..., x_n}` e poi calcola:

- `min_* = min(X)`
- `max_* = max(X)`
- `average_* = sum(X) / n`

Sotto, per ogni famiglia, e' indicato che cosa rappresenta l'elemento `x_i`. Quando il nome del KPI non coincide perfettamente con il valore realmente calcolato dal codice, la discrepanza e' segnalata esplicitamente.

### 7.1 Master instance

- `day_number`, `care_unit_total_number`, `operator_total_number`, `patient_number`, `total_window_number`: conteggi diretti di giorni, care unit giornaliere, operatori, pazienti e finestre temporali richieste.
- `average_care_unit_per_day`: `care_unit_total_number / day_number`.
- `care_unit_duration`: per ogni care unit in ogni giorno, somma delle durate degli operatori assegnati a quella care unit.
  Formula elementare: `care_unit_duration(cu, d) = sum_{op in cu,d} op.duration`.
  Colonne: `min_care_unit_duration`, `max_care_unit_duration`, `average_care_unit_duration`.
- `operator_duration`: durata del singolo operatore.
  Formula elementare: `operator_duration(op) = op.duration`.
  Colonne: `min_operator_duration`, `max_operator_duration`, `average_operator_duration`.
- `service_duration`: durata del singolo servizio.
  Formula elementare: `service_duration(s) = service.duration`.
  Colonne: `min_service_duration`, `max_service_duration`, `average_service_duration`.
- `operator_total_duration`: somma di tutte le durate operatore dell'istanza.
  Formula: `sum operator_duration(op)`.
  Colonna: `operator_total_duration`.
- `window_size`: ampiezza della singola finestra di richiesta.
  Formula elementare: `window.end - window.start + 1`.
  Colonne: `min_window_size`, `max_window_size`, `average_window_size`.
- `total_time_slots_requested`: somma delle ampiezze di tutte le finestre.
  Formula: `sum window_size(w)`.
  Colonna: `total_time_slots_requested`.
- `request_over_disponibility_ratio`: rapporto carico/capacita' nominale.
  Formula: `total_time_slots_requested / operator_total_duration`.
  Colonna: `request_over_disponibility_ratio`.
- `patient_request_number`: attenzione, il nome e' fuorviante.
  Il codice usa `sum(len(item) for item in patient.requests.items())`, quindi di fatto calcola `2 * numero_di_servizi_distinti_richiesti_dal_paziente`, non il numero di finestre e non il numero di richieste elementari.
  Colonne: `min_patient_request_number`, `max_patient_request_number`, `average_patient_request_number`.
- `windows_overlapping_per_patient`: per ogni paziente, numero di coppie di finestre che si sovrappongono temporalmente.
  Formula elementare: conteggio delle coppie `(w_i, w_j)` con `i < j` e `w_i.overlaps(w_j) = True`.
  Colonne: `min_windows_overlapping_per_patient`, `max_windows_overlapping_per_patient`, `average_windows_overlapping_per_patient`.
- `total_overlapping_windows`: somma di `windows_overlapping_per_patient` su tutti i pazienti.
  Colonna: `total_overlapping_windows`.
- `day_number_used_per_patient`: anche qui il nome e' fuorviante.
  Il codice non conta i giorni distinti usati dal paziente, ma la somma delle ampiezze delle sue finestre.
  Formula elementare: `sum_{w del paziente} (w.end - w.start + 1)`.
  Colonne: `min_day_number_used_per_patient`, `max_day_number_used_per_patient`, `average_day_number_used_per_patient`.

### 7.2 Master/final result

- `day_number`: numero di giorni presenti in `result.scheduled`.
- `patient_number`: numero di pazienti distinti che compaiono almeno una volta nelle richieste schedulate.
- `total_scheduled_request_number`: numero totale di richieste schedulate.
  Formula: `sum_d len(result.scheduled[d])`.
  Colonna: `total_scheduled_request_number`.
- `total_scheduled_request_duration`: somma delle durate di tutte le richieste schedulate.
  Formula: `sum_d sum_{r in scheduled[d]} duration(r.service)`.
  Colonna: `total_scheduled_request_duration`.
- `total_rejected_request_number`, `total_rejected_request_duration`: conteggio e durata totale delle richieste in `result.rejected`.
- `scheduled_request_number_per_day`: per ogni giorno, numero di richieste schedulate in quel giorno.
  Formula elementare: `len(result.scheduled[d])`.
  Colonne: `min_scheduled_request_number_per_day`, `max_scheduled_request_number_per_day`, `average_scheduled_request_number_per_day`.
- `scheduled_request_duration_per_day`: per ogni giorno, somma delle durate dei servizi schedulati.
  Formula elementare: `sum_{r in scheduled[d]} duration(r.service)`.
  Colonne: `min_scheduled_request_duration_per_day`, `max_scheduled_request_duration_per_day`, `average_scheduled_request_duration_per_day`.
- `patients_per_day`: per ogni giorno, numero di pazienti distinti serviti quel giorno.
  Formula elementare: `len({r.patient_name : r in scheduled[d]})`.
  Colonne: `min_patients_per_day`, `max_patients_per_day`, `average_patients_per_day`.
- `day_number_used_per_patient`: per ogni paziente, numero di giorni in cui compare almeno una richiesta schedulata.
  Formula elementare: `len({d : esiste r in scheduled[d] con r.patient = p})`.
  Colonne: `min_day_number_used_per_patient`, `max_day_number_used_per_patient`, `average_day_number_used_per_patient`.
- `request_number_per_patient_same_day`: per ogni coppia `(giorno, paziente presente in quel giorno)`, numero di richieste schedulate di quel paziente in quel giorno.
  Colonne: `min_request_number_per_patient_same_day`, `max_request_number_per_patient_same_day`, `average_request_number_per_patient_same_day`.
- `request_duration_per_patient_same_day`: per ogni coppia `(giorno, paziente)`, somma delle durate dei servizi di quel paziente in quel giorno.
  Colonne: `min_request_duration_per_patient_same_day`, `max_request_duration_per_patient_same_day`, `average_request_duration_per_patient_same_day`.
- `care_unit_used_per_patient_same_day`: per ogni coppia `(giorno, paziente)`, numero di care unit distinte coinvolte dalle richieste del paziente in quel giorno.
  Colonne: `min_care_unit_used_per_patient_same_day`, `max_care_unit_used_per_patient_same_day`, `average_care_unit_used_per_patient_same_day`.
- `operator_used_per_patient` e `patient_served_per_operator` nei risultati `fat/final`: i nomi sono fuorvianti.
  Il codice non conta operatori distinti per paziente ne' pazienti distinti per operatore.
  Conta invece, per ogni `(giorno, paziente)`, quante richieste sono state assegnate a quel paziente, e per ogni `(giorno, operatore)`, quante richieste sono state assegnate a quell'operatore.
  In pratica queste famiglie misurano il carico in numero di richieste, non la cardinalita' di operatori/pazienti distinti.
  Colonne per paziente: `total_operator_used_per_patient`, `min_operator_used_per_patient`, `max_operator_used_per_patient`, `average_operator_used_per_patient`.
  Colonne per operatore: `total_patient_served_per_operator`, `min_patient_served_per_operator`, `max_patient_served_per_operator`, `average_patient_served_per_operator`.
- `objective_value` (solo `final_result`): valore obiettivo ricalcolato dall'analyzer.
  Formula: per ogni finestra `(p, s, w)` dell'istanza, se esiste almeno un giorno della finestra in cui il servizio `s` del paziente `p` e' schedulato, si aggiunge `duration(s) * priority(p)`.
  Nota: in questa analisi la penalizzazione `minimize_hospital_accesses` non viene applicata, perche' l'analyzer richiama `get_result_value(..., [], None)`.
  Colonna: `objective_value`.
- `time_slots_remaining_per_day` (solo `final_result`): capacita' residua per giorno.
  Formula elementare: `sum durata operatori del giorno - sum durata richieste schedulate del giorno`.
  Colonne: `min_time_slots_remaining_per_day`, `max_time_slots_remaining_per_day`, `average_time_slots_remaining_per_day`.
- `total_time_slots_remaining`: somma dei residui giornalieri.
  Colonna: `total_time_slots_remaining`.

### 7.3 Subproblem instance

- `care_unit_number`, `operator_total_number`, `patient_number`, `total_request_number`: conteggi diretti sul sottoproblema.
  `total_request_number = sum_p len(patient.requests)`.
- `care_unit_duration`, `operator_duration`, `service_duration`: stessi significati del caso master, ma limitati alla singola istanza di sottoproblema.
  Colonne care unit: `min_care_unit_duration`, `max_care_unit_duration`, `average_care_unit_duration`.
  Colonne operatori: `min_operator_duration`, `max_operator_duration`, `average_operator_duration`.
  Colonne servizi: `min_service_duration`, `max_service_duration`, `average_service_duration`.
- `operator_total_duration`: somma delle durate operatore del sottoproblema.
  Colonna: `operator_total_duration`.
- `total_time_slots_requested`: somma delle durate dei servizi richiesti nel sottoproblema.
  Nel caso `fat`: `sum duration(request.service_name)`.
  Nel caso `slim`: `sum duration(service_name)`.
  Colonna: `total_time_slots_requested`.
- `request_over_disponibility_ratio`: `total_time_slots_requested / operator_total_duration`.
  Colonna: `request_over_disponibility_ratio`.
- `patient_request_number`: per ogni paziente, numero di richieste presenti nel sottoproblema.
  Formula elementare: `len(patient.requests)`.
  Colonne: `min_patient_request_number`, `max_patient_request_number`, `average_patient_request_number`.
- `care_units_used_per_patient`: per ogni paziente, numero di care unit distinte toccate dalle sue richieste.
  Colonne: `min_care_units_used_per_patient`, `max_care_units_used_per_patient`, `average_care_units_used_per_patient`.

### 7.4 Subproblem result

- Questi KPI sono calcolati quasi tutti sulle sole richieste schedulate; le sole metriche sui rejected sono `rejected_request_number` e `rejected_request_duration`.
- `patient_number`: numero di pazienti distinti che compaiono in `result.scheduled`.
  Nota: un paziente con sole richieste rigettate non contribuisce a questo KPI.
- `total_scheduled_request_number`: `len(result.scheduled)`.
  Colonna: `total_scheduled_request_number`.
- `total_scheduled_request_duration`: `sum_{r in scheduled} duration(r.service)`.
  Colonna: `total_scheduled_request_duration`.
- `rejected_request_number`, `rejected_request_duration`: conteggio e durata totale delle richieste rigettate.
- `care_units_used_per_patient`: per ogni paziente schedulato, numero di care unit distinte usate dalle sue richieste schedulate.
  Colonne: `min_care_units_used_per_patient`, `max_care_units_used_per_patient`, `average_care_units_used_per_patient`.
- `scheduled_request_duration`: durata della singola richiesta schedulata.
  Colonne: `min_scheduled_request_duration`, `max_scheduled_request_duration`, `average_scheduled_request_duration`.
- `request_number_per_patient`: per ogni paziente schedulato, numero di richieste schedulate del paziente.
  Colonne: `min_request_number_per_patient`, `max_request_number_per_patient`, `average_request_number_per_patient`.
- `request_duration_per_patient`: per ogni paziente schedulato, somma delle durate delle sue richieste schedulate.
  Colonne: `min_request_duration_per_patient`, `max_request_duration_per_patient`, `average_request_duration_per_patient`.
- `operator_used_per_patient` e `patient_served_per_operator`: anche qui i nomi sono fuorvianti.
  Il codice conta il numero di richieste schedulate per paziente e per operatore, non il numero di operatori distinti usati da un paziente ne' il numero di pazienti distinti serviti da un operatore.
  Colonne per paziente: `min_operator_used_per_patient`, `max_operator_used_per_patient`, `average_operator_used_per_patient`.
  Colonne per operatore: `min_patient_served_per_operator`, `max_patient_served_per_operator`, `average_patient_served_per_operator`.

### 7.5 Core

- `core_number`: numero di core nel file analizzato.
- `core_size`: numero di componenti del core.
  Formula elementare: `len(core.components)`.
  Colonne: `min_core_size`, `max_core_size`, `average_core_size`.
- `core_reason_size`: numero di elementi nella ragione del core.
  Formula elementare: `len(core.reason)`.
  Colonne: `min_core_reason_size`, `max_core_reason_size`, `average_core_reason_size`.
- `patient_number_per_core`: numero di pazienti distinti coinvolti nei componenti del core.
  Colonne: `min_patient_number_per_core`, `max_patient_number_per_core`, `average_patient_number_per_core`.
- `care_unit_number_per_core`: numero di care unit distinte coinvolte nei componenti del core.
  Colonne: `min_care_unit_number_per_core`, `max_care_unit_number_per_core`, `average_care_unit_number_per_core`.
- `total_duration_per_core`: somma delle durate dei servizi presenti nei componenti del core.
  Formula elementare: `sum_{c in core.components} duration(c.service_name)`.
  Colonne: `min_total_duration_per_core`, `max_total_duration_per_core`, `average_total_duration_per_core`.
- `core_day_saturation_percentage`: quota di capacita' giornaliera "coperta" dal core.
  Formula elementare: `total_duration_per_core / total_operator_duration_del_giorno_del_core`.
  Colonne: `min_core_day_saturation_percentage`, `max_core_day_saturation_percentage`, `average_core_day_saturation_percentage`.
- Il codice contiene anche una famiglia `operator_number_per_core`, ma nella versione attuale non viene popolata per via del controllo di tipo usato in `cores_analyzer.py`.
  Colonne previste ma oggi non popolate: `min_operator_number_per_core`, `max_operator_number_per_core`, `average_operator_number_per_core`.

### 7.6 Log Gurobi

- Questi KPI sono estratti testualmente dal log del solver con un parser scritto sul formato Gurobi. Se usi GLPK, molti campi possono mancare o restare vuoti.
- `status`: impostato a `optimal` se il log contiene `Optimal solution found`, a `time_limit` se contiene `Time limit reached`.
- `objective_value`, `upper_bound`, `gap`: letti dalla riga `Best objective ...`.
- `root_relaxation`: letto dalla riga `Root relaxation ...`; il parser salva `-1` o `-1.0` nei casi speciali `cutoff` o `limit`.
- `time`: letto dalla riga `Explored ...`, nel token che il parser interpreta come tempo finale di solve.
- `constraint_number`, `variable_number`: letti dalla riga `Optimize a model with ...`.
- `presolved_constraint_number`, `presolved_variable_number`: letti dalla riga `Presolved ...`.
- `best_solution_time`: tempo dell'ultimo incumbent letto dall'ultima riga del log che inizia con `H` o `*`.

## 8) Note importanti sul codice attuale

- In `solver.py`, l’early stop con approssimazione usa la chiave `percentage_of_optimum_approach`. Se `early_stop_optimum_approximation_percentage != 1.0` e la chiave non è presente, si ottiene errore.
- In `analyzer.py`, se i flag `do_instance_analysis`, `do_master_result_analysis`, `do_subproblem_result_analysis` mancano, viene usato fallback `True` per tutti e tre.
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
