import time

from src.common.custom_types import FatCore, PatientServiceOperator, Service, SlimSubproblemResult
from src.common.custom_types import ServiceName, SlimCore, PatientService, ServiceOperator
from src.common.custom_types import FatSubproblemInstance, SlimSubproblemInstance
from src.common.custom_types import DayName, FatSubproblemPatient, SlimSubproblemPatient
from src.common.solver_factory import get_solver_name, build_solver
from src.checkers.check_subproblem_instance import check_fat_subproblem_instance, check_slim_subproblem_instance
from src.milp_models.subproblem_model import get_fat_subproblem_model, get_slim_subproblem_model
from src.milp_models.subproblem_model import get_result_from_fat_subproblem_model, get_result_from_slim_subproblem_model


def get_fat_core_components_metric(core: FatCore) -> dict[PatientServiceOperator, int]:
    """Calcolo della distanza a partire dalla sorgente del core, aggiungendo 1
    per ogni arco attraversato."""

    components_metric: dict[PatientServiceOperator, int] = {}

    requests_to_visit: set[PatientServiceOperator] = set()
    requests_visited: set[PatientServiceOperator] = set()

    # Richiesta di partenza
    requests_to_visit.add(core.reason[0])
    components_metric[core.reason[0]] = 0

    while len(requests_to_visit) > 0:

        # Estrai una richiesta e segnala come visitata
        request = requests_to_visit.pop()
        requests_visited.add(request)

        patient_name = request.patient_name
        operator_name = request.operator_name

        # Cerca ogni altra richiesta a lei collegata
        for other_request in core.components:
            if other_request in requests_visited:
                continue
            if other_request in requests_to_visit:
                continue
            
            # Se il paziente o l'operatore sono gli stessi
            if operator_name == other_request.operator_name:
                requests_to_visit.add(other_request)
                components_metric[other_request] = components_metric[request] + 1
            
            elif patient_name == other_request.patient_name:
                requests_to_visit.add(other_request)
                components_metric[other_request] = components_metric[request] + 10
    
    return components_metric

def get_slim_core_components_metric(
        services: dict[ServiceName, Service],
        result: SlimSubproblemResult,
        core: SlimCore) -> dict[PatientService, int]:
    """Calcolo della distanza a partire dalla sorgente del core, aggiungendo 1
    per ogni arco attraversato."""

    components_metric: dict[PatientService, int] = {}

    requests_to_visit: set[PatientService] = set()
    requests_visited: set[PatientService] = set()

    # Richiesta di partenza
    requests_to_visit.add(core.reason[0])
    components_metric[core.reason[0]] = 0

    while len(requests_to_visit) > 0:

        # Estrai una richiesta e segnala come visitata
        request = requests_to_visit.pop()
        requests_visited.add(request)

        patient_name = request.patient_name
        care_unit_name = services[request.service_name].care_unit_name

        operator_name = None
        for subproblem_request in result.scheduled:
            if subproblem_request.patient_name == patient_name and subproblem_request.service_name == request.service_name:
                operator_name = subproblem_request.operator_name
                break
    
        # Cerca ogni altra richiesta a lei collegata
        for other_request in core.components:
            if other_request in requests_visited:
                continue
            if other_request in requests_to_visit:
                continue
            
            other_patient_name = other_request.patient_name

            # Se il paziente o l'operatore sono gli stessi
            if operator_name is not None:
                
                other_operator_name = None
                for subproblem_request in result.scheduled:
                    if subproblem_request.patient_name == other_patient_name and subproblem_request.service_name == other_request.service_name:
                        other_operator_name = subproblem_request.operator_name
                        break
                
                if operator_name == other_operator_name:
                    requests_to_visit.add(other_request)
                    components_metric[other_request] = components_metric[request] + 1
            
            else:
                other_care_unit_name = services[other_request.service_name].care_unit_name
                if care_unit_name == other_care_unit_name:
                    requests_to_visit.add(other_request)
                    components_metric[other_request] = components_metric[request] + 1
            
            if other_request not in components_metric and patient_name == other_patient_name:
                requests_to_visit.add(other_request)
                components_metric[other_request] = components_metric[request] + 10
    
    return components_metric

def is_instance_fully_satisfiable(
        instance: FatSubproblemInstance | SlimSubproblemInstance,
        config) -> bool:
    """Risolve un'istanza del sottoproblema e ritorna True se ogni richiesta è
    stata soddisfatta."""

    if isinstance(instance, FatSubproblemInstance):
        model = get_slim_subproblem_model(instance)
    else:
        model = get_fat_subproblem_model(instance, config['core_pruning']['additional_info'])
    
    solver_name = get_solver_name(config)
    opt = build_solver(
        solver_name,
        config['core_pruning']['time_limit'],
        config['core_pruning']['memory_limit'],
        config['core_pruning'])

    start = time.perf_counter()
    opt.solve(model, logfile=None)
    end = time.perf_counter()
    if end - start > config['core_pruning']['time_limit']:
        return True

    if isinstance(instance, FatSubproblemInstance):
        result = get_result_from_slim_subproblem_model(model)
    else:
        result = get_result_from_fat_subproblem_model(model)

    return len(result.rejected) == 0

def get_pruned_fat_cores(
        instances: dict[DayName, FatSubproblemInstance],
        reduced_cores: list[FatCore],
        config) -> list[FatCore]:
    """Computazione dei core ridotti, a cui si tenta progressivamente di
    togliere le richieste più lontane secondo una metrica euristica. Appena il
    core diventa pienamente soddisfacibile si torna indietro di un passaggio."""
    
    cores = reduced_cores

    # Ogni core tenta la potatura
    for core_index, core in enumerate(cores):
        if len(core.components) <= 1:
            continue

        # Ottenimento della metrica euristica con cui selezionare le richieste
        components_metric = get_fat_core_components_metric(core)

        # Ordine euristico con cui le richieste verranno progressivamente
        # eliminate dall'istanza
        sorted_requests = sorted(
            components_metric.keys(),
            key=lambda c: components_metric[c])
        
        # Il calcolo verrà eseguito su una copia dell'istanza del giorno del
        # core
        instance = instances[core.day]
        cloned_instance = FatSubproblemInstance(
            services=instance.services,
            day=instance.day)
        
        start = 0
        end = len(sorted_requests) - 1
        cursor = (end - start) // 2 + start

        print(f'Pruning core ({core_index + 1}/{len(cores)}) with {len(core.components)}:', end='')

        # Continua a togliere richieste finchè l'istanza non è risolta
        # pienamente
        while end > start + 1:

            print(f' -> {cursor}', end='')
            
            # Elimina i pazienti dall'istanza copiata
            cloned_instance.patients = {}
            
            # Aggiungi le richieste fino a 'cursor'
            for request in sorted_requests[:cursor + 1]:
                
                patient_name = request.patient_name
                service_name = request.service_name
                operator_name = request.operator_name

                if patient_name not in cloned_instance.patients:
                    cloned_instance.patients[patient_name] = FatSubproblemPatient(instance.patients[patient_name].priority)
                cloned_instance.patients[patient_name].requests.append(ServiceOperator(service_name, operator_name))

            errors = check_fat_subproblem_instance(cloned_instance)
            if len(errors) > 0:
                for error in errors:
                    print(f'ERROR: {error}')
                return []

            # Ricerca dicotomica
            if is_instance_fully_satisfiable(cloned_instance, config):
                start = cursor
            else:
                end = cursor
                print('x', end='')
            
            cursor = (end - start) // 2 + start

        # Le nuove componenti sono quelle rimaste, più l'ultima appena tolta che
        # ha garantito la piena soddisfacibilità
        if end >= len(core.reason):
            core.components = sorted_requests[:end + 1]
        else:
            print(f'ERROR: core size is less than its reason')

        print(f' done with {len(core.components)} components')

        # Ogni componente del core viene testata per raggiungere
        # l'irriducibilità
        if config['post_pruning_irreducibility']:

            print(f'Checking irreducibility ({len(core.components)} components):', end='')

            # Crea una copia delle componenti del core
            irreducible_components: list[PatientServiceOperator] = core.components.copy()

            # Tenta di eliminare ogni componente (a parte la prima e l'ultima)
            # cercando di mantenere la non soddisfacibilità
            for component_index, component in enumerate(core.components[1:-1]):

                # Rimuovi la componente corrente
                irreducible_components.remove(component)

                # Elimina i pazienti dall'istanza copiata
                cloned_instance.patients = {}
                
                # Aggiungi le richieste senza quella corrente
                for request in irreducible_components:
                    
                    patient_name = request.patient_name
                    service_name = request.service_name
                    operator_name = request.operator_name

                    if patient_name not in cloned_instance.patients:
                        cloned_instance.patients[patient_name] = FatSubproblemPatient(instance.patients[patient_name].priority)
                    cloned_instance.patients[patient_name].requests.append(ServiceOperator(service_name, operator_name))

                errors = check_fat_subproblem_instance(cloned_instance)
                if len(errors) > 0:
                    for error in errors:
                        print(f'ERROR: {error}')
                    return []

                print(f' {component_index + 2}')
                if is_instance_fully_satisfiable(cloned_instance, config):
                    irreducible_components.append(component)
                    print('y', end='')
                else:
                    print('x', end='')
            
            print('')
            
            core.components = irreducible_components
    
    return cores

def get_pruned_slim_cores(
        all_subproblem_results: dict[DayName, SlimSubproblemResult],
        instances: dict[DayName, SlimSubproblemInstance],
        reduced_cores: list[SlimCore],
        config) -> list[SlimCore]:
    """Computazione dei core ridotti, a cui si tenta progressivamente di
    togliere le richieste più lontane secondo una metrica euristica. Appena il
    core diventa pienamente soddisfacibile si torna indietro di un passaggio."""
    
    cores = reduced_cores

    # Ogni core tenta la potatura
    for core_index, core in enumerate(cores):
        if len(core.components) <= 1:
            continue

        day_name = core.day

        # Ottenimento della metrica euristica con cui selezionare le richieste
        # (fat anche se i core sono slim per avere più granellazione)
        components_metric = get_slim_core_components_metric(instances[day_name].services, all_subproblem_results[day_name], core)

        # Ordine euristico con cui le richieste verranno progressivamente
        # eliminate dall'istanza
        sorted_requests = sorted(
            components_metric.keys(),
            key=lambda c: components_metric[c])

        # Il calcolo verrà eseguito su una copia dell'istanza del giorno del
        # core
        instance = instances[core.day]
        cloned_instance = SlimSubproblemInstance(
            services=instance.services,
            day=instance.day)
        
        start = 0
        end = len(sorted_requests) - 1
        cursor = (end - start) // 2 + start

        print(f'Pruning core ({core_index + 1}/{len(cores)}) with {len(core.components)}:', end='')

        # Continua a togliere richieste finchè l'istanza non è risolta
        # pienamente
        while end > start + 1:

            print(f' -> {cursor}', end='')
            
            # Elimina i pazienti dall'istanza copiata
            cloned_instance.patients = {}
            
            # Aggiungi le richieste fino a 'cursor'
            for request in sorted_requests[:cursor + 1]:
                
                patient_name = request.patient_name
                service_name = request.service_name

                if patient_name not in cloned_instance.patients:
                    cloned_instance.patients[patient_name] = SlimSubproblemPatient(instance.patients[patient_name].priority)
                cloned_instance.patients[patient_name].requests.append(service_name)

            errors = check_slim_subproblem_instance(cloned_instance)
            if len(errors) > 0:
                for error in errors:
                    print(f'ERROR: {error}')
                return []

            # Ricerca dicotomica
            if is_instance_fully_satisfiable(cloned_instance, config):
                start = cursor
            else:
                end = cursor
                print('x', end='')
            
            cursor = (end - start) // 2 + start


        # Le nuove componenti sono quelle rimaste, più l'ultima appena tolta che
        # ha garantito la piena soddisfacibilità
        if end >= len(core.reason):
            core.components = sorted_requests[:end + 1]
        else:
            print(f'ERROR: core size is less than its reason')

        print(f' done with {len(core.components)} components')

        # Ogni componente del core viene testata per raggiungere
        # l'irriducibilità
        if config['post_pruning_irreducibility']:

            print(f'Checking irreducibility ({len(core.components)} components):', end='')

            # Crea una copia delle componenti del core
            irreducible_components: list[PatientService] = core.components.copy()
            
            # Tenta di eliminare ogni componente (a parte la prima e l'ultima)
            # cercando di mantenere la non soddisfacibilità
            for component_index, component in enumerate(core.components[1:-1]):

                # Rimuovi la componente corrente
                irreducible_components.remove(component)

                # Elimina i pazienti dall'istanza copiata
                cloned_instance.patients = {}
                
                # Aggiungi le richieste senza quella corrente
                for request in irreducible_components:
                    
                    patient_name = request.patient_name
                    service_name = request.service_name

                    if patient_name not in cloned_instance.patients:
                        cloned_instance.patients[patient_name] = SlimSubproblemPatient(instance.patients[patient_name].priority)
                    cloned_instance.patients[patient_name].requests.append(service_name)

                errors = check_slim_subproblem_instance(cloned_instance)
                if len(errors) > 0:
                    for error in errors:
                        print(f'ERROR: {error}')
                    return []

                print(f' {component_index + 2}', end='')

                if is_instance_fully_satisfiable(cloned_instance, config):
                    irreducible_components.append(component)
                    print('y', end='')
                else:
                    print('x', end='')
            
            print('')

            core.components = irreducible_components

    return cores
