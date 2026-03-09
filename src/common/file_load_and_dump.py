from src.common.custom_types import MasterInstance, Service, Day, Operator, MasterPatient
from src.common.custom_types import ServiceWindow, Window, FatMasterResult, SlimMasterResult
from src.common.custom_types import FatSubproblemInstance, SlimSubproblemInstance, FatSubproblemResult, SlimSubproblemResult
from src.common.custom_types import FinalResult, FatCore, SlimCore, PatientServiceOperator
from src.common.custom_types import PatientServiceOperatorTimeSlot, PatientService, PatientServiceWindow
from src.common.custom_types import SlimSubproblemPatient, FatSubproblemPatient, ServiceOperator, CacheMatch

def decode_master_instance(obj) -> MasterInstance:
    
    instance = MasterInstance()

    for service_name, service in obj['services'].items():
        
        care_unit_name = service['care_unit']
        duration = service['duration']
        
        instance.services[service_name] = Service(care_unit_name, duration)
    
    for day_name, day in obj['days'].items():
        
        instance.days[int(day_name)] = Day()
        
        for care_unit_name, care_unit in day.items():
            for operator_name, operator in care_unit.items():
                
                start = operator['start']
                duration = operator['duration']

                instance.days[int(day_name)].add_operator(operator_name, Operator(
                    care_unit_name, start, duration))
    
    for patient_name, patient in obj['patients'].items():
        
        instance.patients[patient_name] = MasterPatient(patient['priority'])
        
        service_windows: set[ServiceWindow] = set()
        for service_name, windows in patient['requests'].items():
            for window in windows:
                service_windows.add(ServiceWindow(service_name, Window(window[0], window[1])))
        
        instance.patients[patient_name].add_requests(list(service_windows))

    return instance

def encode_services(instance: MasterInstance | FatSubproblemInstance | SlimSubproblemInstance):

    services = {}

    for service_name, service in instance.services.items():
        services[service_name] = {
            'care_unit': service.care_unit_name,
            'duration': service.duration
        }
    
    return services

def encode_patients(instance: MasterInstance | FatSubproblemInstance | SlimSubproblemInstance):

    patients = {}

    if isinstance(instance, MasterInstance):
        for patient_name, patient in instance.patients.items():
            patients[patient_name] = {
                'priority': patient.priority,
                'requests': {}
            }
    else:
        for patient_name, patient in instance.patients.items():
            patients[patient_name] = {
                'priority': patient.priority,
                'requests': []
            }
    
    return patients

def encode_master_instance(instance: MasterInstance):

    obj = {
        'services': encode_services(instance),
        'days': {},
        'patients': encode_patients(instance)
    }
    
    for day_name, day in instance.days.items():
        obj['days'][day_name] = {}
        for care_unit_name, care_unit in day.care_units.items():
            obj['days'][day_name][care_unit_name] = {}
            for operator_name, operator in care_unit.items():
                obj['days'][day_name][care_unit_name][operator_name] = {
                    'start': operator.start,
                    'duration': operator.duration
                }

    for patient_name, patient in instance.patients.items():
        for service_name, windows in patient.requests.items():
            obj['patients'][patient_name]['requests'][service_name] = []
            for window in windows:
                obj['patients'][patient_name]['requests'][service_name].append([
                    window.start, window.end])
    
    return obj

def encode_rejected_result(result: FatMasterResult | SlimMasterResult | FinalResult):

    rejected_requests = []
    
    for psw in result.rejected:
        rejected_requests.append({
            'patient': psw.patient_name,
            'service': psw.service_name,
            'window': [psw.window.start, psw.window.end]
        })
    
    return rejected_requests

def decode_master_result(obj) -> FatMasterResult | SlimMasterResult:

    is_fat = 'operator' in list(obj['scheduled'].values())[0][0]

    if is_fat:
        result = FatMasterResult()
    else:
        result = SlimMasterResult()
    
    for day_name, requests in obj['scheduled'].items():
        result.scheduled[int(day_name)] = []
        for request in requests:
            if isinstance(result, FatMasterResult):
                result.scheduled[int(day_name)].append(PatientServiceOperator(
                    request['patient'], request['service'], request['operator']))
            else:
                result.scheduled[int(day_name)].append(PatientService(
                    request['patient'], request['service']))
    
    for request in obj['rejected']:
        result.rejected.append(PatientServiceWindow(
            request['patient'],
            request['service'],
            Window(request['window'][0], request['window'][1])))

    return result


def encode_master_result(result: FatMasterResult | SlimMasterResult):

    obj = {
        'scheduled': {},
        'rejected': encode_rejected_result(result)
    }

    if isinstance(result, FatMasterResult):
        for day_name, requests in result.scheduled.items():
            obj['scheduled'][day_name] = []
            for pso in requests:
                obj['scheduled'][day_name].append({
                    'patient': pso.patient_name,
                    'service': pso.service_name,
                    'operator': pso.operator_name
                })
    else:
        for day_name, requests in result.scheduled.items():
            obj['scheduled'][day_name] = []
            for pso in requests:
                obj['scheduled'][day_name].append({
                    'patient': pso.patient_name,
                    'service': pso.service_name
                })
    
    return obj

def decode_subproblem_instance(obj) -> FatSubproblemInstance | SlimSubproblemInstance:

    is_fat = type(list(obj['patients'].values())[0]['requests'][0]) == dict
    
    if is_fat:
        instance = FatSubproblemInstance()
    else:
        instance = SlimSubproblemInstance()
    
    for service_name, service in obj['services'].items():
        instance.services[service_name] = Service(service['care_unit'], service['duration'])
    
    for care_unit_name, care_unit in obj['day'].items():
        for operator_name, operator in care_unit.items():
            instance.day.add_operator(operator_name, Operator(
                care_unit_name,
                operator['start'],
                operator['duration']))
    
    if isinstance(instance, FatSubproblemInstance):
        for patient_name, patient in obj['patients'].items():
            instance.patients[patient_name] = FatSubproblemPatient(patient['priority'])
            for request in patient['requests']:
                instance.patients[patient_name].requests.append(ServiceOperator(
                    request['service'], request['operator']))
    else:
        for patient_name, patient in obj['patients'].items():
            instance.patients[patient_name] = SlimSubproblemPatient(patient['priority'])
            for service_name in patient['requests']:
                instance.patients[patient_name].requests.append(service_name)
    
    return instance

def encode_subproblem_instance(instance: FatSubproblemInstance | SlimSubproblemInstance):

    obj = {
        'services': encode_services(instance),
        'day': {},
        'patients': encode_patients(instance)
    }

    for care_unit_name, care_unit in instance.day.care_units.items():
        obj['day'][care_unit_name] = {}
        for operator_name, operator in care_unit.items():
            obj['day'][care_unit_name][operator_name] = {
                'start': operator.start,
                'duration': operator.duration
            }

    if isinstance(instance, FatSubproblemInstance):
        for patient_name, patient in instance.patients.items():
            for request in patient.requests:
                obj['patients'][patient_name]['requests'].append({
                    'service': request.service_name,
                    'operator': request.operator_name
                })
    else:
        for patient_name, patient in instance.patients.items():
            for service_name in patient.requests:
                obj['patients'][patient_name]['requests'].append(service_name)

    return obj

def decode_subproblem_result(obj) -> FatSubproblemResult | SlimSubproblemResult:

    is_fat = len(obj['rejected']) > 0 and 'operator' in obj['rejected'][0]

    if is_fat:
        result = FatSubproblemResult()
    else:
        result = SlimSubproblemResult()

    for request in obj['scheduled']:
        result.scheduled.append(PatientServiceOperatorTimeSlot(
            request['patient'],
            request['service'],
            request['operator'],
            request['time']))
    
    if is_fat:
        for request in obj['rejected']:
            result.rejected.append(PatientServiceOperator(
                request['patient'],
                request['service'],
                request['operator']))
    else:
        for request in obj['rejected']:
            result.rejected.append(PatientService(
                request['patient'],
                request['service'])) # type: ignore

    return result

def encode_subproblem_result(result: FatSubproblemResult | SlimSubproblemResult):

    obj = {
        'scheduled': [],
        'rejected': []
    }

    for request in result.scheduled:
        obj['scheduled'].append({
            'patient': request.patient_name,
            'service': request.service_name,
            'operator': request.operator_name,
            'time': request.time_slot,
        })
    
    for request in result.rejected:
        if isinstance(result, FatSubproblemResult):
            obj['rejected'].append({
                'patient': request.patient_name,
                'service': request.service_name,
                'operator': request.operator_name # type: ignore
            })
        else:
            obj['rejected'].append({
                'patient': request.patient_name,
                'service': request.service_name
            })
    
    return obj

def decode_final_result(obj) -> FinalResult:

    result = FinalResult()

    for day_name, requests in obj['scheduled'].items():
        result.scheduled[int(day_name)] = []
        for request in requests:
            result.scheduled[int(day_name)].append(PatientServiceOperatorTimeSlot(
                request['patient'],
                request['service'],
                request['operator'],
                request['time']))
    
    for request in obj['rejected']:
        result.rejected.append(PatientServiceWindow(
            request['patient'],
            request['service'],
            Window(request['window'][0], request['window'][1])))
    
    return result

def encode_final_result(result: FinalResult):

    obj = {
        'scheduled': {},
        'rejected': encode_rejected_result(result)
    }

    for day_name, requests in result.scheduled.items():
        obj['scheduled'][day_name] = []
        for request in requests:
            obj['scheduled'][day_name].append({
                'patient': request.patient_name,
                'service': request.service_name,
                'operator': request.operator_name,
                'time': request.time_slot,
            })
    
    return obj

def decode_cores(obj) -> list[FatCore] | list[SlimCore]:

    is_fat = False
    for core_obj in obj:
        probe_items = core_obj.get('reason', [])
        if len(probe_items) == 0:
            probe_items = core_obj.get('components', [])
        if len(probe_items) > 0 and 'operator' in probe_items[0]:
            is_fat = True
            break

    cores: list[FatCore] | list[SlimCore] = []

    for core_obj in obj:
        if 'day' in core_obj:
            day = int(core_obj['day'])
        elif 'days' in core_obj:
            days = core_obj['days']
            if isinstance(days, list):
                if len(days) == 0:
                    raise ValueError('Invalid core object: "days" list is empty.')
                day = int(days[0])
            else:
                day = int(days)
        else:
            raise KeyError('day')
        
        if is_fat:
            core = FatCore(day)
        else:
            core = SlimCore(day)

        for reason in core_obj['reason']:
            if is_fat:
                    core.reason.append(PatientServiceOperator(
                        reason['patient'],
                        reason['service'],
                        reason['operator']))
            else:
                    core.reason.append(PatientService(
                        reason['patient'],
                        reason['service'])) # type: ignore
        
        for component in core_obj['components']:
            if is_fat:
                    core.components.append(PatientServiceOperator(
                        component['patient'],
                        component['service'],
                        component['operator']))
            else:
                    core.components.append(PatientService(
                        component['patient'],
                        component['service'])) # type: ignore
        
        cores.append(core) # type: ignore

    return cores

def encode_cores(cores: list[FatCore] | list[SlimCore]):

    objs = []

    for core in cores:

        obj = {
            'reason': [],
            'day': core.day,
            'components': []
        }

        if isinstance(core, FatCore):
            for reason in core.reason:
                obj['reason'].append({
                    'patient': reason.patient_name,
                    'service': reason.service_name,
                    'operator': reason.operator_name
                })
        else:
            for reason in core.reason:
                obj['reason'].append({
                    'patient': reason.patient_name,
                    'service': reason.service_name
                })
        
        if isinstance(core, FatCore):
            for component in core.components:
                obj['components'].append({
                    'patient': component.patient_name,
                    'service': component.service_name,
                    'operator': component.operator_name
                })
        else:
            for component in core.components:
                obj['components'].append({
                    'patient': component.patient_name,
                    'service': component.service_name
                })
        
        objs.append(obj)
    
    return objs

def decode_cache_match(obj) -> CacheMatch:

    matching: CacheMatch = {}

    for day_name, iteration_name in obj.items():
        matching[day_name] = iteration_name
    
    return matching

def encode_cache_matching(matching: CacheMatch):

    obj = {}

    for day_name, iteration_name in matching.items():
        obj[day_name] = iteration_name

    return obj
