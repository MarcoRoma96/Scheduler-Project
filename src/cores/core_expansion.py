import time

from src.common.custom_types import FatCore, SlimCore, DayName, MasterInstance, ServiceName, Service
from src.common.custom_types import PatientService, PatientServiceOperator, SlimArc, FatArc, CareUnitName
from src.common.solver_factory import get_solver_name, build_solver
from src.milp_models.max_matching_model import get_max_matching_model, get_matching_from_max_matching_model, ban_matching_from_model
from src.milp_models.subsumption_model import get_subsumption_model, subsumption_model_has_solution

def get_expansion_arcs(
        core: FatCore | SlimCore,
        all_possible_master_requests: list[PatientServiceOperator] | list[PatientService],
        services: dict[ServiceName, Service],
        config) -> set[SlimArc] | set[FatArc]:
    """Funzione che ritorna l'elenco di tutte le coppie che rappresentano
    possibili rinomine fra componenti del core e possibili richieste del master.
    Questo elenco definisce un grafo bipartito. A seconda dei parametri di
    configurazione alcuni di questi archi non verrano inclusi."""

    is_fat = isinstance(core, FatCore)

    # Lista di tutti gli archi del grafo bipartito
    arcs: set[FatArc] | set[SlimArc] = set()

    # Ogni componente del core viene vista come un vertice di sinistra
    for component in core.components:

        # Ricerca dei vertici di destra a lui collegati
        for request in all_possible_master_requests:

            # Il servizio deve mantenere la stessa unità di cura
            if services[component.service_name].care_unit_name != services[request.service_name].care_unit_name:
                continue

            # I servizi di destra (del master) sono validi solo se hanno durata
            # maggiore o uguale a quelli di sinistra (del core)
            if services[component.service_name].duration > services[request.service_name].duration:
                continue

            # Se il nome del paziente o servizio è differente e non li stiamo
            # anonimizzando, l'arco non va bene
            if ((not config['core_patient_expansion'] and component.patient_name != request.patient_name) or
                (not config['core_service_expansion'] and component.service_name != request.service_name)):
                continue

            # Se il nome dell'operatore è differente e non lo stiamo
            # anonimizzando, l'arco non va bene
            if is_fat and not config['core_operator_expansion'] and component.operator_name != request.operator_name: # type: ignore
                continue
            
            # Se i controlli sopra non si sono attivati, aggiungi l'arco nel
            # grafo
            if is_fat:
                arcs.add((
                    PatientServiceOperator(component.patient_name, component.service_name, component.operator_name), # type: ignore
                    PatientServiceOperator(request.patient_name, request.service_name, request.operator_name))) # type: ignore
            else:
                arcs.add((
                    PatientService(component.patient_name, component.service_name),
                    PatientService(request.patient_name, request.service_name)))

    return arcs


def get_core_from_matching(
        core: FatCore | SlimCore,
        matching: set[SlimArc] | set[FatArc]) -> FatCore | SlimCore:
    """Rinomina del core con i dati presenti nel matching. Eventuali nomi non
    presenti saranno copiati senza modifiche. Questa funzione non modifica il
    core di input."""

    if isinstance(core, FatCore):
        matched_core = FatCore(day=core.day)
    else:
        matched_core = SlimCore(day=core.day)

    # Ogni componente del core andrà rinominata, se esiste nel matching
    for component in core.components:

        matched_component = None
        for arc in matching:
            if arc[0] == component:
                matched_component = arc[1]
                break
        
        if matched_component is not None:
            matched_core.components.append(matched_component) # type: ignore
        else:
            matched_core.components.append(component) # type: ignore
    
    # Ogni motivo del core andrà rinominato, se esiste nel matching
    for component in core.reason:

        matched_component = None
        for arc in matching:
            if arc[0] == component:
                matched_component = arc[1]
                break
        
        if matched_component is not None:
            matched_core.reason.append(matched_component) # type: ignore
        else:
            matched_core.reason.append(component) # type: ignore
    
    return matched_core


def expand_cores(
        cores: list[FatCore] | list[SlimCore],
        all_possible_master_requests: dict[DayName, list[PatientServiceOperator]] | dict[DayName, list[PatientService]],
        services: dict[ServiceName, Service],
        config,
        subsumptions=None) -> list[FatCore] | list[SlimCore]:

    expanded_cores: list[FatCore] | list[SlimCore] = []

    solver_name = get_solver_name(config)
    opt = build_solver(
        solver_name,
        config['core_expansion']['time_limit'],
        config['core_expansion']['memory_limit'])

    print(f'Expanding {len(cores)} cores')
    for core_index, core in enumerate(cores):

        # Insieme di giorni su cui il core verrà espanso
        days_to_do: set[DayName] = set([core.day])

        # Se bisogna espandere i giorni
        if config['core_day_expansion'] and subsumptions is not None:

            # Insieme di tutte le unità di cura toccate dal core corrente
            care_unit_affected: set[CareUnitName] = set()
            for component in core.components:
                care_unit_affected.add(services[component.service_name].care_unit_name)

            # Elenco di giorni più piccoli o uguali al giorno corrente in
            # tutte le unità di cura toccate
            smaller_days: set[DayName] = subsumptions[care_unit_affected.pop()][core.day]
            for care_unit_name in care_unit_affected:
                smaller_days.intersection_update(subsumptions[care_unit_name][core.day])
            
            days_to_do.update(smaller_days)

        for day_name in days_to_do:

            print(f'core {core_index + 1}, day {day_name}: ', end='')
            arcs = get_expansion_arcs(core, all_possible_master_requests[day_name], services, config)
            
            matching_model = get_max_matching_model(arcs)
            if matching_model is None:
                print('nomodel')
                continue

            core_expansion_number = 0

            while core_expansion_number < config['max_single_core_expansion']:

                start = time.perf_counter()
                result = opt.solve(matching_model, logfile=None)
                end = time.perf_counter()
                
                if end - start >= config['core_expansion']['time_limit']:
                    print('tmlim')
                    break
                
                matching = get_matching_from_max_matching_model(matching_model, result)

                if len(matching) != len(core.components):
                    print('nosol')
                    break

                print('.', end='')

                expanded_core = get_core_from_matching(core, matching)
                expanded_core.day = day_name
                expanded_cores.append(expanded_core) # type: ignore
                ban_matching_from_model(matching_model, matching)

                core_expansion_number += 1
            
            if core_expansion_number >= config['max_single_core_expansion']:
                print('maxcut')

    return expanded_cores


def get_subsumptions(instance: MasterInstance, config) -> dict[CareUnitName, dict[DayName, set[DayName]]]:

    subsumptions: dict[CareUnitName, dict[DayName, set[DayName]]] = {}

    # Nomi delle unità di cura toccate
    care_unit_names: set[CareUnitName] = set()
    for day in instance.days.values():
        care_unit_names.update(day.care_units.keys())
    
    solver_name = get_solver_name(config)
    opt = build_solver(
        solver_name,
        config['subsumption']['time_limit'],
        config['subsumption']['memory_limit'])

    # Generazione della relazione di minore o uguale per ogni unità di cura
    for care_unit_name in care_unit_names:
        subsumptions[care_unit_name] = {}

        # Ogni giorno ricerca i suoi minori o uguali
        for big_day_name, big_day in instance.days.items():
            if care_unit_name not in big_day.care_units:
                continue

            subsumptions[care_unit_name][big_day_name] = set()

            for small_day_name, small_day in instance.days.items():
                if big_day_name == small_day_name:
                    continue
                if care_unit_name not in small_day.care_units:
                    continue
                if small_day_name in subsumptions[care_unit_name][big_day_name]:
                    continue

                subsumption_model = get_subsumption_model(big_day.care_units[care_unit_name], small_day.care_units[care_unit_name])

                start = time.perf_counter()
                result = opt.solve(subsumption_model, logfile=None)
                end = time.perf_counter()

                if end - start >= config['core_expansion']['time_limit']:
                    continue

                if subsumption_model_has_solution(subsumption_model, result):
                    subsumptions[care_unit_name][big_day_name].add(small_day_name)
                    if small_day_name in subsumptions[care_unit_name]:
                        subsumptions[care_unit_name][big_day_name].update(subsumptions[care_unit_name][small_day_name])

    return subsumptions
