import pyomo.environ as pyo
from pyomo.opt.results import SolverStatus


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


def load_usable_solution(model, result) -> bool:
    """Load a usable incumbent into the model even for non-optimal exits.

    Pyomo refuses to load results with solver.status == error, even when the
    solver returned an incumbent. When that happens and a solution is present,
    coerce the status to 'aborted' so the solution can be loaded safely.
    """

    if not has_usable_solution(result):
        return False

    try:
        if (
            getattr(result.solver, "status", None) == SolverStatus.error
            and len(result.solution) > 0
        ):
            result.solver.status = SolverStatus.aborted
        model.solutions.load_from(result)
        return True
    except Exception:
        return False
