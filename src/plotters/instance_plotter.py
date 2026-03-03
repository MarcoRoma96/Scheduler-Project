import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path

from src.common.custom_types import MasterInstance, FatMasterResult, SlimMasterResult
from src.common.custom_types import DayName, CareUnitName, FinalResult, SlimSubproblemInstance
from src.common.custom_types import PatientServiceOperator, TimeSlot, OperatorName
from src.common.custom_types import FatSubproblemInstance, FatSubproblemResult, SlimSubproblemResult
from src.plotters.care_unit_colors import get_instance_care_unit_colors

def plot_master_results(
        instance: MasterInstance,
        result: FatMasterResult | SlimMasterResult | FinalResult,
        save_path: Path,
        title: str):

    care_unit_colors = get_instance_care_unit_colors(instance)
    
    care_unit_durations = {(day_name, care_unit_name): sum(o.duration for o in care_unit.values())
        for day_name, day in instance.days.items()
        for care_unit_name, care_unit in day.care_units.items()}
    
    column_width = 1.0
    space_between_days = 1.0

    x = 0
    care_unit_xs: dict[tuple[DayName, CareUnitName], float] = {}
    x_ticks_pos: list[float] = []

    for day_name, day in instance.days.items():

        x_ticks_pos.append(x + (len(day.care_units) * column_width) * 0.5)

        for care_unit_name in day.care_units.keys():
            care_unit_xs[day_name, care_unit_name] = x
            x += column_width
        x += space_between_days

    fig, ax = plt.subplots()
    fig.set_size_inches(12, 6)
    
    x_mins = []
    x_maxs = []
    y_pos = []

    for day_name, care_unit_name in care_unit_xs.keys():
        x_mins.append(care_unit_xs[day_name, care_unit_name])
        x_maxs.append(care_unit_xs[day_name, care_unit_name] + column_width)
        y_pos.append(care_unit_durations[day_name, care_unit_name])

    ax.hlines(xmin=x_mins, xmax=x_maxs, y=y_pos, colors='black', lw=2, zorder=0)

    cumulative_heights: dict[tuple[DayName, CareUnitName], int] = {}
    for day_name, requests in result.scheduled.items():
        for request in requests:

            service_name = request.service_name
            care_unit_name = instance.services[service_name].care_unit_name
            duration = instance.services[service_name].duration

            key = (day_name, care_unit_name)
            if key not in cumulative_heights:
                cumulative_heights[key] = 0

            ax.add_patch(Rectangle(
                (care_unit_xs[key], cumulative_heights[key]), column_width, duration,
                linewidth=1, edgecolor='k',
                facecolor=care_unit_colors[care_unit_name]))
            
            cumulative_heights[key] += duration

    max_duration = max(d for d in care_unit_durations.values())
    y_ticks = [0, int(max_duration * 0.25), int(max_duration * 0.5), int(max_duration * 0.75), max_duration]

    ax.set_xlim(left=-space_between_days, right=x)
    ax.set_ylim(bottom=0, top=max_duration + 1)
    ax.set_xticks(x_ticks_pos, labels=list(instance.days.keys())) # type: ignore
    ax.set_yticks(y_ticks, labels=y_ticks) # type: ignore
    
    ax.set_title(title)
    ax.set_xlabel('Days')
    ax.set_ylabel('Total request slots')

    fig.savefig(save_path, dpi=200)
    plt.close('all')

def plot_subproblem_results(
        instance: FatSubproblemInstance | SlimSubproblemInstance,
        result: FatSubproblemResult | SlimSubproblemResult,
        save_path: Path,
        title: str):

    care_unit_colors = get_instance_care_unit_colors(instance)

    row_height = 2.0
    space_between_operators = 1.0

    y = 0
    operator_ys: dict[OperatorName, float] = {}
    y_ticks_pos: list[float] = []

    for operator_name in instance.day.operators.keys():

        y_ticks_pos.append(y + row_height * 0.5)

        operator_ys[operator_name] = y
        y += row_height + space_between_operators
    
    fig, ax = plt.subplots()
    fig.set_size_inches(12, 6)

    xs = []
    ys = []
    x_mins = []
    x_maxs = []
    y_mins = []
    y_maxs = []
    
    for operator_name, operator in instance.day.operators.items():
        
        x_mins.append(operator.start)
        x_maxs.append(operator.end)
        ys.append(operator_ys[operator_name] + 0.5 * row_height)
        
        xs.append(operator.start)
        y_mins.append(operator_ys[operator_name] - space_between_operators * 0.25)
        y_maxs.append(operator_ys[operator_name] + row_height + space_between_operators * 0.25)
        xs.append(operator.end)
        y_mins.append(operator_ys[operator_name] - space_between_operators * 0.25)
        y_maxs.append(operator_ys[operator_name] + row_height + space_between_operators * 0.25)
    
    ax.vlines(x=xs, ymin=y_mins, ymax=y_maxs, colors='black', lw=2, zorder=2)
    ax.hlines(xmin=x_mins, xmax=x_maxs, y=ys, colors='black', lw=2, zorder=0)

    for request in result.scheduled:

        service_name = request.service_name
        operator_name = request.operator_name
        care_unit_name = instance.services[service_name].care_unit_name
        duration = instance.services[service_name].duration
        start_time = request.time_slot

        ax.add_patch(Rectangle(
            (start_time, operator_ys[operator_name]),
            duration, row_height,
            linewidth=1, edgecolor='k',
            facecolor=care_unit_colors[care_unit_name]))
        
        ax.text(
            (start_time + duration * 0.5), operator_ys[operator_name] + row_height * 0.125,
            f'{request.patient_name}\n{service_name}', ha='center')

    operator_end_times: dict[OperatorName, TimeSlot] = {}
    for operator_name in instance.day.operators.keys():
        end_times = [r.time_slot + instance.services[r.service_name].duration
            for r in result.scheduled if r.operator_name == operator_name]
        if len(end_times) == 0:
            operator_end_times[operator_name] = 0
        else:
            operator_end_times[operator_name] = max(end_times)

    max_operator_end_time = max(d for d in operator_end_times.values())

    for request in result.rejected:

        service_name = request.service_name
        care_unit_name = instance.services[service_name].care_unit_name
        duration = instance.services[service_name].duration

        if isinstance(request, PatientServiceOperator):
            operator_name = request.operator_name
        
        else:
            operator_name = min((operator_name
                for operator_name, operator in instance.day.operators.items()
                if operator.care_unit_name == care_unit_name),
                key=lambda o: operator_end_times[o])
        
        start_time = operator_end_times[operator_name]
        operator_end_times[operator_name] += duration
        
        ax.add_patch(Rectangle(
            (start_time, operator_ys[operator_name]),
            duration, row_height,
            linewidth=1, edgecolor='k',
            facecolor=care_unit_colors[care_unit_name], alpha=0.5, zorder=3))
        
        ax.text(
            (start_time + duration * 0.5), operator_ys[operator_name] + row_height * 0.125,
            f'{request.patient_name}\n{service_name}', ha='center', zorder=3)

    max_time_slot = max(operator_end_times.values())
    x_ticks = list(range(0, max_time_slot + 1))

    ax.vlines(x=list(range(max_time_slot + 1)), ymin=-space_between_operators, ymax=y,
              colors='grey', lw=0.5, ls=(0, (5, 10)), zorder=-1)

    ax.set_xlim(left=-1, right=max_time_slot + 1)
    ax.set_ylim(bottom=-space_between_operators, top=y)
    ax.set_xticks(x_ticks, labels=x_ticks) # type: ignore
    ax.set_yticks(y_ticks_pos, labels=list(operator_ys.keys()))

    if max_operator_end_time < max_time_slot:
        for i in range(max_operator_end_time + 1, max_time_slot + 1):
            ax.get_xticklabels()[i].set_color("red")

    ax.set_title(title)
    ax.set_xlabel('Time slots')
    ax.set_ylabel('Operators')

    fig.savefig(save_path)
    plt.close('all')
