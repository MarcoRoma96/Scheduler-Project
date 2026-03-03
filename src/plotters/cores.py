import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path

from src.common.custom_types import FatCore, SlimCore, MasterInstance, OperatorName, TimeSlot
from src.common.custom_types import PatientServiceOperator, DayName, FatSubproblemResult, SlimSubproblemResult
from src.common.tools import is_combination_to_do
from src.plotters.care_unit_colors import get_instance_care_unit_colors

def plot_core_info(master_result_df: pd.DataFrame, results_path: Path, config):

    for key, master_iterations in master_result_df.groupby(['config', 'group', 'instance']):

        if not is_combination_to_do(key[0], key[1], key[2], config):
            continue

        cores_data: dict[str, pd.DataFrame] = {}

        for core_type in ['generalist', 'basic', 'reduced', 'pruned', 'expanded']:
            if f'{core_type}_core_number' not in master_iterations or f'{core_type}_average_core_size' not in master_iterations:
                continue
            
            cores_data[core_type] = master_iterations[[
                'config', 'group', 'instance', 'iteration',
                f'{core_type}_core_number',
                f'{core_type}_average_core_size', f'{core_type}_min_core_size', f'{core_type}_max_core_size',
                f'{core_type}_average_total_duration_per_core', f'{core_type}_min_total_duration_per_core', f'{core_type}_max_total_duration_per_core',
                f'{core_type}_average_care_unit_number_per_core', f'{core_type}_min_care_unit_number_per_core', f'{core_type}_max_care_unit_number_per_core'
            ]]
            cores_data[core_type] = cores_data[core_type].rename(columns={
                f'{core_type}_core_number': 'core_number',
                f'{core_type}_average_core_size': 'average_core_size',
                f'{core_type}_min_core_size': 'min_core_size',
                f'{core_type}_max_core_size': 'max_core_size',
                f'{core_type}_average_total_duration_per_core': 'average_total_duration_per_core',
                f'{core_type}_min_total_duration_per_core': 'min_total_duration_per_core',
                f'{core_type}_max_total_duration_per_core': 'max_total_duration_per_core',
                f'{core_type}_average_care_unit_number_per_core': 'average_care_unit_number_per_core',
                f'{core_type}_min_care_unit_number_per_core': 'min_care_unit_number_per_core',
                f'{core_type}_max_care_unit_number_per_core': 'max_care_unit_number_per_core'
            })
            cores_data[core_type] = cores_data[core_type].sort_values(by='iteration')
        
        is_empty = True
        for core_data in cores_data.values():
            core_data = core_data.drop(columns=['config', 'group', 'instance', 'iteration']).dropna(how='all')
            if not core_data.empty:
                is_empty = False
                break
        if is_empty:
            continue

        min_core_size = 1000
        max_core_size = 0

        for core_type, value in cores_data.items():
            cores_data[core_type] = value.dropna(how='all')
            min_core_size = min(min_core_size, cores_data[core_type]['core_number'].min())
            max_core_size = max(max_core_size, cores_data[core_type]['core_number'].max())

        colors = {
            'generalist': 'black',
            'basic': 'blue',
            'reduced': 'green',
            'pruned': 'red',
            'expanded': 'orange'
        }

        average_result_duration = master_iterations['master_average_scheduled_request_duration_per_day']

        fig, axs = plt.subplots(2, 2)

        for core_type, value in cores_data.items():
            # marker_sizes: pd.Series = (value['core_number'] - min_core_size) / (max_core_size - min_core_size) * 20 + 10
            # axs[0, 0].scatter(value['iteration'], value['average_core_size'], s=marker_sizes, marker='s', facecolors='none', linewidths=2.0, color=colors[core_type])
            axs[0, 0].plot(value['iteration'], value['average_core_size'], color=colors[core_type], label=core_type)
            axs[0, 0].fill_between(value['iteration'], value['min_core_size'], value['max_core_size'], alpha=0.25, linewidth=0, color=colors[core_type])
            
            axs[1, 0].plot(value['iteration'], value['core_number'], color=colors[core_type], label=core_type)

            axs[0, 1].plot(value['iteration'], value['average_total_duration_per_core'] / average_result_duration, color=colors[core_type], label=core_type)
            axs[0, 1].fill_between(value['iteration'], value['min_total_duration_per_core'] / average_result_duration, value['max_total_duration_per_core'] / average_result_duration, alpha=0.25, linewidth=0, color=colors[core_type])
            
            axs[1, 1].plot(value['iteration'], value['average_care_unit_number_per_core'], color=colors[core_type], label=core_type)
            axs[1, 1].fill_between(value['iteration'], value['min_care_unit_number_per_core'], value['max_care_unit_number_per_core'], alpha=0.25, linewidth=0, color=colors[core_type])
        
        axs[0, 0].set_ylabel('Core size')
        axs[1, 0].legend()
        axs[1, 0].set_xlabel('Iteration')
        axs[1, 0].set_ylabel('Core number')
        axs[0, 1].set_ylabel('Core size (%)')
        axs[0, 1].set_ylim([0.0, 1.1])
        axs[1, 1].set_xlabel('Iteration')
        axs[1, 1].set_ylabel('Care unit number')

        save_path = results_path.joinpath(f'{key[0]}__{key[1]}__{key[2]}', 'plots')
        save_path.mkdir(exist_ok=True)

        fig.suptitle(f'Cores of config \'{key[0]}\'\ngroup \'{key[1]}\' instance \'{key[2]}\'')
        fig.tight_layout()
        fig.savefig(save_path.joinpath('cores.png'))
        plt.close('all')

def plot_core_gantt(
        instance: MasterInstance,
        cores: list[FatCore] | list[SlimCore],
        save_path: Path,
        all_subproblem_result: dict[DayName, FatSubproblemResult] | dict[DayName, SlimSubproblemResult],
        title: str):

    care_unit_colors = get_instance_care_unit_colors(instance)

    row_height = 2.0
    space_between_operators = 1.0

    for core_index, core in enumerate(cores):
        day_name = core.day[0]
        if day_name not in all_subproblem_result:
            print(f'day {day_name} does not have subproblem result')
            continue

        y = 0
        operator_ys: dict[OperatorName, float] = {}
        y_ticks_pos: list[float] = []

        for operator_name in instance.days[day_name].operators.keys():

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

        for operator_name, operator in instance.days[day_name].operators.items():
            
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

        operator_end_times: dict[OperatorName, TimeSlot] = {}

        for request in all_subproblem_result[day_name].scheduled:

            patient_name = request.patient_name
            service_name = request.service_name
            operator_name = request.operator_name
            care_unit_name = instance.services[service_name].care_unit_name
            duration = instance.services[service_name].duration
            start_time = request.time_slot
            
            request_in_core = False
            for component in core.components:
                if component.service_name == service_name and component.patient_name == patient_name:
                    if isinstance(component, PatientServiceOperator):
                        if component.operator_name == operator_name:
                            request_in_core = True
                            break
                    else:
                        request_in_core = True
                        break

            if not request_in_core:
                continue

            end_time = start_time + instance.services[service_name].duration
            if operator_name not in operator_end_times:
                operator_end_times[operator_name] = end_time
            else:
                operator_end_times[operator_name] = max(end_time, operator_end_times[operator_name])

            ax.add_patch(Rectangle(
                (start_time, operator_ys[operator_name]),
                duration, row_height,
                linewidth=1, edgecolor='k',
                facecolor=care_unit_colors[care_unit_name]))
            
            ax.text(
                (start_time + duration * 0.5), operator_ys[operator_name] + row_height * 0.125,
                f'{request.patient_name}\n{service_name}', ha='center')

        max_operator_end_time = max(d for d in operator_end_times.values())

        for component in core.reason:

            service_name = component.service_name
            care_unit_name = instance.services[service_name].care_unit_name
            duration = instance.services[service_name].duration

            if isinstance(component, PatientServiceOperator):
                operator_name = component.operator_name
            
            else:
                operator_name = min((operator_name
                    for operator_name, operator in instance.days[day_name].operators.items()
                    if operator.care_unit_name == care_unit_name),
                    key=lambda o: operator_end_times[o])
            
            start_time = operator_end_times[operator_name]
            operator_end_times[operator_name] += duration
            
            ax.add_patch(Rectangle(
                (start_time, operator_ys[operator_name]),
                duration, row_height,
                linewidth=1, edgecolor='k',
                facecolor=care_unit_colors[care_unit_name], alpha=0.5, zorder=1))
            
            ax.text(
                (start_time + duration * 0.5), operator_ys[operator_name] + row_height * 0.125,
                f'{component.patient_name}\n{service_name}', ha='center', zorder=3)

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

        fig.savefig(save_path.joinpath(f'core_{core_index}.png'))
        plt.close('all')
