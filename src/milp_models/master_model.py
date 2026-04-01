import pyomo.environ as pyo
from src.common.custom_types import MasterInstance, PatientName, ServiceName, DayName, TimeSlot
from src.common.custom_types import SlimMasterResult, PatientService, PatientServiceWindow, FatMasterResult
from src.common.custom_types import PatientServiceOperator, FatCore, SlimCore, Window, FatSubproblemResult, SlimSubproblemResult

def get_slim_master_model(instance: MasterInstance, additional_info: list[str]) -> pyo.ConcreteModel:

    model = pyo.ConcreteModel()

    # INSIEMI ##################################################################

    # Tutte le coppie (day, care_unit)
    model.care_units = pyo.Set(initialize=sorted((d, c) for d, day in instance.days.items() for c in day.care_units.keys())) # type: ignore

    max_span: dict[DayName, TimeSlot] = {}
    for day_name, day in instance.days.items():
        min_time_slot = min([o.start for o in day.operators.values()])
        max_time_slot = max([o.start + o.duration for o in day.operators.values()])
        max_span[day_name] = max_time_slot - min_time_slot

    # INDICI ###################################################################

    # Insieme di quadruple (p, s, start, end) per ogni finestra
    window_index = set()

    # Terne (p, s, d) per ogni giorno che può potenzialmente avere (p, s)
    do_index = set()

    # Indici nella forma (p, d)
    pat_days_index = set()

    for patient_name, patient in instance.patients.items():
        for service_name, windows in patient.requests.items():
            for window in windows:
                
                window_index.add((patient_name, service_name, window.start, window.end))
                
                for day_index in range(window.start, window.end + 1):
                    do_index.add((patient_name, service_name, day_index))
                    pat_days_index.add((patient_name, day_index))

    model.window_index = pyo.Set(initialize=sorted(window_index)) # type: ignore
    model.do_index = pyo.Set(initialize=sorted(do_index)) # type: ignore
    model.pat_days_index = pyo.Set(initialize=sorted(pat_days_index)) # type: ignore

    del window_index, do_index, pat_days_index

    # VARIABILI ################################################################

    # Variabili decisionali che specificano quando ogni servizio è programmato
    model.do = pyo.Var(model.do_index, domain=pyo.Binary) # type: ignore

    model.window = pyo.Var(model.window_index, domain=pyo.Binary) # type: ignore

    # VINCOLI ##################################################################

    # Se una finestra è soddisfatta, è soddisfatta in un unico giorno interno
    # alla sua finestra
    @model.Constraint(model.window_index) # type: ignore
    def link_window_to_do_variables(model, p, s, start, end):
        return pyo.quicksum(model.do[pp, ss, d] for pp, ss, d in model.do_index if p == pp and s == ss and d >= start and d <= end) == model.window[p, s, start, end]

    # La durata totale dei servizi programmati per ogni unità di cura non può
    # superare la capacità di quest'ultima
    @model.Constraint(model.care_units) # type: ignore
    def respect_care_unit_capacity(model, d, c):
        
        tuples_affected: list[tuple[PatientName, ServiceName]] = [(p, s) for p, s, dd in model.do_index if d == dd and c == instance.services[s].care_unit_name]
        if len(tuples_affected) == 0:
            return pyo.Constraint.Skip
        
        care_unit_duration = sum([o.duration for o in instance.days[d].care_units[c].values()])
        if sum(instance.services[s].duration for _, s in tuples_affected) <= care_unit_duration:
            return pyo.Constraint.Skip

        return pyo.quicksum(model.do[p, s, d] * instance.services[s].duration for p, s in tuples_affected) <= care_unit_duration

    # Non è possibile inserire richieste dello stesso paziente la cui durata
    # totale eccede gli slot temporali di quel giorno
    @model.Constraint(model.pat_days_index) # type: ignore
    def patient_total_duration(model, p, d):
        
        tuples_affected = [s for pp, s, dd in model.do_index if pp == p and dd == d]
        if sum(instance.services[s].duration for s in tuples_affected) <= max_span[d]:
            return pyo.Constraint.Skip
        
        return pyo.quicksum(model.do[p, s, d] * instance.services[s].duration for s in tuples_affected) <= max_span[d]

    model.cores = pyo.ConstraintList() # type: ignore

    # FUNZIONE OBIETTIVO #######################################################

    # L'obiettivo è massimizzare la durata delle richieste svolte, pesate per la
    # priorità dei pazienti
    
    if 'use_optimality_cuts' in additional_info:

        model.day_index = pyo.Set(initialize=sorted(instance.days.keys())) # type: ignore
        model.optimality_cuts = pyo.ConstraintList() # type: ignore
        model.day_solution_component = pyo.Var(model.day_index, domain=pyo.NonNegativeIntegers) # type: ignore

        @model.Constraint(model.day_index) # type: ignore
        def day_solution_component_constraint(model, d):

            tuple_list = [(p, s) for p, s, dd in model.do_index if d == dd]
            
            if len(tuple_list) == 0:
                return model.day_solution_component[d] == 0
            
            return model.day_solution_component[d] == pyo.quicksum(
                model.do[p, s, d] * instance.services[s].duration * instance.patients[p].priority for p, s in tuple_list)
    
    if 'minimize_hospital_accesses' in additional_info:
        
        model.pat_uses_day = pyo.Var(model.pat_days_index, domain=pyo.Binary) # type: ignore
        
        @model.Constraint(model.do_index) # type: ignore
        def link_do_to_pat_uses_day_variables(model, p, s, d):
            return model.do[p, s, d] <= model.pat_uses_day[p, d]
        
        @model.Constraint(model.pat_days_index) # type: ignore
        def link_do_to_pat_uses_day_variables(model, p, d):
            return model.pat_uses_day[p, d] <= pyo.quicksum(
                model.do[p, s, d] for pp, s, dd in model.do_index if p == pp and d == dd)
    
        @model.Objective(sense=pyo.maximize) # type: ignore
        def objective_function(model): # type: ignore
            return (pyo.quicksum(model.window[p, s, start, end] * instance.services[s].duration * instance.patients[p].priority for p, s, start, end in model.window_index)
                    # - 1.0 / len(model.pat_days_index) * pyo.quicksum(model.pat_uses_day[p, d] for p, d in model.pat_days_index))
                    
                    + (1.0 / len(model.do_index)) * (  pyo.quicksum(model.do[p, s, d] for p, s, d, in model.do_index)
                                                     - pyo.quicksum(model.pat_uses_day[p, d] for p, d in model.pat_days_index)
                                                    )
                    )
    
    else:
        @model.Objective(sense=pyo.maximize) # type: ignore
        def objective_function(model):
            return pyo.quicksum(model.window[p, s, start, end] * instance.services[s].duration * instance.patients[p].priority for p, s, start, end in model.window_index)


    return model # type: ignore

def add_core_constraints_to_slim_master_model(model: pyo.ConcreteModel, cores: list[SlimCore]):
    
    for core in cores:
        
        expr = 1
        d = core.day
        
        for component in core.components:
            
            p = component.patient_name
            s = component.service_name
            
            expr += model.do[p, s, d] # type: ignore
        
        model.cores.add(expr=expr <= len(core.components)) # type: ignore

def add_optimality_cuts(
        model: pyo.ConcreteModel,
        subproblem_results: dict[DayName, FatSubproblemResult | SlimSubproblemResult],
        instance: MasterInstance) -> int:

    if not hasattr(model, 'optimality_cuts') or not hasattr(model, 'day_solution_component'):
        return 0

    cut_count_added = 0

    for day_name, subproblem_result in subproblem_results.items():

        # Il sottoproblema deve avere rifiutato almeno una richiesta
        # perché il taglio abbia senso
        if len(subproblem_result.rejected) <= 0:
            continue

        # Somma delle durate dei servizi svolti questo giorno
        solution_value = sum(
            instance.services[request.service_name].duration * instance.patients[request.patient_name].priority
            for request in subproblem_result.scheduled)
        
        # Lista di coppie (p,s) di ogni servizio chiesto questo giorno (anche se non è stato soddisfatto)
        satisfied_patient_service_tuples = {(request.patient_name, request.service_name) for request in subproblem_result.scheduled}
        satisfied_patient_service_tuples.update((request.patient_name, request.service_name) for request in subproblem_result.rejected)

        # Lista di coppie (p,s) di ogni possibile richiesta che il master potrebbe chiedere in questo giorno
        all_patient_service_tuples: set[tuple[str, str]] = set()
        for patient_name, patient in instance.patients.items():
            for service_name, windows in patient.requests.items():
                for window in windows:
                    if window.start <= day_name and window.end >= day_name:
                        all_patient_service_tuples.add((patient_name, service_name))

        if len(all_patient_service_tuples) == 0:
            continue

        # Durata totale di tutte le richieste possibili questo giorno (usato per big M nel vincolo)
        max_total_service_duration = sum(
            instance.services[service_name].duration * instance.patients[patient_name].priority
            for patient_name, service_name in all_patient_service_tuples)

        model.optimality_cuts.add(expr=
            model.day_solution_component[day_name] <= solution_value +
                                                    (max_total_service_duration - solution_value) * pyo.quicksum(
                                                    model.do[p, s, day_name] for p, s in all_patient_service_tuples if (p, s) not in satisfied_patient_service_tuples))
        cut_count_added += 1

    return cut_count_added


def get_result_from_slim_master_model(model: pyo.ConcreteModel) -> SlimMasterResult:

    result = SlimMasterResult()

    for p, s, d in model.do_index: # type: ignore
        if pyo.value(model.do[p, s, d]) < 0.5: # type: ignore
            continue
        
        if d not in result.scheduled:
            result.scheduled[d] = []
        result.scheduled[d].append(PatientService(p, s))

    for p, s, start, end in model.window_index: # type: ignore
        if pyo.value(model.window[p, s, start, end]) >= 0.5: # type: ignore
            continue
        result.rejected.append(PatientServiceWindow(p, s, Window(start, end)))

    # Ordina le chiavi
    result.scheduled = dict(sorted([(d, r) for d, r in result.scheduled.items()], key=lambda vv: vv[0]))
    for results in result.scheduled.values():
        results.sort(key=lambda r: (r.patient_name, r.service_name))
    result.rejected.sort(key=lambda r: (r.patient_name, r.service_name))

    return result

def get_fat_master_model(instance: MasterInstance, additional_info) -> pyo.ConcreteModel:

    model = pyo.ConcreteModel()

    # INSIEMI ##################################################################

    # Tutte le coppie (day, operator)
    model.operators = pyo.Set(initialize=sorted((d, o) for d, day in instance.days.items() for o in day.operators.keys())) # type: ignore

    max_span: dict[DayName, TimeSlot] = {}
    for day_name, day in instance.days.items():
        min_time_slot = min([o.start for o in day.operators.values()])
        max_time_slot = max([o.start + o.duration for o in day.operators.values()])
        max_span[day_name] = max_time_slot - min_time_slot

    # INDICI ###################################################################

    # Insieme di quadruple (p, s, start, end) per ogni finestra
    window_index = set()

    # Tuple (p, s, d, o) per ogni giorno che può potenzialmente avere (p, s)
    do_index = set()

    # Indici nella forma (p, d)
    pat_days_index = set()

    for patient_name, patient in instance.patients.items():
        for service_name, windows in patient.requests.items():
            care_unit_name = instance.services[service_name].care_unit_name
            for window in windows:
                
                window_index.add((patient_name, service_name, window.start, window.end))
                
                for day_name in range(window.start, window.end + 1):
                    for operator_name in instance.days[day_name].care_units[care_unit_name].keys():
                        do_index.add((patient_name, service_name, day_name, operator_name))
                        pat_days_index.add((patient_name, day_name))

    model.window_index = pyo.Set(initialize=sorted(window_index)) # type: ignore
    model.do_index = pyo.Set(initialize=sorted(do_index)) # type: ignore
    model.pat_days_index = pyo.Set(initialize=sorted(pat_days_index)) # type: ignore

    del window_index, do_index, pat_days_index

    # VARIABILI ################################################################

    # Variabili decisionali che specificano quando ogni servizio è programmato
    model.do = pyo.Var(model.do_index, domain=pyo.Binary) # type: ignore

    model.window = pyo.Var(model.window_index, domain=pyo.Binary) # type: ignore

    # VINCOLI ##################################################################

    # Se una finestra è soddisfatta, è soddisfatta in un unico giorno interno
    # alla sua finestra
    @model.Constraint(model.window_index) # type: ignore
    def link_window_to_do_variables(model, p, s, start, end):
        return pyo.quicksum(model.do[pp, ss, d, o] for pp, ss, d, o in model.do_index if p == pp and s == ss and d >= start and d <= end) == model.window[p, s, start, end]

    # La durata totale dei servizi programmati per ogni operatore non può
    # superare la durata di quest'ultimo
    @model.Constraint(model.operators) # type: ignore
    def respect_operator_duration(model, d, o):

        operator_duration = instance.days[d].operators[o].duration
        
        tuples_affected: list[tuple[PatientName, ServiceName]] = [(p, s) for p, s, dd, oo in model.do_index if d == dd and o == oo]
        if len(tuples_affected) == 0:
            return pyo.Constraint.Skip
        
        if sum(instance.services[s].duration for _, s in tuples_affected) <= operator_duration:
            return pyo.Constraint.Skip

        return pyo.quicksum(model.do[p, s, d, o] * instance.services[s].duration for p, s in tuples_affected) <= operator_duration

    # Non è possibile inserire richieste dello stesso paziente la cui durata
    # totale eccede gli slot temporali di quel giorno
    @model.Constraint(model.pat_days_index) # type: ignore
    def patient_total_duration(model, p, d):
        
        tuples_affected = [(s, o) for pp, s, dd, o in model.do_index if pp == p and dd == d]
        if sum(instance.services[s].duration for s, _ in tuples_affected) <= max_span[d]:
            return pyo.Constraint.Skip
        
        return pyo.quicksum(model.do[p, s, d, o] * instance.services[s].duration for s, o in tuples_affected) <= max_span[d]

    model.cores = pyo.ConstraintList() # type: ignore

    # FUNZIONE OBIETTIVO #######################################################

    # L'obiettivo è massimizzare la durata delle richieste svolte, pesate per la
    # priorità dei pazienti

    if 'minimize_hospital_accesses' in additional_info:
        
        model.pat_uses_day = pyo.Var(model.pat_days_index, domain=pyo.Binary) # type: ignore
        
        model.psd_index = pyo.Set(initialize=sorted((p, s, d) for p, s, d, _ in model.do_index)) # type: ignore

        @model.Constraint(model.psd_index) # type: ignore
        def link_do_to_pat_uses_day_variables(model, p, s, d):
            return pyo.quicksum(model.do[p, s, d, o] for pp, ss, dd, o in model.do_index if p == pp and s == ss and d == dd) <= model.pat_uses_day[p, d]
    
        @model.Objective(sense=pyo.maximize) # type: ignore
        def objective_function(model): # type: ignore
            return (pyo.quicksum(model.window[p, s, start, end] * instance.services[s].duration * instance.patients[p].priority for p, s, start, end in model.window_index)
                    - 1.0 / len(model.pat_days_index) * pyo.quicksum(model.pat_uses_day[p, d] for p, d in model.pat_days_index))
    else:
        @model.Objective(sense=pyo.maximize) # type: ignore
        def objective_function(model):
            return pyo.quicksum(model.window[p, s, start, end] * instance.services[s].duration * instance.patients[p].priority for p, s, start, end in model.window_index)


    return model # type: ignore

def add_core_constraints_to_fat_master_model(model: pyo.ConcreteModel, cores: list[FatCore]):
    
    for core in cores:
        
        expr = 1
        d = core.day

        for component in core.components:
            
            p = component.patient_name
            s = component.service_name
            o = component.operator_name
            
            expr += model.do[p, s, d, o] # type: ignore
        
        model.cores.add(expr=expr <= len(core.components)) # type: ignore

def get_result_from_fat_master_model(model: pyo.ConcreteModel) -> FatMasterResult:

    result = FatMasterResult()

    for p, s, d, o in model.do_index: # type: ignore
        if pyo.value(model.do[p, s, d, o]) < 0.5: # type: ignore
            continue
        
        if d not in result.scheduled:
            result.scheduled[d] = []
        result.scheduled[d].append(PatientServiceOperator(p, s, o))

    for p, s, start, end in model.window_index: # type: ignore
        if pyo.value(model.window[p, s, start, end]) >= 0.5: # type: ignore
            continue
        result.rejected.append(PatientServiceWindow(p, s, Window(start, end)))

    # Ordina le chiavi
    result.scheduled = dict(sorted([(d, r) for d, r in result.scheduled.items()], key=lambda vv: vv[0]))
    for results in result.scheduled.values():
        results.sort(key=lambda r: (r.patient_name, r.service_name, r.operator_name))
    result.rejected.sort(key=lambda r: (r.patient_name, r.service_name))

    return result
