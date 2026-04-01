# Scheduler Project

Framework per la generazione di istanze e la risoluzione MILP (Pyomo + Gurobi) di un problema di scheduling sanitario multi-giorno, con pipeline completa:

1. generazione istanze (`generator.py`)
2. solving (`solver.py` iterativo con core/caching, oppure `single_pass_solver.py`)
3. analisi dei risultati (`analyzer.py`)
4. plotting risultati di solving (`plotter.py`)
5. plotting strutturale delle istanze master (`master_instance_plotter.py`)

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

### 2.4-bis Genera grafici strutturali delle istanze master

```bash
python master_instance_plotter.py -i instances
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
├── master_instance_plotter.py
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

## 4) Flusso di lavoro generale:

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
- genera richieste finche' la somma delle durate dei servizi richiesti raggiunge il target di saturazione (`request_over_disponibility_ratio`)
- estrae l'ampiezza di ogni finestra con distribuzione triangolare tra `1` e `window_max_size`, con moda `ceil(window_max_size / 3)`
- opzionalmente copia finestre tra servizi dello stesso paziente (`same_window_percentage`)
- opzionalmente ripara le finestre dello stesso servizio dello stesso paziente per renderle disgiunte (`enforce_same_service_disjoint_windows`)

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
- aggiorna l'analisi centralizzata in `results/analysis/`
  - `instance_analysis.xlsx`
  - `master_result_analysis.csv`
  - `subproblem_result_analysis.csv`

### 4.5 Plotter (`plotter.py`)

- modalità `all`: grafici batch usando risultati + tabelle di analisi centralizzate
- modalità `instance`: plot dettagliato di una singola istanza/iterazione
- i plot batch basati sulle tabelle analitiche (`result_value_vs_time`, `core_info`, `solving_times`, `solving_times_by_day`, `requests_per_patient`, `aggregate_best_solution_value` e la famiglia `comparison/group` di `experiment_group_comparison`) richiedono prima l’esecuzione di `analyzer.py`

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

### 4.6 Plotter istanze master (`master_instance_plotter.py`)

- scansiona una root di istanze master (`<input>/<group>/inst_*.json`)
- crea una cartella `plots_instances/`
- per ogni istanza genera grafici strutturali direttamente dall'istanza, senza passare dal solver
- produce anche grafici aggregati che confrontano tutte le istanze di tutti i gruppi

Plot per singola istanza:

- `patient_windows_gantt.png`
  - asse `x`: giorni
  - asse `y`: pazienti
  - ogni blocco rappresenta una finestra di richiesta
  - larghezza del blocco: `window.end - window.start + 1`
  - colore del blocco: care unit del servizio associato
  - se due finestre dello stesso paziente si sovrappongono, vengono impilate su righe distinte sotto lo stesso paziente

- `average_window_overlap_by_day.png`
  - per ogni giorno `d` e paziente `p`, definisce:
    - `overlap(p, d) = numero di finestre del paziente p che contengono il giorno d`
  - per ogni giorno viene mostrato il boxplot dei valori `overlap(p, d)` sui pazienti

- `weighted_window_overlap_by_day.png`
  - per ogni giorno `d` e paziente `p`, definisce:
    - `weighted_overlap(p, d) = somma su tutte le finestre w attive in d di 1 / |w|`
    - con `|w| = w.end - w.start + 1`
  - interpreta ogni finestra come distribuita uniformemente sulla propria ampiezza
  - per ogni giorno viene mostrato il boxplot dei valori `weighted_overlap(p, d)` sui pazienti

- `spread_capacity_heatmap.png`
  - righe: care unit
  - colonne: giorni
  - per ogni cella `(cu, d)`:
    - `spread(cu, d) = somma su tutte le richieste della care unit cu attive in d di duration(service) / |window|`
    - `capacity(cu, d) = somma delle durate degli operatori della care unit cu nel giorno d`
    - valore mostrato: `spread(cu, d) / capacity(cu, d)`
  - se `capacity(cu, d) = 0`, la cella resta vuota/NaN

Plot aggregati su tutte le istanze:

- `instance_daily_median_window_overlap_distribution.png`
  - per ogni istanza e giorno `d`:
    - `m(d) = mediana sui pazienti di overlap(p, d)`
  - il boxplot dell'istanza usa i valori `m(d)` su tutti i giorni

- `instance_daily_weighted_window_overlap_distribution.png`
  - per ogni istanza e giorno `d`:
    - `m_w(d) = mediana sui pazienti di weighted_overlap(p, d)`
  - il boxplot dell'istanza usa i valori `m_w(d)` su tutti i giorni

- `instance_daily_average_spread_capacity_distribution.png`
  - per ogni istanza e giorno `d`:
    - `avg_ratio(d) = media sulle care unit dei valori spread(cu, d) / capacity(cu, d)`
  - il boxplot dell'istanza usa i valori `avg_ratio(d)` su tutti i giorni con valore definito

- `instance_request_count_distribution.png`
  - per ogni paziente `p` dell'istanza:
    - `request_count(p) = numero totale di finestre richieste dal paziente`
  - il boxplot dell'istanza usa i valori `request_count(p)` su tutti i pazienti

- `instance_duration_weighted_request_count_distribution.png`
  - per ogni paziente `p`:
    - `weighted_request_count(p) = somma sui servizi richiesti di len(windows(service)) * duration(service)`
  - il boxplot dell'istanza usa i valori `weighted_request_count(p)` su tutti i pazienti

- `instance_same_service_overlapping_window_distribution.png`
  - per ogni paziente `p` e servizio `s`:
    - una finestra `w_i` conta se esiste almeno una finestra `w_j` dello stesso servizio `s` dello stesso paziente `p`, con `i != j`, tale che `w_i` e `w_j` si sovrappongono in almeno un giorno
  - per ogni paziente:
    - `same_service_overlap_count(p) = numero totale di finestre che soddisfano la condizione sopra, sommando su tutti i servizi`
  - il boxplot dell'istanza usa i valori `same_service_overlap_count(p)` su tutti i pazienti

Nota importante sulle scale:

- il file `master_instance_plotter.py` contiene il flag globale:
  - `USE_GLOBAL_PLOT_SCALES = True`
- quando attivo:
  - i boxplot `average_window_overlap_by_day.png` e `weighted_window_overlap_by_day.png` condividono il limite superiore dell'asse `y`
  - le `spread_capacity_heatmap.png` condividono la stessa scala colore (`vmax` globale)
- non vengono invece omologati numero di pazienti, numero di care unit o numero di giorni: questi restano locali all'istanza

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
| `window_max_size` | `int` | Ampiezza massima inclusiva della finestra richiesta (giorni), estratta con distribuzione triangolare tra `1` e `window_max_size` e moda `ceil(window_max_size / 3)` |
| `same_window_percentage` | `float` in `[0,1]` | Probabilità di copiare finestre tra servizi dello stesso paziente |
| `enforce_same_service_disjoint_windows` | `bool` | Se `true`, post-processa le finestre dello stesso servizio dello stesso paziente per renderle disgiunte con shift e, se necessario, riduzione della tolleranza |

Con `enforce_same_service_disjoint_windows: true`, il generatore applica il repair dopo tutta la costruzione dell'istanza, quindi anche dopo l'eventuale effetto di `same_window_percentage`. Se per uno stesso paziente/servizio il numero di richieste supera il numero di giorni dell'orizzonte, la generazione fallisce con errore esplicito perché non esiste una disposizione pairwise disjoint neppure riducendo tutte le finestre a lunghezza `1`.

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
| `threads` | `int` | Solo Gurobi. `0` = automatico/default Gurobi; `1` = single-thread; `N > 1` limita il solver a `N` thread |
| `method` | `str \| int` | Solo Gurobi. Valori supportati: `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi (`-1`, `0`, `1`, `2`, `3`) |
| `presolve` | `str \| int` | Solo Gurobi. Valori supportati: `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi (`-1`, `0`, `1`, `2`) |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. Mappa `MemLimit`. Se `> 0` impone un limite hard interno al solver; `0` o valore assente = disabilitato |
| `additional_info` | `list[str]` | Flag opzionali |

Flag `master.additional_info`:

- `minimize_hospital_accesses`: penalizza uso di molti giorni per paziente in obiettivo
- `use_optimality_cuts`: aggiunge tagli di ottimalità per le strutture con master slim; il solver salva anche `optimality_cut_count_added`, `optimality_cut_total_count` e `optimality_cut_time` per iterazione in `master_result_analysis`

### `subproblem`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve di ciascun sottoproblema giornaliero |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `threads` | `int` | Solo Gurobi. `0` = automatico; utile ridurlo per contenere RAM sui solve più pesanti |
| `method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi |
| `presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. `MemLimit`; `0` = disabilitato |
| `additional_info` | `list[str]` | Flag opzionali |

Flag `subproblem.additional_info`:

- `use_redundant_operator_cut`: vincolo ridondante sulla capacità operatore (modelli fat)
- `preemptive_forbidding`: solo `fat-fat`; forza preferenza per assegnamento operatori identico al master e abilita core preemptive

### `cache`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve modello cache |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `threads` | `int` | Solo Gurobi. `0` = automatico |
| `method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi |
| `presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. `MemLimit`; `0` = disabilitato |

### `core_pruning`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve di test soddisfacibilità nel pruning |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `threads` | `int` | Solo Gurobi. `0` = automatico |
| `method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi |
| `presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. `MemLimit`; `0` = disabilitato |
| `additional_info` | `list[str]` | Flag passati al modello subproblem usato nel pruning |

### `core_expansion`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve per matching nell’espansione |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `threads` | `int` | Solo Gurobi. `0` = automatico |
| `method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi |
| `presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. `MemLimit`; `0` = disabilitato |

### `subsumption`

| Campo | Tipo | Significato |
|---|---|---|
| `time_limit` | `int` secondi | Time limit solve per confronto giorni (day expansion) |
| `memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `threads` | `int` | Solo Gurobi. `0` = automatico |
| `method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi |
| `presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi |
| `hard_memory_limit` | `int \| float` GB | Solo Gurobi. `MemLimit`; `0` = disabilitato |

Note operative sui nuovi campi Gurobi:

- `threads`: ridurre questo valore e' spesso il modo piu' efficace per contenere i picchi RAM. `0` lascia decidere a Gurobi.
- `method`: nel codice i valori stringa vengono mappati cosi':
  - `auto -> -1`
  - `primal -> 0`
  - `dual -> 1`
  - `barrier -> 2`
  - `concurrent -> 3`
- `presolve`: nel codice i valori stringa vengono mappati cosi':
  - `auto -> -1`
  - `off -> 0`
  - `conservative -> 1`
  - `aggressive -> 2`
- `hard_memory_limit`: usa `MemLimit`, diverso da `memory_limit` che usa `SoftMemLimit`. `SoftMemLimit` e' un limite soft del solver; `MemLimit` e' piu' rigido. Su modelli molto grandi, usare entrambi e' spesso preferibile.

### 5.5 Config single-pass (`configs/single_pass_solver_config.yaml`)

Parametri `base`:

| Campo | Tipo / Valori | Significato |
|---|---|---|
| `problem_type` | `'monolithic' \| 'fat-master' \| 'slim-master' \| 'fat-subproblem' \| 'slim-subproblem'` | Modello da risolvere |
| `solver_name` | `'gurobi' \| 'glpk'` | Solver MILP usato dal single-pass |
| `solver.time_limit` | `int` secondi | Time limit solve |
| `solver.memory_limit` | `int` GB | Gurobi: `SoftMemLimit`, GLPK: `memlim` (in MB) |
| `solver.threads` | `int` | Solo Gurobi. `0` = automatico/default Gurobi; `1` = single-thread; `N > 1` limita il numero di thread |
| `solver.method` | `str \| int` | Solo Gurobi. `auto`, `primal`, `dual`, `barrier`, `concurrent`, oppure intero Gurobi (`-1`, `0`, `1`, `2`, `3`) |
| `solver.presolve` | `str \| int` | Solo Gurobi. `auto`, `off`, `conservative`, `aggressive`, oppure intero Gurobi (`-1`, `0`, `1`, `2`) |
| `solver.hard_memory_limit` | `int \| float` GB | Solo Gurobi. Mappa `MemLimit`. `0` = disabilitato |
| `solver.additional_info` | `list[str]` | Flag opzionali modello |

Flag `solver.additional_info`:

- `minimize_hospital_accesses`
- `use_redundant_operator_cut`
- `use_redundant_patient_cut`

Note operative anche per il single-pass:

- `solver.threads: 0` mantiene il comportamento corrente.
- `solver.method: auto` mantiene il comportamento corrente.
- `solver.presolve: auto` mantiene il comportamento corrente; impostare `off` o `conservative` puo' ridurre l'aggressivita' del presolve e, in alcuni casi, i picchi di memoria.
- `solver.hard_memory_limit: 0` mantiene il comportamento corrente; se imposti un valore positivo, Gurobi attiva anche `MemLimit` oltre a `SoftMemLimit`.

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
Di default `analyzer.py` aggiorna un'unica analisi centralizzata in `results/analysis/` e riusa i blocchi gia' analizzati per le triple `(config, group, instance)` che non sono cambiate nella cartella risultati corrente. I filtri `configs_to_do` / `groups_to_do` / `instances_to_do` decidono quale sottoinsieme della base analitica centrale va sincronizzato; le righe fuori filtro restano intatte. Usa `--overwrite` se vuoi forzare il ricalcolo completo del solo sottoinsieme selezionato, mantenendo gli altri dati centrali.

### 5.7 Config plotter (`configs/plotter_config.yaml`)

| Campo | Tipo | Significato |
|---|---|---|
| `configs_to_do`, `configs_to_avoid` | `list[str]` | Filtri config |
| `groups_to_do`, `groups_to_avoid` | `list[str]` | Filtri gruppo |
| `instances_to_do`, `instances_to_avoid` | `list[str]` | Filtri istanza |
| `plots_to_do` | `dict[str, list[str]]` | Selezione dei plot batch per livello di aggregazione: `comparison`, `group`, `run` |
| `run_plot_configs_to_do` | `list[str]` | Filtro opzionale solo per i plot `run`: restringe i run-level plots a specifiche config (`['all']` o assente = nessun filtro aggiuntivo) |
| `run_plot_groups_to_do` | `list[str]` | Filtro opzionale solo per i plot `run`: restringe i run-level plots a specifici gruppi |
| `run_plot_instances_to_do` | `list[str]` | Filtro opzionale solo per i plot `run`: restringe i run-level plots a una o piu' istanze |
| `experiment_group_comparison_config_order` | `list[str]` | Ordine visuale dei test nei plot aggregati `experiment_group_comparison` |
| `experiment_group_comparison_config_aliases` | `dict[str, str]` | Alias etichette dei test nei plot aggregati `experiment_group_comparison` |
| `experiment_group_comparison_configs_to_do` | `list[str]` | Filtro opzionale solo per `experiment_group_comparison`: confronta solo i test indicati senza toccare gli altri plot batch |
| `experiment_group_comparison_output_subdir` | `str` | Sotto-cartella relativa a `results/plots/` dove salvare la comparazione filtrata, utile per mantenere piu' confronti distinti |
| `experiment_group_comparison_row_split_priority` | `list[str]` | Ordine opzionale di separazione verticale delle figure aggregate su piu' righe. Valori ammessi: `patient_number`, `care_unit_number`, `test`. Lista vuota = layout attuale a riga singola |

Schema canonico di `plots_to_do`:

```yaml
plots_to_do:
  comparison:
    - comparison_box_lbbd_iterations
    - comparison_scatter_core_generation_progress
  group:
    - bubble_duration_ratio_group
    - core_generation_profile_group
  run:
    - result_value_vs_time
    - solving_times
    - core_info
```

Retrocompatibilita' in lettura:

- il vecchio formato piatto, ad esempio
  ```yaml
  plots_to_do:
    - result_value_vs_time
    - core_info
    - experiment_group_comparison
  ```
  e' ancora accettato da CLI e GUI;
- se la GUI salva il file, lo normalizza sempre nel nuovo schema annidato;
- nel formato legacy, `experiment_group_comparison` significa "tutti i plot `comparison` e `group` della famiglia aggregata".

Plot disponibili per livello:

- `comparison`
  - `comparison_box_lbbd_iterations`
  - `comparison_box_avg_cores_per_iteration`
  - `comparison_box_total_solving_time`
  - `comparison_performance_profile_total_time`
  - `comparison_box_final_gap_pct`
  - `comparison_box_final_objective_value`
  - `comparison_box_scheduled_duration_over_capacity_ratio`
  - `comparison_box_scheduled_services_ratio`
  - `comparison_bubble_duration_ratio_summary`
  - `comparison_bubble_core_count_vs_core_size`
  - `comparison_scatter_core_generation_progress`
  - `comparison_bar_optimal_count_with_mean_gap`
  - `comparison_bar_master_vs_subproblem_total_time`
  - `comparison_bar_master_vs_subproblem_time_share_pct`
  - `comparison_master_bar_and_subproblem_iteration_box`
  - `comparison_master_timeout_feasible_and_mean_gap_boxplots`
  - `comparison_master_same_day_request_grouping_boxplots`
- `group`
  - `bubble_duration_ratio_group`
  - `core_generation_profile_group`
- `run`
  - `best_instance`
  - `best_instance_subproblems`
  - `core_gantt`
  - `result_value_vs_time`
  - `core_info`
  - `solving_times`
  - `solving_times_by_day`
  - `requests_per_patient`
  - `equal_requests_between_iterations`

Note pratiche:

- `experiment_group_comparison_config_order` e `experiment_group_comparison_config_aliases` servono a controllare l'aspetto dei plot `comparison` e non filtrano i dati.
- `experiment_group_comparison_configs_to_do` filtra solo i plot `comparison`, non i plot `group` o `run`.
- `run_plot_configs_to_do`, `run_plot_groups_to_do`, `run_plot_instances_to_do` filtrano solo i plot `run`, non i plot `comparison` o `group`.
- Se imposti anche `experiment_group_comparison_output_subdir`, i PNG della comparazione filtrata vengono scritti in `results/plots/<subdir>/...`, cosi' puoi rigenerare confronti diversi senza sovrascrivere i plot aggregati generali.
- `experiment_group_comparison_row_split_priority` permette di spezzare tutte le figure aggregate su piu' righe. Esempi:
  - `['patient_number']`: una riga per ogni numerosita' pazienti
  - `['care_unit_number']`: una riga per ogni numerosita' care unit
  - `['patient_number', 'test']`: una riga per ogni combinazione `pazienti x test`, ordinate secondo quella priorita'
- Il plotter legge l'analisi centralizzata `results/analysis/` e filtra in memoria i dati necessari.
- Nei plot aggregati, `total_solving_time` usa `run_total_time_elapsed` quando disponibile in `instance_analysis.xlsx`; in caso contrario mantiene il fallback storico `master + subproblem`.
- I risultati legacy restano leggibili: se mancano i nuovi file di timing (`run_status.json` arricchito, `iteration_timing_stats.json`, `subproblem_day_<d>_stats.json`), analyzer e plotter fanno fallback ai dati storici e stampano un warning esplicito.

Significato dei plot disponibili:

Riferimento esteso con formule, assi e interpretazione:
- [docs/plot_reference.md](/home/marco/Universita/Dottorato/Studi/Progetto%20NCDs%20Agenda/Tesi%20Vancini/Scheduler-Project/docs/plot_reference.md)

| Plot | Modalità | Input richiesti | Output | Cosa rappresenta | Stato |
|---|---|---|---|---|---|
| `best_instance` | `all` | `master_instance.json` + `best_final_result_so_far.json` | `plots/best_result/final_result.png` | Vista compatta giorno/care unit del miglior risultato finale trovato. Ogni rettangolo rappresenta una richiesta schedulata; il colore identifica la care unit. | Implementato |
| `best_instance_subproblems` | `all` | `master_instance.json` + `best_final_result_so_far.json` | `plots/best_result/subproblem_day_<d>.png` | Gantt per ogni giorno del miglior risultato: asse `x` sui time slot, asse `y` sugli operatori, rettangoli pieni per richieste assegnate. | Implementato |
| `core_gantt` | `all` | file core per iterazione + `subproblem_day_<d>_result.json` | `plots/cores/iter_<k>/core_<i>.png` | Gantt del singolo core: i `components` del core sono disegnati sul calendario operatori del giorno, la `reason` e' mostrata in overlay semi-trasparente. | Implementato |
| `result_value_vs_time` | `all` | `analysis/master_result_analysis.csv` + `analysis/subproblem_result_analysis.csv` | `plots/result_value_vs_time.png` | Evoluzione del valore soluzione nel tempo cumulato: linea master, linea final/subproblem, linea cache se presente. Se disponibile usa il nuovo `iteration_tracked_elapsed_time`, altrimenti ripiega su `master + cache + subproblem`. | Implementato |
| `core_info` | `all` | `analysis/master_result_analysis.csv` | `plots/cores.png` | Evoluzione per iterazione delle proprieta' dei core: numero, dimensione, durata relativa e numero di care unit coinvolte, separate per tipo di core. | Implementato |
| `solving_times` | `all` | `analysis/master_result_analysis.csv` + `analysis/subproblem_result_analysis.csv` | `plots/solving_times.png` | Tempi per iterazione di master, cache, sottoproblemi e `other tracked`. `Other` usa `iteration_tracked_elapsed_time` quando disponibile e quindi include build, expansion, post-processing e altri passaggi intermedi tracciati; sulle analisi legacy ripiega su `master + cache + subproblem`. | Implementato |
| `solving_times_by_day` | `all` | `analysis/subproblem_result_analysis.csv` | `plots/solving_times_by_day.png` | Heatmap giorno x iterazione dei tempi di solve del sottoproblema; una `x` nera marca i giorni ancora con richieste rifiutate. | Implementato |
| `requests_per_patient` | `all` | `analysis/master_result_analysis.csv` | `plots/requests_per_patient.png` | Pannello 2x2 sull'evoluzione delle richieste per paziente e delle risorse usate per paziente, confrontando master e final lungo le iterazioni. | Implementato, ma le metriche sottostanti ereditano i naming talvolta fuorvianti dei KPI analyzer |
| `equal_requests_between_iterations` | `all` | `master_result.json` + `final_result.json` per iterazione | `plots/equal_requests_between_iterations.png` | Stabilita' inter-iterazione: confronta il numero totale di richieste e quante richieste restano uguali rispetto all'iterazione precedente, per master e final. | Implementato |
| `aggregate_best_solution_value` | `all` | `analysis/master_result_analysis.csv` | previsto `plots/...` | Doveva aggregare i migliori valori soluzione su piu' istanze/configurazioni, ma il modulo oggi si ferma dopo una stampa intermedia e non salva il grafico. | Incompleto |
| `experiment_group_comparison` | `all` | `analysis/master_result_analysis.csv` + `analysis/subproblem_result_analysis.csv` | `results/plots/comparison_*.png`, `results/plots/<subdir>/comparison_*.png`, `results/plots/groups/<config>__<group>/*.png` | Famiglia aggregata articolata in plot `comparison` globali e plot `group` per singolo `(config, group)`: boxplot su iterazioni LBBD, core medi/iterazione, tempo totale, gap finale%, objective finale, ratio di durata/servizi soddisfatti; performance profile sul tempo totale tracciato con pannelli disposti per `patient_number x care_unit_number`; istogrammi su ottimi e tempi MP/SP; bubble plot riassuntivi; scatter aggregato del numero di core lungo l'avanzamento normalizzato delle iterazioni; figure per gruppo con curve per istanza del numero di core per iterazione e boxplot della distribuzione giornaliera dei core per iterazione. | Implementato |

Modalità `instance`:

- legge una singola cartella risultato `<config>__<group>__<instance>`
- richiede `master_instance.json`, `iter_<k>/master_result.json`, `iter_<k>/final_result.json`
- produce:
  - `master_result.png`
  - `final_result.png`
  - `subproblem_day_<d>.png`
- è pensata come modalità di debug visivo puntuale di una specifica iterazione

### 5.8 CLI master instance plotter (`master_instance_plotter.py`)

Non usa un file YAML: la configurazione è via CLI e tramite un flag globale nel codice.

Argomenti:

| Campo | Tipo | Significato |
|---|---|---|
| `-i`, `--input` | `Path` | Root delle istanze master (`<root>/<group>/inst_*.json`) |
| `--skip-existing` | flag | Salta la generazione dei PNG già presenti |

Flag globale nel codice:

| Nome | Tipo | Significato |
|---|---|---|
| `USE_GLOBAL_PLOT_SCALES` | `bool` | Condivide alcune scale tra istanze per rendere confrontabili i plot |

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
    ├── master_result_analysis.csv
    ├── subproblem_result_analysis.csv
    └── method_comparison_report.xlsx
```

Note pratiche:

- `instance_analysis.xlsx` contiene il riassunto finale per istanza. Qui trovi anche il `final_gap_*` globale della run:
  - per LBBD: ultimo upper bound del master vs miglior soluzione finale feasible trovata;
  - per monolitico: incumbent, bound e gap letti direttamente dal `solver_log.log` di Gurobi.
- `master_result_analysis.csv` contiene invece le metriche per iterazione LBBD, incluse le colonne `lbbd_final_gap_*` calcolate iterazione per iterazione.
- `subproblem_result_analysis.csv` contiene il dettaglio per giorno/sottoproblema. E' stato spostato in CSV per evitare il limite di righe dei fogli Excel sui dataset grandi.
- Con il solver aggiornato, `master_result_analysis.csv` include anche i nuovi campi da `iteration_timing_stats.json` (per esempio `iteration_tracked_elapsed_time`, `core_expansion_time`, `cache_model_build_time`, `core_constraint_add_time`, `cache_update_time`, `final_result_compose_time`, `final_result_postprocess_time`), mentre `subproblem_result_analysis.csv` importa i nuovi `subproblem_day_<d>_stats.json` (per esempio `subproblem_model_build_time`, `subproblem_postprocess_time`, `from_true_cache`).
- Se questi file non sono presenti per run storiche, l'analyzer lascia vuote le nuove colonne e stampa un warning di compatibilita' invece di fallire.
- `method_comparison_report.xlsx` e' prodotto dallo script [method_comparison_report.py](/home/marco/Universita/Dottorato/Studi/Progetto%20NCDs%20Agenda/Tesi%20Vancini/Scheduler-Project/method_comparison_report.py), che legge l'analisi centralizzata e costruisce un confronto statistico tra tutti i metodi presenti.
  - output principali:
    - `config_summary`: riepilogo globale per metodo
    - `group_summary`: riepilogo per gruppo e metodo
    - `pairwise_optimality`: confronti paired sull'ottimalita' con test esatto di McNemar
    - `pairwise_continuous_all` / `pairwise_continuous_opt`: confronti paired su tempi, gap e objective con paired Student t-test e sign test
    - `repeated_anova`: ANOVA a misure ripetute sulle istanze complete comuni a tutti i metodi
  - uso tipico:
    ```bash
    python method_comparison_report.py -i results_experiment
    ```
  - filtro opzionale su un sottoinsieme di metodi:
    ```bash
    python method_comparison_report.py -i results_experiment --configs pruned irreducible_pruned pruned_pat_expansion
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

### 6.6 Output master instance plotter

```text
<instances_root>/
└── plots_instances/
    ├── <group>/
    │   └── <instance>/
    │       ├── patient_windows_gantt.png
    │       ├── average_window_overlap_by_day.png
    │       ├── weighted_window_overlap_by_day.png
    │       └── spread_capacity_heatmap.png
    ├── instance_daily_median_window_overlap_distribution.png
    ├── instance_daily_weighted_window_overlap_distribution.png
    ├── instance_daily_average_spread_capacity_distribution.png
    ├── instance_request_count_distribution.png
    ├── instance_duration_weighted_request_count_distribution.png
    └── instance_same_service_overlapping_window_distribution.png
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
- `total_time_slots_requested`: somma delle durate dei servizi richiesti nell'istanza master.
  Formula: `sum service_duration(service_name) per ogni finestra/ richiesta del servizio`.
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
- `lbbd_final_gap_value`, `lbbd_final_gap_pct`: gap LBBD per iterazione, riportato in `master_result_analysis.csv`, tra upper bound del master dell'iterazione e valore feasible finale prodotto dai sottoproblemi nella stessa iterazione.
  Formula:
  `lbbd_final_gap_value = max(0, master_upper_bound - final_objective_value)`
  `lbbd_final_gap_pct = 100 * lbbd_final_gap_value / abs(final_objective_value)`
  Sono valorizzate solo se l'analyzer ha sia `master_upper_bound` dal log Gurobi, sia `final_objective_value` dal `final_result`.
- `lbbd_final_gap_over_operator_total_duration_ratio`, `lbbd_final_gap_over_operator_total_duration_pct`: normalizzazione dello stesso gap rispetto alla capacita' totale operatori dell'istanza master.
  Formula:
  `lbbd_final_gap_over_operator_total_duration_ratio = lbbd_final_gap_value / operator_total_duration`
  `lbbd_final_gap_over_operator_total_duration_pct = 100 * lbbd_final_gap_over_operator_total_duration_ratio`
  Questa normalizzazione e' utile per capire l'ordine di grandezza del gap rispetto alla capacita' totale. L'interpretazione come "slot mancanti" e' esatta quando le priorita' sono tutte `1` e il valore obiettivo coincide di fatto con una durata totale.
- `final_gap_source`, `final_gap_feasible_value`, `final_gap_upper_bound`, `final_gap_value`, `final_gap_pct`: sintesi finale globale per istanza, riportata in `instance_analysis.xlsx`.
  Casi:
  - LBBD: `final_gap_upper_bound` viene dall'ultimo `master_log.log`, `final_gap_feasible_value` dal `best_final_result_so_far.json`.
  - monolitico: `final_gap_upper_bound`, `final_gap_feasible_value` e `final_gap_pct` vengono dal `solver_log.log` top-level di Gurobi.
  Formula numerica comune:
  `final_gap_value = max(0, final_gap_upper_bound - final_gap_feasible_value)`
  Se disponibile, `final_gap_pct` e' quello del log Gurobi; altrimenti viene ricostruito come:
  `final_gap_pct = 100 * final_gap_value / abs(final_gap_feasible_value)`
- `final_gap_over_operator_total_duration_ratio`, `final_gap_over_operator_total_duration_pct`: stesso gap finale globale, normalizzato rispetto alla disponibilita' totale degli operatori dell'istanza.
  Formula:
  `final_gap_over_operator_total_duration_ratio = final_gap_value / operator_total_duration`
  `final_gap_over_operator_total_duration_pct = 100 * final_gap_over_operator_total_duration_ratio`
  `pct` significa semplicemente `percentage`, cioe' "espresso in percentuale".
- `run_status`, `run_stage`, `run_message`, `run_error_code`, `run_return_code`, `run_timestamp`, `run_stop_reason`, `run_stop_iteration`, `run_total_time_elapsed`, `run_last_master_status`: copia dei campi grezzi da `run_status.json`, utili per debug del worker/processo.
  Attenzione: `run_status = success` significa solo che il processo si e' concluso senza errore tecnico, non che l'istanza sia ottima.
- `run_wall_start_timestamp`, `run_wall_end_timestamp`, `run_wall_start_epoch`, `run_wall_end_epoch`, `run_wall_elapsed_seconds`, `run_wall_minus_tracked_elapsed_seconds`: nuovo wall-time completo end-start del worker isolato. Rappresenta il tempo reale esterno della run, mentre `run_total_time_elapsed` resta il totale interno tracciato dal solver.
- `run_total_*_time`: nuovi aggregati temporali per famiglia di passaggi della run, per esempio `run_total_master_model_build_time`, `run_total_subsumption_time`, `run_total_cache_model_build_time`, `run_total_subproblem_model_build_time`, `run_total_core_expansion_time`, `run_total_core_constraint_add_time`, `run_total_cache_update_time`, `run_total_final_result_compose_time`, `run_total_final_result_postprocess_time`, `run_total_cache_postprocess_time`, `run_total_true_cache_lookup_time`, oltre ai tempi gia' noti di solve/core generation. Sono valorizzati solo per run prodotte col solver aggiornato.
- `status`, `status_reason`: classificazione finale sintetica dell'esito dell'istanza in `instance_analysis.xlsx`.
  Valori tipici:
  - `optimal`: per LBBD, run uscita con uno stop di accettazione previsto dal solver/config, prima di `total_time_limit`, e senza `last_master_status = time_limit`
  - `optimality_uncertain_master_time_limit`: gap finale chiuso, ma ultimo master chiuso per time limit
  - `optimality_uncertain_total_time_limit`: stop di accettazione raggiunto, ma il tempo totale registrato arriva almeno a `total_time_limit`
  - `max_iteration_feasible` / `max_iteration_no_solution`: raggiunto il massimo numero di iterazioni
  - `total_time_limit_feasible` / `total_time_limit_no_solution`: raggiunto il limite temporale totale della LBBD
  - `failed_memory_limit`: worker terminato per memory limit
  - `failed_exception`: worker terminato per eccezione
  - `time_limit_feasible` / `time_limit_no_solution`: casi monolitici dal log del solver
  - `memory_limit_feasible` / `memory_limit_no_solution`: casi monolitici dal log del solver
  - `completed_nonoptimal`: run conclusa con soluzione ma gap finale non chiuso
  La classificazione LBBD usa in priorita' `run_stop_reason` e gli altri campi persistiti nel `run_status.json`; per run storiche prive di questi campi resta un fallback legacy basato su gap finale e `final_master_status`.
- `solver_status`, `solver_objective_value`, `solver_upper_bound`, `solver_gap`, `solver_time`, ...: colonne aggiuntive presenti in `instance_analysis.xlsx` per i run monolitici/single-pass, copiate dal `solver_log.log` top-level. Servono per distinguere chiaramente il dato del solver dal valore ricalcolato dall'analyzer sul `result.json`.
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
