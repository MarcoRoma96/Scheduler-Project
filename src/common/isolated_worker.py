from pathlib import Path
import json
import resource
import time


RUN_STATUS_FILENAME = 'run_status.json'


def write_run_status(
        output_path: Path,
        *,
        status: str,
        config_name: str,
        group_name: str,
        instance_name: str,
        message: str,
        error_code: int | None = None,
        return_code: int | None = None,
        stage: str | None = None,
        **extra_fields):
    output_path.mkdir(exist_ok=True)
    payload = {
        'status': status,
        'config': config_name,
        'group': group_name,
        'instance': instance_name,
        'message': message,
        'error_code': error_code,
        'return_code': return_code,
        'stage': stage,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    payload.update(extra_fields)
    with open(output_path.joinpath(RUN_STATUS_FILENAME), 'w') as file:
        json.dump(payload, file, indent=4)


def build_worker_preexec(memory_limit_gb: int | float | None):
    if memory_limit_gb is None or memory_limit_gb <= 0:
        return None

    limit_bytes = int(float(memory_limit_gb) * 1024 * 1024 * 1024)

    def _preexec():
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))

    return _preexec


def describe_worker_returncode(return_code: int) -> str:
    if return_code < 0:
        return f'Worker terminated by signal {-return_code}.'
    return f'Worker failed with return code {return_code}.'
