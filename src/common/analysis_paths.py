from __future__ import annotations

from pathlib import Path


def normalize_filter_values(raw_value: object) -> list[str]:
    if isinstance(raw_value, list):
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            token = str(item).strip()
            if token == '':
                continue
            lowered = token.lower()
            if lowered == 'all':
                return ['all']
            if token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized if len(normalized) > 0 else ['all']

    token = str(raw_value).strip() if raw_value is not None else ''
    if token == '' or token.lower() == 'all':
        return ['all']
    return [token]


def is_all_filter_values(raw_value: object) -> bool:
    normalized = normalize_filter_values(raw_value)
    return len(normalized) == 0 or (len(normalized) == 1 and normalized[0].lower() == 'all')


def get_analysis_output_path(results_root: Path, config: dict | None) -> Path:
    return results_root.joinpath('analysis')
