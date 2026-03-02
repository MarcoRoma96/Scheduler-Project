import pyomo.environ as pyo


def get_solver_name(config) -> str:
    """Ritorna il nome del solver normalizzato, con fallback a Gurobi."""

    solver_name = str(config.get('solver_name', 'gurobi')).strip().lower()
    if solver_name not in ['gurobi', 'glpk']:
        raise ValueError(
            f"Unsupported solver_name '{solver_name}'. Supported values: 'gurobi', 'glpk'.")
    return solver_name


def build_solver(
        solver_name: str,
        time_limit: int | float | None,
        memory_limit: int | float | None):
    """Crea e configura il solver con opzioni compatibili."""

    opt = pyo.SolverFactory(solver_name)

    if solver_name == 'gurobi':
        if time_limit is not None:
            opt.options['TimeLimit'] = time_limit
        if memory_limit is not None:
            opt.options['SoftMemLimit'] = memory_limit

    elif solver_name == 'glpk':
        if time_limit is not None:
            opt.options['tmlim'] = max(1, int(time_limit))
        if memory_limit is not None:
            # GLPK usa memlim in MB
            opt.options['memlim'] = max(1, int(float(memory_limit) * 1024))

    return opt


def _termination_name(result) -> str:
    try:
        return str(result.solver.termination_condition).strip().lower()
    except Exception:
        return "unknown"


def _status_name(result) -> str:
    try:
        return str(result.solver.status).strip().lower()
    except Exception:
        return "unknown"


def has_usable_solution(result) -> bool:
    """Ritorna True se il solver ha prodotto una soluzione leggibile da Pyomo."""

    termination = _termination_name(result)
    if termination in {"optimal", "feasible", "locallyoptimal", "globallyoptimal"}:
        return True

    # Alcuni solver in time-limit possono comunque restituire un incumbent.
    try:
        return len(result.solution) > 0
    except Exception:
        return False


def describe_solver_result(result) -> str:
    return f"status={_status_name(result)}, termination={_termination_name(result)}"
