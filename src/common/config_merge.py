import copy


def merge_group_config(base_config, config_diff_from_base):
    """Deep-merge base config with group overrides.

    Nested dictionaries are merged recursively so per-group overrides can set
    only a subset of fields (for example only ``master.time_limit``).
    """

    merged = copy.deepcopy(base_config)

    def _merge(dst: dict, src: dict):
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                _merge(dst[key], value)
            else:
                dst[key] = copy.deepcopy(value)

    _merge(merged, config_diff_from_base)
    return merged
