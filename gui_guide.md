# GUI Guide - `control_panel.py`

## 0) Guida all uso della GUI (utente)

Questa sezione e orientata all uso pratico della GUI.
Se vuoi imparare a usare il pannello, parti da qui.
Le sezioni successive restano utili come riferimento tecnico.

### 0.1 Avvio rapido

1. Apri un terminale nella root del progetto.
2. Avvia la GUI con:
   `python control_panel.py`
3. Si apre la finestra `Scheduler Control Panel`.

### 0.2 Layout principale della finestra

Elementi principali:

- Menu sinistro:
  - `Generator`
  - `Solving`
  - `Analysis / Plot`
  - `Stop running command`
- Area centrale: pagina attiva (in base al menu selezionato).
- Pannello in basso `CLI Output`: mostra il comando lanciato e tutto l output testuale.
- Status bar in basso: stato sintetico (`Ready`, `Running command...`, errori, completato).

Comportamento importante:

- La GUI esegue un comando per volta.
- Se un comando e gia in esecuzione, un nuovo run viene bloccato finche non termina (o finche non premi `Stop running command`).

### 0.3 Come usare il Configuration Panel

Il `Configuration Panel` appare in piu pagine e serve per gestire file YAML.

Riga superiore:

- `Config file`: path del file YAML.
- `Browse`: seleziona un file YAML dal filesystem.
- `Load`: carica il YAML nel pannello.
- `Save`: salva su file il contenuto attuale.

Riga sezione:

- `Section`: scegli quale sezione del YAML editare (`root`, `base`, `groups.<nome>` quando presenti).
- `Apply Form -> YAML`: prende i valori della form e aggiorna il testo YAML.
- `Apply YAML -> Form`: parse del testo YAML e ricostruzione form.

Tab `Parameters`:

- Mostra i parametri in form editabile.
- I campi sono organizzati su piu colonne per gruppo logico.
- Ogni colonna e un box con titolo gruppo.
- Se i gruppi sono tanti o larghi, usa la scrollbar orizzontale.
- Se i campi sono molti, usa la scrollbar verticale.
- Tipi campo:
  - `Checkbutton` per boolean
  - `Entry` per numeri/stringhe
  - per `list/dict` si usa YAML inline (esempio: `[1, 2, 3]`, `{a: 1}`)

Tab `Raw YAML`:

- Permette modifica diretta del YAML completo.
- Utile per cambi massivi o copia/incolla di blocchi.

Sequenza consigliata quando modifichi config:

1. `Browse` + `Load`.
2. Modifica da `Parameters` o `Raw YAML`.
3. Premi `Apply ...` se vuoi sincronizzare manualmente la vista opposta.
4. Premi `Save`.

### 0.4 Workflow tipico: generazione istanze

Pagina `Generator`:

- `Master preset`: imposta config `configs/master_generator_config.yaml`.
- `Subproblem preset`: imposta config `configs/subproblem_generator_config.yaml`.
- `Config file`: YAML del generatore.
- `Output dir`: cartella di output istanze.
- `Overwrite existing output`: abilita sovrascrittura.
- `Run generator`: avvia `generator.py -c <config> -o <output> [--overwrite]`.
- `Open output`: apre la cartella output nel file manager.

Passi consigliati:

1. Scegli preset o file config manuale.
2. Carica e controlla i parametri nel `Configuration Panel`.
3. Imposta output.
4. Avvia `Run generator`.
5. Verifica log nel `CLI Output`.

### 0.5 Workflow tipico: solving

Pagina `Solving`, con due tab:

- `Iterative` (script `solver.py`)
- `Single-pass` (script `single_pass_solver.py`)

Controlli principali (uguali in entrambe le tab):

- `Config file`
- `Input dir`
- `Output dir`
- `Overwrite existing output`
- `Run <script>`
- `Open output`
- `Configuration Panel` per edit YAML solver

Comando lanciato:

- `python <script> -c <config> -i <input_dir> -o <output_dir> [--overwrite]`

### 0.6 Workflow tipico: analisi e plot

Pagina `Analysis / Plot`, con tre tab:

1. `Analyzer`
2. `Plotter`
3. `Browse results`

Tab `Analyzer`:

- `Config file` + `Browse`
- `Results input` + `Browse`
- `Run analyzer`: avvia `analyzer.py -c <config> -i <results_input>`
- `Open analysis folder`: apre `<results_input>/analysis`
- `Configuration Panel` per config analyzer

Tab `Plotter`:

- Sezione all results:
  - `Config file`
  - `Results input`
  - `Run plotter all`: avvia `plotter.py all -c <config> -i <results_input>`
- Sezione single instance:
  - `Single result dir`
  - `Single output dir`
  - `Iteration`
  - `Run plotter instance`: avvia `plotter.py instance -i <single_result_dir> -o <single_output_dir> --iter <N>`
- `Configuration Panel` per config plotter (parte `all`).

Tab `Browse results`:

- `Results root`: radice risultati (default `results`)
- `Refresh`: rilegge struttura cartelle
- Filtri:
  - `Config`
  - `Group`
  - `Instance`
  - `File type` (`Plots (.png)`, `Analysis (.xlsx)`, `JSON results`, `Logs (.log)`, `All files`)
- Tabella file trovati
- `Open selected`: apre il file selezionato
- `Open parent folder`: apre la cartella contenitore del file
- doppio click su riga file: equivale a `Open selected`

Nota sulla navigazione risultati:

- Il browser si aspetta directory risultato con nome nel formato:
  `<config>__<group>__<instance>`
- In `Analysis (.xlsx)` legge da `<results_root>/analysis`.

### 0.7 Pannello CLI Output e stato

Nel box `CLI Output`:

- viene mostrato il comando completo preceduto da `$`
- segue lo stream stdout/stderr in tempo reale
- a fine esecuzione appare `[PROCESS EXIT CODE X]`

Controlli:

- `Clear`: pulisce solo il testo del terminale GUI
- `Stop running command` (menu sinistro): invia richiesta di terminazione del processo

Interpretazione rapida:

- exit code `0`: esecuzione completata correttamente
- exit code diverso da `0`: errore nello script o nei parametri/path

### 0.8 Regole pratiche su path e apertura file

- I path relativi vengono risolti rispetto alla root progetto.
- `Browse` inserisce, quando possibile, path relativo alla root (piu portabile).
- I pulsanti `Open ...` usano il file manager del sistema (`xdg-open` su Linux).
- Se il path non esiste, la GUI mostra un messaggio di errore.

## 1) Scope

Questo documento descrive in dettaglio la GUI implementata in `control_panel.py`, con focus su:

- architettura generale
- responsabilita di ogni classe
- descrizione dettagliata di ogni funzione/metodo
- flusso di esecuzione dei comandi CLI
- punti di estensione

La GUI e basata su `tkinter` + `ttk` e funge da pannello di controllo per:

- generazione istanze
- solving (iterative + single-pass)
- analisi e plotting
- esplorazione risultati


## 2) Architettura ad alto livello

Il file e organizzato in:

1. helper globale
2. widget/componenti di pagina
3. applicazione principale (`ControlPanelApp`)

Gerarchia principale:

- `ControlPanelApp`
  - page `GeneratorPage`
    - `ConfigEditor`
  - page `SolvingPage`
    - tab `SolverRunPanel` (Iterative)
      - `ConfigEditor`
    - tab `SolverRunPanel` (Single-pass)
      - `ConfigEditor`
  - page `AnalysisPlotPage`
    - tab Analyzer
      - `ConfigEditor`
    - tab Plotter
      - `ConfigEditor`
    - tab Browse results
      - `ResultsBrowser`


## 3) Helper globale

## `dump_inline_yaml(value) -> str`

Scopo:

- serializzare un valore python in YAML inline (flow style), utile per mostrare `list`/`dict` in una singola `Entry`.

Comportamento:

- usa `yaml.safe_dump(..., default_flow_style=True, sort_keys=False, allow_unicode=False)`
- rimuove whitespace finale con `.strip()`
- se il risultato e vuoto ritorna `"null"`

Uso principale:

- form editor in `ConfigEditor._build_form()`


## 4) Classe `ConfigEditor`

`ConfigEditor` e un widget riusabile per editare file YAML con doppia vista:

- vista form parametrica
- vista raw YAML

Tiene sincronizzati i due mondi e permette load/save.

### `__init__(...)`

Responsabilita:

- costruire layout base del pannello
- agganciare `path_var` esterno per il file config
- inizializzare stato interno:
  - `config_data`
  - `current_section_path`
  - `field_specs`
- creare:
  - riga path file + bottoni `Browse/Load/Save`
  - selettore sezione
  - notebook con tab `Parameters` e `Raw YAML`
  - area form scrollabile via `Canvas + Frame + Scrollbar`

### `_on_form_configure(self, _event=None)`

Aggiorna la `scrollregion` del canvas quando cambia la dimensione della form.

### `_on_canvas_configure(self, event=None)`

Ridimensiona la finestra interna del canvas per seguire la larghezza disponibile.

### `_browse_file(self)`

Apre file picker per yaml e aggiorna `path_var`.

### `_resolve_path(self, path_str: str) -> Path`

Risolve path relativo rispetto a `project_root`; espande `~`.

### `_path_to_string(self, path: Path) -> str`

Converte un `Path` in stringa preferendo il formato relativo al progetto, se possibile.

### `load_file(self)`

Pipeline:

1. valida path
2. carica e parse YAML
3. verifica top-level `dict`
4. aggiorna stato:
   - `current_file`
   - `config_data`
5. refresh UI:
   - `_refresh_sections()`
   - `_populate_raw_text()`
   - `_build_form()`
6. notifica status

Error handling:

- warning su path mancante
- error su file inesistente
- error su parse yaml o formato non supportato

### `save_file(self)`

Pipeline:

1. valida path
2. determina tab attivo
3. sincronizza dati:
   - se tab raw: `apply_yaml_text(show_success=False)`
   - se tab form: `apply_form(show_success=False)`
4. salva `config_data` su disco via `yaml.safe_dump(sort_keys=False)`
5. aggiorna status + messagebox di conferma

### `_refresh_sections(self)`

Costruisce le sezioni selezionabili nel combobox:

- `root`
- `base` (se presente e dict)
- `groups.<nome>` per ogni gruppo dict

Gestisce anche fallback della sezione corrente se non piu valida.

### `_populate_raw_text(self)`

Scrive `config_data` nel tab raw YAML.

### `_on_section_change(self, _event=None)`

Aggiorna `current_section_path` e ricostruisce la form.

### `_get_section_obj(self)`

Risoluzione dinamica dell oggetto corrente a partire da `current_section_path`.

Esempio:

- `"root"` -> `config_data`
- `"groups.test"` -> `config_data["groups"]["test"]`

### `_flatten_section(self, obj, prefix="") -> list[tuple[str, object]]`

Trasforma un dict annidato in lista piatta di coppie:

- `("a.b.c", value)`

Serve per creare la form riga-per-riga.

### `_set_value_by_path(self, section: dict, dotted_path: str, value)`

Setta un valore in struttura dict annidata creando i nodi intermedi mancanti.

### `_build_form(self)`

Ricostruisce completamente la tab `Parameters`:

1. pulisce widget precedenti
2. recupera sezione corrente
3. flatten campi
4. per ogni campo crea widget in base al tipo:
   - `bool` -> `Checkbutton`
   - altri tipi -> `Entry`
5. per `list/dict` mostra YAML inline
6. aggiorna `field_specs` con metadata di parsing

### `_parse_form_value(self, path: str, spec: dict)`

Converte il testo form in tipo corretto:

- `int`, `float`, `str`, `list`, `dict`, `None`

Validazioni:

- per `list` e `dict` parse YAML + check tipo
- per campi `None` accetta `""`, `null`, `None`, `~`

### `apply_form(self, show_success=True) -> bool`

Applica valori della form al modello YAML:

1. parse ogni campo via `_parse_form_value`
2. set nel dict via `_set_value_by_path`
3. aggiorna tab raw con `_populate_raw_text`

Ritorna:

- `True` su successo
- `False` su errore

### `apply_yaml_text(self, show_success=True) -> bool`

Applica testo raw YAML alla form:

1. parse raw
2. verifica top-level dict
3. aggiorna `config_data`
4. refresh sezioni e form

Ritorna:

- `True` su successo
- `False` su errore


## 5) Classe `GeneratorPage`

Pagina dedicata a `generator.py`.

### `__init__(self, parent, app)`

Costruisce:

- controlli run:
  - config
  - output
  - flag overwrite
- quick preset:
  - master config
  - subproblem config
- bottoni:
  - run generator
  - open output
- un `ConfigEditor` collegato al file config scelto

### `_browse_config(self)`

Apre file picker config YAML.

### `_browse_output(self)`

Apre directory picker output.

### `_run(self)`

Compone comando:

- `python generator.py -c <config> -o <output> [--overwrite]`

e lo invia a `app.start_command(...)`.

### `_open_output(self)`

Apre la directory output nel file manager.


## 6) Classe `SolverRunPanel`

Pannello riusabile per script solver con stessa interfaccia:

- config
- input
- output
- overwrite
- config editor

Usato due volte in `SolvingPage`:

- `solver.py`
- `single_pass_solver.py`

### `__init__(...)`

Costruisce layout standard e `ConfigEditor` interno.

### `_run(self)`

Compone comando:

- `python <script_name> -c <config> -i <input> -o <output> [--overwrite]`

e lo avvia via `app.start_command`.


## 7) Classe `SolvingPage`

Contenitore con notebook a due tab:

- `Iterative` -> `SolverRunPanel(..., script_name="solver.py", ...)`
- `Single-pass` -> `SolverRunPanel(..., script_name="single_pass_solver.py", ...)`

### `__init__(self, parent, app)`

Inizializza notebook e i due pannelli solver.


## 8) Classe `ResultsBrowser`

Widget per navigare output risultati e aprire rapidamente file.

Filtri principali:

- config
- group
- instance
- tipo file

### `__init__(self, parent, app)`

Costruisce:

- selector root risultati
- filtri combobox
- treeview file
- azioni open
- label conteggio file

Stato:

- `result_dirs`: tuple `(config, group, instance, path)`
- `file_index`: mappa `tree_iid -> Path`

### `_results_root(self) -> Path`

Ritorna root risultati risolto rispetto al progetto.

### `refresh(self)`

Scansiona root risultati:

- prende solo directory con naming `<config>__<group>__<instance>`
- popola valori filtri
- resetta selezioni su `All`
- aggiorna lista file

### `_on_filter_change(self)`

Aggiorna dinamicamente il filtro `Instance` in base a `Config` e `Group`, poi refresh lista file.

### `_selected_dirs(self) -> list[Path]`

Ritorna le directory risultato coerenti con i filtri correnti.

### `_refresh_file_list(self)`

Crea lista file visualizzati in base al tipo selezionato:

- `Plots (.png)` -> `plots/**/*.png`
- `Analysis (.xlsx)` -> `analysis/*.xlsx`
- `JSON results` -> `*.json` + `iter_*/*.json`
- `Logs (.log)` -> `*.log` + `iter_*/*.log`
- `All files` -> tutto ricorsivo + analysis

Deduplica e ordina, poi popola treeview.

### `_selected_path(self) -> Path | None`

Ritorna path dell elemento selezionato in treeview.

### `open_selected(self)`

Apre il file selezionato.

### `open_selected_parent(self)`

Apre la cartella parent del file selezionato.


## 9) Classe `AnalysisPlotPage`

Pagina con tre tab:

- Analyzer
- Plotter
- Browse results

### `__init__(self, parent, app)`

Crea notebook e delega la costruzione tab ai metodi helper.

### `_build_analyzer_tab(self, parent)`

Costruisce pannello per:

- selezionare config analyzer
- selezionare input risultati
- eseguire analyzer
- aprire cartella `analysis`
- editare config via `ConfigEditor`

### `_build_plotter_tab(self, parent)`

Costruisce pannello per:

- run `plotter.py all`
- run `plotter.py instance` con:
  - input result dir
  - output dir
  - indice iterazione
- edit config plotter via `ConfigEditor`

### `_run_analyzer(self)`

Avvia:

- `python analyzer.py -c <config> -i <results>`

### `_open_analysis_folder(self)`

Apre `<results>/analysis`.

### `_run_plotter_all(self)`

Avvia:

- `python plotter.py all -c <config> -i <results>`

### `_run_plotter_instance(self)`

Avvia:

- `python plotter.py instance -i <result_dir> -o <out> --iter <n>`


## 10) Classe `ControlPanelApp`

Classe root dell applicazione.

Responsabilita principali:

- layout complessivo
- navigazione pagine
- status bar
- terminal output
- lifecycle processo esterno
- utility path/file dialog/open

### `__init__(self)`

Inizializza:

- `project_root`
- dimensioni finestra
- stato processo (`process`, `output_thread`, `output_queue`)
- layout completo via `_build_layout()`
- pagina iniziale `Generator`

### `_build_layout(self)`

Costruisce shell UI:

- `PanedWindow` principale sinistra/destra
- nav laterale con bottoni pagina + stop command
- container pagine
- terminale output con bottone clear
- status bar in fondo

Istanzia e registra le pagine:

- `GeneratorPage`
- `SolvingPage`
- `AnalysisPlotPage`

### `_show_page(self, page_name: str)`

Mostra pagina richiesta (`tkraise`) e aggiorna status.

### `set_status(self, message: str)`

Aggiorna testo status bar.

### `append_output(self, text: str)`

Aggiunge testo al terminale e fa auto-scroll.

### `clear_output(self)`

Pulisce area terminale.

### `resolve_path(self, path_str: str) -> Path`

Path utility:

- espande `~`
- risolve path relativi rispetto a `project_root`

### `browse_file(self, target_var, filetypes)`

Dialog selezione file e update variabile target.

### `browse_directory(self, target_var)`

Dialog selezione directory e update variabile target.

### `open_path(self, path: Path)`

Apertura file/cartella OS-specific:

- Linux: `xdg-open`
- macOS: `open`
- Windows: `os.startfile`

### `_format_command(self, cmd: list[str]) -> str`

Formatta comando shell-safe per visualizzazione in terminale GUI.

### `start_command(self, cmd: list[str])`

Core runtime method:

1. impedisce run concorrenti
2. avvia subprocess (`stdout` + `stderr` unificati)
3. mostra comando in terminale
4. lancia thread di lettura `_read_output`
5. avvia polling non bloccante `_poll_output`

### `_read_output(self)`

Thread worker:

- legge linee da `stdout`
- pusha eventi in coda:
  - `("out", line)`
  - `("done", return_code)`

### `_poll_output(self)`

Loop GUI (con `after`) che drena coda:

- stampa output
- gestisce fine processo
- aggiorna status in base all exit code

### `stop_command(self)`

Se c e processo attivo:

- invia `terminate()`
- aggiorna status
- logga evento nel terminale


## 11) Entry point

Blocco finale:

- crea `ControlPanelApp()`
- avvia `mainloop()`

```python
if __name__ == "__main__":
    app = ControlPanelApp()
    app.mainloop()
```


## 12) Flusso dati principale

Configurazioni:

1. user modifica campi form in `ConfigEditor`
2. `apply_form` converte e aggiorna `config_data`
3. `save_file` persiste YAML
4. run button costruisce comando con path correnti

Esecuzione script:

1. `start_command` avvia subprocess
2. `_read_output` gira su thread separato
3. `_poll_output` aggiorna terminale in thread GUI
4. su fine processo viene mostrato exit code

Browsing risultati:

1. `ResultsBrowser.refresh` indicizza directory output
2. filtri riducono sottoinsieme
3. `_refresh_file_list` materializza elenco file
4. open file/cartella via `ControlPanelApp.open_path`


## 13) Note UX e limiti attuali

- un solo comando alla volta (scelta intenzionale per chiarezza stato)
- editor form lavora su campi scalari/list/dict flattenati (non supporta editing strutturale profondo tipo aggiunta/rimozione chiavi complesse con wizard dedicato)
- parser output e generico (stampa testo raw)
- apertura file dipende da tool di sistema (`xdg-open`/`open`/`startfile`)


## 14) Punti di estensione consigliati

1. history dei comandi lanciati con rerun rapido
2. validazione schema YAML per ogni tipo config
3. progress bar semantica (es. iterazioni solver) oltre al log testuale
4. export/import preset UI
5. thumbnail gallery per plot PNG dentro GUI
