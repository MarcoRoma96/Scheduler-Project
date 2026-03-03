from collections.abc import Iterable
from matplotlib.patches import Patch

from src.common.custom_types import CareUnitName, MasterInstance
from src.common.custom_types import FatSubproblemInstance, SlimSubproblemInstance

CARE_UNIT_COLOR_PALETTE = [
    'tab:blue',
    'tab:orange',
    'tab:green',
    'tab:red',
    'tab:purple',
    'tab:brown',
    'tab:pink',
    'tab:gray',
    'tab:olive',
    'tab:cyan',
]


def get_care_unit_colors(care_unit_names: Iterable[CareUnitName]) -> dict[CareUnitName, str]:
    ordered_names = sorted(set(care_unit_names))
    return {
        care_unit_name: CARE_UNIT_COLOR_PALETTE[index % len(CARE_UNIT_COLOR_PALETTE)]
        for index, care_unit_name in enumerate(ordered_names)
    }


def get_instance_care_unit_colors(
        instance: MasterInstance | FatSubproblemInstance | SlimSubproblemInstance
) -> dict[CareUnitName, str]:
    if isinstance(instance, MasterInstance):
        return get_care_unit_colors(
            care_unit_name
            for day in instance.days.values()
            for care_unit_name in day.care_units.keys()
        )

    return get_care_unit_colors(instance.day.care_units.keys())


def get_care_unit_legend_handles(care_unit_colors: dict[CareUnitName, str]) -> list[Patch]:
    return [
        Patch(facecolor=color, edgecolor='black', label=care_unit_name)
        for care_unit_name, color in care_unit_colors.items()
    ]
