from pathlib import Path

from src.common.custom_types import MasterInstance, FatMasterResult, SlimMasterResult, FinalResult
from src.common.custom_types import PatientName, DayName, PatientService, PatientServiceOperator
from src.common.custom_types import PatientServiceOperatorTimeSlot

def analyze_log(log_path: Path) -> dict[str, int | float | str]:

    analysis: dict[str, int | float | str] = {}

    last_h_line: str | None = None

    with open(log_path, 'r') as file:
        for line in file:
            
            if line.startswith('Optimal solution found'):
                analysis['status'] = 'optimal'
            if line.startswith('Time limit reached'):
                analysis['status'] = 'time_limit'
            if line.startswith('Memory limit reached'):
                analysis['status'] = 'memory_limit'
            
            if line.startswith('Best objective'):
                tokens = line.split()
                analysis['objective_value'] = float(tokens[2].rstrip(','))
                analysis['upper_bound'] = float(tokens[5].rstrip(','))
                gap_token = tokens[-1].rstrip('%')
                if gap_token == '-':
                    analysis['gap'] = float('nan')
                else:
                    analysis['gap'] = float(gap_token)

            if line.startswith('Root relaxation'):
                tokens = line.split()
                if tokens[2] == 'cutoff,':
                    analysis['root_relaxation'] = -1
                else:
                    if tokens[3].startswith('limit'):
                        analysis['root_relaxation'] = -1.0
                    else:
                        analysis['root_relaxation'] = float(tokens[3][:-1])
            
            if line.startswith('Explored'):
                tokens = line.split()
                analysis['time'] = float(tokens[7])
            
            if line.startswith('Optimize a model with'):
                tokens = line.split()
                analysis['constraint_number'] = int(tokens[4])
                analysis['variable_number'] = int(tokens[6])
            
            if line.startswith('Presolved'):
                tokens = line.split()
                analysis['presolved_constraint_number'] = int(tokens[1])
                analysis['presolved_variable_number'] = int(tokens[3])

            if line.startswith('H') or line.startswith('*'):
                last_h_line = line
    
    if last_h_line is not None:
        tokens = last_h_line.split()
        analysis['best_solution_time'] = float(tokens[-1][:-1])

    return analysis

def get_day_number_used_by_patients(all_days_requests: dict[DayName, list[PatientServiceOperator]] | dict[DayName, list[PatientService]] | dict[DayName, list[PatientServiceOperatorTimeSlot]]) -> int:

    day_used_by_patient: dict[PatientName, set[DayName]] = {}
    for day_name, requests in all_days_requests.items():
        for request in requests:
            if request.patient_name not in day_used_by_patient:
                day_used_by_patient[request.patient_name] = set()
            day_used_by_patient[request.patient_name].add(day_name)
    
    return sum(len(day_names) for day_names in day_used_by_patient.values())

def get_total_operator_duration(instance: MasterInstance) -> int:
    return sum(
        operator.duration
        for day in instance.days.values()
        for care_unit in day.care_units.values()
        for operator in care_unit.values())

def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def get_gap_metrics(
        upper_bound,
        feasible_value,
        operator_total_duration: int | float | None,
        prefix: str,
        pct_override = None) -> dict[str, float]:
    upper_bound = _safe_float(upper_bound)
    feasible_value = _safe_float(feasible_value)
    total_operator_duration = _safe_float(operator_total_duration)
    gap_pct_override = _safe_float(pct_override)

    gap_value = float('nan')
    gap_pct = float('nan')
    gap_over_operator_total_duration_ratio = float('nan')
    gap_over_operator_total_duration_pct = float('nan')

    if upper_bound is not None and feasible_value is not None:
        gap_value = max(0.0, upper_bound - feasible_value)

        if gap_pct_override is not None:
            gap_pct = gap_pct_override
        elif abs(feasible_value) > 1e-9:
            gap_pct = gap_value / abs(feasible_value) * 100.0

        if total_operator_duration is not None and total_operator_duration > 1e-9:
            gap_over_operator_total_duration_ratio = gap_value / total_operator_duration
            gap_over_operator_total_duration_pct = gap_over_operator_total_duration_ratio * 100.0

    return {
        f'{prefix}_value': gap_value,
        f'{prefix}_pct': gap_pct,
        f'{prefix}_over_operator_total_duration_ratio': gap_over_operator_total_duration_ratio,
        f'{prefix}_over_operator_total_duration_pct': gap_over_operator_total_duration_pct,
    }

def get_lbbd_final_gap_metrics(
        master_upper_bound,
        final_objective_value,
        operator_total_duration: int | float | None) -> dict[str, float]:
    return get_gap_metrics(
        master_upper_bound,
        final_objective_value,
        operator_total_duration,
        prefix='lbbd_final_gap')

def get_result_value(
        instance: MasterInstance,
        result: FatMasterResult | SlimMasterResult | FinalResult,
        additional_info: list[str],
        worst_case_day_number: int | None) -> float:

    value = 0
    for patient_name, patient in instance.patients.items():
        for service_name, windows in patient.requests.items():
            for window in windows:
                
                is_window_satisfied = False
                
                for day_index in range(window.start, window.end + 1):
                    for request in result.scheduled[day_index]:
                        if patient_name == request.patient_name and service_name == request.service_name:
                            is_window_satisfied = True
                            break
                    if is_window_satisfied:
                        break

                if is_window_satisfied:
                    value += instance.services[service_name].duration * instance.patients[patient_name].priority

    if 'minimize_hospital_accesses' in additional_info and worst_case_day_number is not None:
        all_days_requests = result.scheduled
        value -= get_day_number_used_by_patients(all_days_requests) / worst_case_day_number

    return value
