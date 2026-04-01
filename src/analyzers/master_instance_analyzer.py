from src.common.custom_types import MasterInstance

def analyze_master_instance(instance: MasterInstance) -> dict[str, int | float]:

    day_number = len(instance.days)

    care_unit_total_number = sum(len(day.care_units) for day in instance.days.values())
    care_unit_durations = [sum(operator.duration for operator in care_unit.values()) for day in instance.days.values() for care_unit in day.care_units.values()]

    operator_total_number = sum(len(care_unit) for day in instance.days.values() for care_unit in day.care_units.values())
    operator_durations = [operator.duration for day in instance.days.values() for care_unit in day.care_units.values() for operator in care_unit.values()]
    operator_total_duration = sum(operator_durations)

    service_durations = [service.duration for service in instance.services.values()]
    
    all_windows = [window for patient in instance.patients.values() for windows in patient.requests.values() for window in windows]
    total_window_number = len(all_windows)
    all_window_sizes = [window.end - window.start + 1 for window in all_windows]
    total_window_time_slots = sum(all_window_sizes)
    total_time_slots_requested = sum(
        instance.services[service_name].duration * len(windows)
        for patient in instance.patients.values()
        for service_name, windows in patient.requests.items())
    
    patient_number = len(instance.patients)
    patient_request_numbers = [sum(len(windows) for windows in patient.requests.items()) for patient in instance.patients.values()]

    windows_overlapping_per_patient: list[int] = []
    day_number_used_per_patient: list[int] = []

    for patient in instance.patients.values():
        
        patient_windows = [window for windows in patient.requests.values() for window in windows]
        day_number_used_per_patient.append(len([day_name for window in patient_windows for day_name in range(window.start, window.end + 1)]))
        
        overlapping_windows = 0
        for i in range(len(patient_windows) - 1):
            window = patient_windows[i]
            for j in range(i + 1, len(patient_windows)):
                if window.overlaps(patient_windows[j]):
                    overlapping_windows += 1
        
        windows_overlapping_per_patient.append(overlapping_windows)
    
    total_overlapping_windows = sum(windows_overlapping_per_patient)

    return {
        'day_number': day_number,
        'care_unit_total_number': care_unit_total_number,
        'operator_total_number': operator_total_number,
        'patient_number': patient_number,
        'total_window_number': total_window_number,
        
        'average_care_unit_per_day': care_unit_total_number / day_number,
        'min_care_unit_duration': min(care_unit_durations),
        'max_care_unit_duration': max(care_unit_durations),
        'average_care_unit_duration': sum(care_unit_durations) / care_unit_total_number,
        
        'min_operator_duration': min(operator_durations),
        'max_operator_duration': max(operator_durations),
        'average_operator_duration': operator_total_duration / operator_total_number,
        
        'min_service_duration': min(service_durations),
        'max_service_duration': max(service_durations),
        'average_service_duration': sum(service_durations) / len(instance.services),
        
        'operator_total_duration': operator_total_duration,
        'total_time_slots_requested': total_time_slots_requested,
        'request_over_disponibility_ratio': total_time_slots_requested / operator_total_duration,
        
        'min_window_size': min(all_window_sizes),
        'max_window_size': max(all_window_sizes),
        'average_window_size': total_window_time_slots / total_window_number,
        
        'min_patient_request_number': min(patient_request_numbers),
        'max_patient_request_number': max(patient_request_numbers),
        'average_patient_request_number': sum(patient_request_numbers) / patient_number,
        
        'total_overlapping_windows': total_overlapping_windows,
        'min_windows_overlapping_per_patient': min(windows_overlapping_per_patient),
        'max_windows_overlapping_per_patient': max(windows_overlapping_per_patient),
        'average_windows_overlapping_per_patient': total_overlapping_windows / patient_number,
        
        'min_day_number_used_per_patient': min(day_number_used_per_patient),
        'max_day_number_used_per_patient': max(day_number_used_per_patient),
        'average_day_number_used_per_patient': sum(day_number_used_per_patient) / patient_number
    }
