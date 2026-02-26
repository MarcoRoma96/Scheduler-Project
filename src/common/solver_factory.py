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
