# Plot Reference

Riferimento compatto dei plot disponibili nel progetto.

Obiettivo del file:
- descrivere il significato di ogni plot;
- indicare quali valori sono mostrati su assi, colori e dimensioni delle bolle;
- riportare le formule effettivamente usate dal codice;
- fornire una lettura interpretativa rapida.

## Convenzioni

- `MP` = master problem.
- `SP` = subproblem.
- `iter k` = iterazione LBBD `k`.
- `day d` = giorno `d`.
- `inst` = singola istanza.
- `pair = (test, group)` = combinazione `config/test` + gruppo istanze.
- `|X|` = cardinalita' dell'insieme/lista `X`.
- `mean(X)` = media aritmetica.
- `median(X)` = mediana.

## Result Plots (`plotter.py`)

### `best_instance`
- Output: `results/<config>__<group>__<instance>/plots/best_result/final_result.png`
- Tipo: vista compatta giorno x care unit.
- Asse `X`: giorni.
- Asse `Y`: slot di durata cumulata nella care unit del giorno.
- Rettangoli: richieste schedulate nella miglior soluzione finale trovata.
- Colore: care unit del servizio.
- Formula visualizzata:
  - altezza rettangolo = `duration(service)`
  - altezza totale disponibile della colonna = `sum(operator.duration)` nella care unit/giorno
- Interpretazione:
  - mostra quanto "riempimento" di durata ottieni in ciascuna care unit per giorno;
  - non mostra l'ordine temporale interno degli operatori, solo volume aggregato.

### `best_instance_subproblems`
- Output: `results/<config>__<group>__<instance>/plots/best_result/subproblem_day_<d>.png`
- Tipo: Gantt del sottoproblema giornaliero.
- Asse `X`: time slot.
- Asse `Y`: operatori.
- Rettangoli pieni: richieste schedulate.
- Rettangoli semitrasparenti in coda: richieste rifiutate mostrate dopo l'ultimo slot occupato dell'operatore scelto.
- Colore: care unit del servizio.
- Formula visualizzata:
  - larghezza rettangolo = `duration(service)`
  - posizione `x` per schedulati = `request.time_slot`
- Interpretazione:
  - permette di vedere se la soluzione giornaliera usa bene gli operatori;
  - le richieste rifiutate sono un promemoria visivo di cio' che non entra nel giorno.

### `core_gantt`
- Output: `results/<config>__<group>__<instance>/plots/cores/iter_<k>/core_<i>.png`
- Tipo: Gantt di un singolo core.
- Asse `X`: time slot.
- Asse `Y`: operatori.
- Rettangoli pieni: `components` del core trovati nella soluzione del sottoproblema.
- Rettangoli semitrasparenti: `reason` del core.
- Formula visualizzata:
  - larghezza rettangolo = `duration(service)`
  - i `components` sono disegnati dove sono effettivamente schedulati nel risultato SP;
  - la `reason` e' appoggiata in coda su un operatore compatibile.
- Interpretazione:
  - serve per capire "che cosa vieta" il taglio;
  - un core piccolo e localizzato contiene informazione piu' precisa.

### `result_value_vs_time`
- Output: `results/<config>__<group>__<instance>/plots/result_value_vs_time.png`
- Tipo: line plot temporale.
- Asse `X`: tempo cumulato di solve.
- Asse `Y`: valore obiettivo/risultato.
- Curve:
  - `master`: `master_objective_value`
  - `cache`: `cache_objective_value` se presente
  - `subproblem`: `final_objective_value`
- Formula ascissa:
  - ogni punto e' posizionato al tempo cumulato raggiunto dopo la chiusura della fase corrispondente dell'iterazione.
- Interpretazione:
  - confronta l'ottimismo del master con il valore feasible finale;
  - utile per capire se la cache o i sottoproblemi migliorano rapidamente la qualita' della soluzione.

### `core_info`
- Output: `results/<config>__<group>__<instance>/plots/cores.png`
- Tipo: pannello 2x2 per iterazione, separato per tipo di core.
- Assi:
  - in tutti i pannelli, `X = iteration`
- Pannelli:
  - alto-sinistra: `average_core_size` con banda `[min_core_size, max_core_size]`
  - basso-sinistra: `core_number`
  - alto-destra: `average_total_duration_per_core / master_average_scheduled_request_duration_per_day`
    con banda tra min e max della stessa quantita'
  - basso-destra: `average_care_unit_number_per_core` con banda `[min, max]`
- Formule:
  - `core_size = |core.components|`
  - `core_number = |cores_iter|`
  - `relative_duration = total_core_duration / master_average_scheduled_request_duration_per_day`
  - `care_unit_number_per_core = numero di care unit distinte toccate dal core`
- Interpretazione:
  - dimensione piccola + numero alto = tagli molto dettagliati e numerosi;
  - durata relativa alta = core "pesanti", piu' vicini a coprire una grossa parte del giorno.

### `solving_times`
- Output: `results/<config>__<group>__<instance>/plots/solving_times.png`
- Tipo: line plot con banda/variabilita' dei sottoproblemi.
- Asse `X`: iterazione.
- Asse `Y`: secondi.
- Curve:
  - `master`: `master_time`
  - `cache`: `cache_time`
  - `total subproblem`: `sum_d subproblem_time(iter, d)`
  - `other tracked`: `max(0, iteration_tracked_elapsed_time - master_time - cache_time - SP_total)`
  - `subproblems` arancione: media per iterazione dei tempi dei singoli sottoproblemi, con errore derivato dai min/max giornalieri
- Formule:
  - `SP_total(k) = sum_d time(k, d)`
  - `SP_mean(k) = mean_d time(k, d)`
  - `Other(k) = max(0, iteration_tracked_elapsed_time(k) - master_time(k) - cache_time(k) - SP_total(k))`
- Interpretazione:
  - distingue il costo complessivo della fase SP dal costo medio di un singolo giorno;
  - `other tracked` raccoglie il resto dei passaggi intermedi tracciati nell'iterazione, inclusi per esempio build dei modelli, expansion dei core, post-processing, aggiunta dei vincoli e aggiornamento cache;
  - se `SP_total` cresce ma `SP_mean` resta basso, il costo e' dovuto soprattutto al numero di giorni/istanze risolti.
  - Su analisi legacy prive di `iteration_timing_stats.json`, il plotter emette un warning e ripiega di fatto sul profilo storico `master + cache + subproblem`.

### `solving_times_by_day`
- Output: `results/<config>__<group>__<instance>/plots/solving_times_by_day.png`
- Tipo: heatmap giorno x iterazione.
- Asse `X`: iterazione.
- Asse `Y`: giorno.
- Colore: `time(iter, day)`.
- Marker `x` neri: giorni con `rejected_request_number > 0`.
- Interpretazione:
  - fa emergere giorni sistematicamente difficili;
  - se le `x` nere si concentrano sempre sugli stessi giorni, il collo di bottiglia e' strutturale.

### `requests_per_patient`
- Output: `results/<config>__<group>__<instance>/plots/requests_per_patient.png`
- Tipo: pannello 2x2.
- Asse `X`: iterazione.
- Pannelli:
  - alto-sinistra: richieste/paziente same-day nel master
  - alto-destra: richieste/paziente same-day nel final result
  - basso-sinistra: risorse usate/paziente same-day nel master
  - basso-destra: risorse usate/paziente same-day nel final result
- Linea centrale: media.
- Banda: intervallo `[min, max]`.
- Significato delle metriche:
  - `request_number_per_patient_same_day`: numero di richieste assegnate a un paziente in un giorno
  - `care_unit_used_per_patient_same_day`: numero di care unit distinte usate dal paziente nello stesso giorno
- Interpretazione:
  - utile per capire concentrazione di attivita' sul singolo paziente;
  - aiuta a leggere quanto il final result "scompatta" o conferma il master.

### `equal_requests_between_iterations`
- Output: `results/<config>__<group>__<instance>/plots/equal_requests_between_iterations.png`
- Tipo: line plot di stabilita' iterativa.
- Asse `X`: iterazione.
- Asse `Y`: numero richieste.
- Curve:
  - `master requests`
  - `equal master requests`
  - `subproblem requests`
  - `equal subproblem requests`
- Formula:
  - una richiesta dell'iterazione `k` e' "equal" se compare anche in `k-1` con stesso paziente, servizio e, nel caso fat, stesso operatore.
- Interpretazione:
  - misura quanto la soluzione si stabilizza fra iterazioni consecutive;
  - se le curve "equal" si avvicinano al totale, la LBBD sta cambiando poco.

### `aggregate_best_solution_value`
- Stato: incompleto.
- Il modulo oggi filtra, stampa un dataframe intermedio e termina senza generare un PNG affidabile.
- Interpretazione:
  - non usarlo come riferimento documentale finche' non viene completato.

## Aggregate Experiment Comparison (`experiment_group_comparison`)

Tutti i plot in questa famiglia usano gruppi `pair = (test, group)`.

Livelli di output della famiglia:
- `comparison`: PNG in `results/plots/` oppure `results/plots/<subdir>/` se e' attivo `experiment_group_comparison_output_subdir`
- `group`: PNG in `results/plots/groups/<config>__<group>/`

Nota di configurazione:
- schema canonico nuovo:
  - `plots_to_do.comparison = [...]`
  - `plots_to_do.group = [...]`
- il vecchio formato piatto `plots_to_do: ['experiment_group_comparison', ...]` e' ancora accettato in lettura;
- nel formato legacy, `experiment_group_comparison` abilita tutti i plot `comparison` e `group` della famiglia.

Notazione comune per le metriche per istanza:
- `iteration_count = numero di iterazioni LBBD presenti per l'istanza`
- `avg_cores_per_iteration = mean_k core_number(k)`
- `total_master_time = sum_k master_time(k)`
- `total_subproblem_time = sum_{k,d} subproblem_time(k,d)`
- `total_solving_time = run_total_time_elapsed` se disponibile in `instance_analysis.xlsx`, altrimenti fallback a `total_master_time + total_subproblem_time`
- `total_other_tracked_time = max(0, total_solving_time - total_master_time - total_subproblem_time)`
- `wall_elapsed_seconds = run_wall_elapsed_seconds` se disponibile in `instance_analysis.xlsx`; resta separato da `total_solving_time` perche' quest'ultimo continua a essere il totale interno tracciato e scomponibile nei plot stacked MP/SP/Other.
- `final_gap_pct = 100 * max(0, master_upper_bound_last - final_objective_value_last) / abs(final_objective_value_last)`
- `scheduled_duration_over_capacity_ratio = final_total_scheduled_request_duration / (final_total_scheduled_request_duration + final_total_time_slots_remaining)`
- `scheduled_number_over_total_ratio = final_total_scheduled_request_number / (final_total_scheduled_request_number + final_total_rejected_request_number)`
- `timeout_feasible_nonoptimal_iteration_count = numero di iterazioni con master_status == time_limit e master_gap > tolleranza`
- `mean_master_gap_over_iterations = mean_k master_gap(k)`
- `total_core_count(k) = numero di core generati nell'iterazione k`
- `day_core_count(k, d) = numero di core dell'iterazione k associati al sottoproblema giornaliero d`
- `iteration_progress(k) = 1.0 se l'istanza ha una sola iterazione, altrimenti position(k) / (iteration_count - 1)`

Nota su `final_gap_pct`:
- `master_upper_bound_last` = upper bound letto dall'ultimo `master_log.log` disponibile della run;
- `final_objective_value_last` = valore del miglior `best_final_result_so_far.json` disponibile nella run;
- quindi i due valori non sono necessariamente una coppia "coerente" della stessa iterazione finale: l'UB e' l'ultimo del master, il feasible e' il miglior feasible globale trovato fino a quel momento.

### `comparison_box_lbbd_iterations.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze del `pair`, di `iteration_count`.
- Interpretazione:
  - confronta quante iterazioni richiedono in media/metrica robusta i diversi test.

### `comparison_box_avg_cores_per_iteration.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `avg_cores_per_iteration`.
- Interpretazione:
  - misura quanti tagli vengono generati mediamente a iterazione.

### `comparison_box_total_solving_time.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `total_solving_time`.
- Interpretazione:
  - confronta il tempo totale tracciato della LBBD;
  - quando disponibile coincide con `run_total_time_elapsed`, quindi include anche il resto dei processamenti tracciati oltre a MP e SP.

### `comparison_performance_profile_total_time.png`
- Tipo: figura aggregata a griglia di performance profile.
- Layout:
  - una riga per ogni `patient_number`;
  - una colonna per ogni `care_unit_number`;
  - ogni pannello rappresenta un `group` e confronta tutti i metodi disponibili.
- Asse `X`:
  - performance factor `tau >= 1`;
  - mostrato su scala logaritmica per rendere piu' leggibili le differenze vicino a `tau = 1`.
- Asse `Y`:
  - quota cumulata di istanze del gruppo per cui il metodo e' entro un fattore `tau` dal miglior metodo sull'istanza.
- Tempo usato:
  - `total_solving_time` dell'istanza.
- Successo:
  - una run contribuisce col proprio tempo solo se `status == "optimal"`;
  - le run non ottimali o senza tempo disponibile vengono trattate come fallimenti e non fanno crescere la curva.
- Formula:
  - per ogni istanza `p` del gruppo e metodo `m`:
    - `t(p, m) = total_solving_time(p, m)` se la run e' ottimale, altrimenti `+inf`
  - per ogni istanza risolta da almeno un metodo:
    - `t_best(p) = min_m t(p, m)`
    - `r(p, m) = t(p, m) / t_best(p)`
  - performance profile del metodo `m`:
    - `rho_m(tau) = |{p : r(p, m) <= tau}| / |P_valid|`
- Interpretazione:
  - `Y = 1.0` significa: il metodo ha coperto tutte le `valid inst`, cioe' tutte le istanze del gruppo risolte all'ottimo da almeno un metodo;
  - valore alto gia' vicino a `tau = 1` = metodo spesso migliore o quasi migliore;
  - curva che cresce lentamente = metodo spesso piu' lento del migliore;
  - curva che non arriva a `1` = ci sono istanze del gruppo che il metodo non chiude all'ottimo mentre almeno un altro metodo si', quindi restano fallimenti rispetto all'insieme `P_valid`.

### `comparison_box_final_gap_pct.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `final_gap_pct`.
- Interpretazione:
  - confronta quanto resta aperto il gap finale `master upper bound vs final feasible del SP`.

### `comparison_box_final_objective_value.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `final_objective_value`.
- Interpretazione:
  - confronta la qualita' finale raggiunta dai test.

### `comparison_box_scheduled_duration_over_capacity_ratio.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `scheduled_duration_over_capacity_ratio`.
- Formula:
  - `scheduled_duration_over_capacity_ratio = scheduled_duration / total_capacity_duration`
- Interpretazione:
  - piu' e' vicino a `1.0`, piu' la soluzione satura la capacita' complessiva.

### `comparison_box_scheduled_services_ratio.png`
- Tipo: boxplot per `pair`.
- Valori nel box:
  - distribuzione, tra istanze, di `scheduled_number_over_total_ratio`.
- Formula:
  - `scheduled_number_over_total_ratio = scheduled_services / total_services`
- Interpretazione:
  - misura la quota di richieste effettivamente servite.

### `comparison_bubble_duration_ratio_summary.png`
- Tipo: bubble plot aggregato per `pair`.
- Asse `X`: categorie `(test, group)`.
- Asse `Y`: `mean_inst(scheduled_duration_over_capacity_ratio)`.
- Dimensione bolla:
  - scalata sui valori di `sum_inst(total_solving_time)`;
  - default corrente: scala lineare;
  - switchabile da codice tramite `BUBBLE_SIZE_SCALE_MODE` in `experiment_group_comparison.py`.
- Etichetta sopra:
  - `gap m = mean_inst(final_gap_pct)`
  - `iter m = mean_inst(iteration_count)`
- Etichetta sotto:
  - `T = sum_inst(total_solving_time)`
- Interpretazione:
  - bolla alta = buona saturazione della capacita';
  - bolla grande = tempo totale tracciato elevato;
  - utile per vedere trade-off qualita'/tempo.

### `comparison_bubble_core_count_vs_core_size.png`
- Tipo: bubble plot aggregato per `pair`.
- Asse `X`: categorie `(test, group)`.
- Asse `Y`: `median_inst(avg_cores_per_iteration)`.
- Dimensione bolla:
  - scalata sui valori di `median_inst(avg_core_size_share_per_iteration)`;
  - default corrente: scala lineare;
  - switchabile da codice tramite `BUBBLE_SIZE_SCALE_MODE` in `experiment_group_comparison.py`.
- Etichetta sopra:
  - `core/SP share m = median_inst(avg_core_size_share_per_iteration)`
  - `iter m = mean_inst(iteration_count)`
- Formula della share:
  - per ogni core `c`:
    - `share(c) = |components(c)| / total_request_number(SP(iter(c), day(c)))`
  - per ogni iterazione `k`:
        - `iteration_share(k) = mean_c share(c)`
  - per ogni istanza:
    - `avg_core_size_share_per_iteration = mean_k iteration_share(k)`
- Interpretazione:
  - share vicina a `1.0` = core quasi grande quanto l'intero insieme di SP request del giorno;
  - share piccola = core piu' selettivo, quindi in genere taglio piu' informativo/potente.

### `bubble_duration_ratio_group.png`
- Output: `results/plots/groups/<config>__<group>/bubble_duration_ratio_group.png`
- Tipo: bubble plot per istanze di uno stesso `(test, group)`.
- Asse `X`: istanza.
- Asse `Y`: `scheduled_duration_over_capacity_ratio` dell'istanza.
- Dimensione bolla:
  - scalata sui valori di `total_solving_time` dell'istanza;
  - default corrente: scala lineare;
  - switchabile da codice tramite `BUBBLE_SIZE_SCALE_MODE` in `experiment_group_comparison.py`.
- Etichette:
  - sopra: `gap` finale e `iteration_count`
  - sotto: tempo totale `T`
- Interpretazione:
  - stessa logica del bubble aggregato, ma disaggregata a livello di singola istanza.

### `core_generation_profile_group.png`
- Output: `results/plots/groups/<config>__<group>/core_generation_profile_group.png`
- Tipo: figura per un singolo `(test, group)`, salvata una volta sola nella cartella gruppo dedicata.
- Pannello alto:
  - una curva per ogni istanza del gruppo;
  - asse `X`: `iteration`;
  - asse `Y`: `total_core_count(k)`.
- Pannello basso:
  - boxplot per indice di iterazione;
  - per ogni box dell'iterazione `k`, i valori sono `day_core_count(k, d)` raccolti su tutti i sottoproblemi giornalieri `d` di tutte le istanze del gruppo;
  - i giorni senza core contribuiscono con valore `0` quando il sottoproblema giornaliero esiste.
- Compressione grafica per run lunghe:
  - se le iterazioni sono molte, il plot non mostra piu' ogni iterazione in modo esplicito;
  - mantiene sempre le iterazioni in cui il numero di core cambia;
  - aggiunge un campionamento periodico delle iterazioni stabili, per evitare figure eccessivamente larghe.
- Interpretazione:
  - pannello alto: mostra come evolve, istanza per istanza, l'intensita' di generazione dei core;
  - pannello basso: mostra quanto i core sono distribuiti o concentrati sui diversi sottoproblemi giornalieri della stessa iterazione.

### `comparison_scatter_core_generation_progress.png`
- Tipo: figura aggregata con uno scatter plot per `test`.
- Asse `X`: `iteration_progress(k)` in `[0, 1]`.
- Asse `Y`: `total_core_count(k)`.
- Punto:
  - un punto = una coppia `(istanza, iterazione)`;
  - colore e marker identificano il `group`.
- Filtro:
  - il plot include solo le istanze con `status == "optimal"` in `instance_analysis.xlsx`;
  - run concluse con timeout, max-iteration, memory-limit, exception o status incerto vengono escluse.
- Formule:
  - `total_core_count(k) = |cores(k)|`
  - `iteration_progress(k) = 1.0 se iteration_count = 1, altrimenti position(k) / (iteration_count - 1)`
- Interpretazione:
  - permette di vedere se i diversi test tendono a generare piu' core all'inizio, al centro o verso la fine della procedura;
  - il confronto tra gruppi tramite marker/colore rende leggibile se la dinamica dipende anche dalla dimensione/struttura delle istanze.

### `comparison_bar_optimal_count_with_mean_gap.png`
- Tipo: istogramma per `pair`.
- Altezza barra:
  - `count_inst(status == "optimal")`
- Etichetta sopra:
  - `gap m = mean_inst(final_gap_pct)`
- Interpretazione:
  - confronta quante istanze chiudono con status ottimale procedurale;
  - il gap medio annotato aiuta a leggere quanto sono "quasi ottime" le altre.

### `comparison_bar_master_vs_subproblem_total_time.png`
- Tipo: barre stacked per `pair`.
- Segmento blu:
  - `mean_inst(total_master_time)`
- Segmento arancione:
  - `mean_inst(total_subproblem_time)`
- Segmento grigio:
  - `mean_inst(total_other_tracked_time)`
- Interpretazione:
  - separa il tempo medio tracciato per istanza tra solve del master, solve dei sottoproblemi e resto dei processamenti tracciati;
  - il segmento grigio include, ad esempio, cache selection solve, build dei modelli, costruzione/expansion dei core, post-processing, aggiunta vincoli al master e aggiornamento cache, cioe' tutto cio' che contribuisce a `run_total_time_elapsed` ma non ai soli solve MP/SP;
  - su analisi legacy prive di `run_total_time_elapsed`, il plotter stampa un warning e ricade sul vecchio `master + subproblem`, quindi il segmento grigio puo' risultare nullo o sottostimato.

### `comparison_bar_master_vs_subproblem_time_share_pct.png`
- Tipo: barre stacked 100%.
- Segmento blu:
  - `100 * mean_inst(total_master_time) / mean_inst(total_solving_time)`
- Segmento arancione:
  - `100 * mean_inst(total_subproblem_time) / mean_inst(total_solving_time)`
- Segmento grigio:
  - `100 * mean_inst(total_other_tracked_time) / mean_inst(total_solving_time)`
- Interpretazione:
  - mostra la composizione percentuale del tempo totale tracciato tra MP, SP e resto dei processamenti tracciati.

### `comparison_master_bar_and_subproblem_iteration_box.png`
- Tipo: figura a due pannelli per riga.
- Pannello alto:
  - barra = `mean(master_time)` sulle righe di iterazione del `pair`
- Pannello basso:
  - boxplot della distribuzione di `mean_day time(iter, day)` per ciascun indice di iterazione, aggregata sulle istanze del `pair`
- Interpretazione:
  - alto: costo medio del master per iterazione;
  - basso: variabilita' del costo giornaliero medio dei sottoproblemi lungo le iterazioni.

### `comparison_master_timeout_feasible_and_mean_gap_boxplots.png`
- Tipo: figura a due boxplot.
- Pannello alto:
  - distribuzione, tra istanze, di `timeout_feasible_nonoptimal_iteration_count`
- Pannello basso:
  - distribuzione, tra istanze, di `mean_master_gap_over_iterations`
- Interpretazione:
  - quantifica quanto spesso il master chiude a `time_limit` con soluzione feasible ma non certificata;
  - il secondo pannello dice quanto era mediamente aperto il gap MIP del master.

### `comparison_master_same_day_request_grouping_boxplots.png`
- Tipo: figura a due boxplot.
- Pannello alto:
  - distribuzione, tra istanze, di `mean_multi_request_patients_per_iteration`
- Formula:
  - per ogni iterazione `k`, per ogni giorno `d`, si conta quante coppie `(patient, day)` hanno piu' di una richiesta assegnata nel master;
  - poi si fa la media sulle iterazioni.
- Pannello basso:
  - distribuzione, tra istanze, di `mean_patient_day_group_size`
- Formula:
  - si prendono tutte le cardinalita' dei gruppi `(patient, day)` di richieste assegnate nel master, includendo anche i singleton;
  - poi si calcola la media globale su tutte le iterazioni e tutti i giorni.
- Interpretazione:
  - alto: quanto spesso il master concentra piu' richieste sullo stesso paziente nello stesso giorno;
  - basso: quanto grandi sono mediamente questi gruppi patient-day.

## Master Instance Plots (`master_instance_plotter.py`)

### `patient_windows_gantt`
- Output: `plots_instances/<group>/<instance>/patient_windows_gantt.png`
- Tipo: Gantt delle finestre del master.
- Asse `X`: giorni.
- Asse `Y`: pazienti.
- Rettangolo:
  - una finestra richiesta `window = [start, end]`
  - larghezza = `end - start + 1`
- Colore: care unit del servizio.
- Interpretazione:
  - mostra densita', sovrapposizioni e dispersione temporale delle finestre richieste.

### `average_window_overlap_by_day`
- Output: `plots_instances/<group>/<instance>/average_window_overlap_by_day.png`
- Tipo: boxplot per giorno.
- Asse `X`: giorno.
- Asse `Y`: numero di finestre attive per paziente.
- Valori del box nel giorno `d`:
  - per ogni paziente `p`:
    - `active_windows(p, d) = count(window contains d)`
- Interpretazione:
  - quantifica quante richieste "cadono" sullo stesso giorno per un paziente.

### `weighted_window_overlap_by_day`
- Output: `plots_instances/<group>/<instance>/weighted_window_overlap_by_day.png`
- Tipo: boxplot per giorno.
- Asse `X`: giorno.
- Asse `Y`: overlap pesato.
- Valori del box nel giorno `d`:
  - per ogni paziente `p`:
    - `weighted_active_windows(p, d) = sum_{window contains d} 1 / |window|`
- Interpretazione:
  - penalizza meno le finestre larghe e pesa di piu' le finestre strette;
  - e' una misura di pressione temporale piu' fine del semplice conteggio.

### `spread_capacity_heatmap`
- Output: `plots_instances/<group>/<instance>/spread_capacity_heatmap.png`
- Tipo: heatmap care unit x giorno.
- Asse `X`: giorno.
- Asse `Y`: care unit.
- Colore:
  - `spread_capacity(cu, d) = spread(cu, d) / capacity(cu, d)`
- Formula:
  - `spread(cu, d) = sum_{window contains d and service in cu} duration(service) / |window|`
  - `capacity(cu, d) = sum_{operator in cu,d} operator.duration`
- Interpretazione:
  - approssima quanta "pressione distribuita" grava sulla capacita' della care unit nel giorno.

### `instance_daily_median_window_overlap_distribution`
- Output: `plots_instances/instance_daily_median_window_overlap_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - `daily_median_overlap(d) = median_p active_windows(p, d)`
- Interpretazione:
  - confronta tra istanze la sovrapposizione giornaliera tipica delle finestre.

### `instance_daily_weighted_window_overlap_distribution`
- Output: `plots_instances/instance_daily_weighted_window_overlap_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - `daily_weighted_median_overlap(d) = median_p weighted_active_windows(p, d)`
- Interpretazione:
  - versione pesata del precedente, piu' sensibile a finestre strette.

### `instance_daily_average_spread_capacity_distribution`
- Output: `plots_instances/instance_daily_average_spread_capacity_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - per ogni giorno:
    - `daily_mean_spread_capacity(d) = mean_cu spread_capacity(cu, d)`
- Interpretazione:
  - confronta il carico medio giornaliero normalizzato sulla capacita'.

### `instance_request_count_distribution`
- Output: `plots_instances/instance_request_count_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - per ogni paziente `p`:
    - `request_count(p) = sum_service |windows(p, service)|`
- Interpretazione:
  - misura quante richieste totali porta mediamente ogni paziente.

### `instance_duration_weighted_request_count_distribution`
- Output: `plots_instances/instance_duration_weighted_request_count_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - per ogni paziente `p`:
    - `weighted_request_count(p) = sum_service |windows(p, service)| * duration(service)`
- Interpretazione:
  - simile al conteggio richieste, ma pesato per la durata dei servizi.

### `instance_same_service_overlapping_window_distribution`
- Output: `plots_instances/instance_same_service_overlapping_window_distribution.png`
- Tipo: boxplot per istanza, raggruppato per gruppo.
- Valori del box:
  - per ogni paziente `p`:
    - si contano le finestre che appartengono allo stesso servizio e che hanno overlap con almeno un'altra finestra dello stesso servizio
- Interpretazione:
  - misura quante finestre "ridondanti o conflittuali" same-service ha un paziente.

## Dove aggiornare questa documentazione

- Elenco nomi plot usati dalla GUI:
  - `src/common/plot_catalog.py`
- Descrizione breve nel README:
  - `README.md`
- Riferimento esteso:
  - questo file `docs/plot_reference.md`
