"""CBC invocation and conservative termination reporting, separate from formulation."""
import re,time
from pathlib import Path
import pulp
from phase2.engine import check


def solve(model,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'cbc.log'
    solver=pulp.PULP_CBC_CMD(msg=False,timeLimit=model.data.config['solver_time_limit_seconds'],gapRel=0,gapAbs=0,threads=1,
                             options=['randomSeed 1'],logPath=str(path),warmStart=getattr(model,'has_warm_start',False))
    check(solver.available(),'CBC solver unavailable; install phase5/requirements.txt in .venv-phase5')
    model.problem.writeLP(str(folder/'model.lp'))
    start=time.perf_counter();model.problem.solve(solver);elapsed=time.perf_counter()-start
    log=path.read_text()
    feasible=model.problem.sol_status in (pulp.LpSolutionOptimal,pulp.LpSolutionIntegerFeasible)
    proven=(model.problem.sol_status==pulp.LpSolutionOptimal and 'Optimal solution found' in log and 'within gap tolerance' not in log)
    # CBC presolve can report a fully fixed model without the usual result heading.
    if model.problem.sol_status==pulp.LpSolutionOptimal and 'Optimal objective' in log:proven=True
    exact=model.data.config['holding_basis']=='moving_average_exact'
    status=('OPTIMAL' if exact else 'OPTIMAL_FIXED_STANDARD_MODEL') if proven else ('FEASIBLE_NOT_PROVEN' if feasible else pulp.LpStatus[model.problem.status].upper())
    objective=float(pulp.value(model.problem.objective)) if feasible else None
    bound=None
    match=re.search(r'Lower bound:\s*([-+\d.eE]+)',log)
    if proven:bound=objective
    elif match:bound=float(match.group(1))-0.0005+float(model.data.sunk_firm) # Conservative allowance for CBC's three-decimal log rounding.
    gap=(max(0,objective-bound)/max(abs(objective),1)) if feasible and bound is not None else None
    return dict(status=status,feasible_incumbent=feasible,proven_optimal_for_fixed_standard_model=proven and not exact,proven_optimal=proven,holding_basis=model.data.config['holding_basis'],
        objective=objective,best_bound=bound,relative_gap=gap,runtime_seconds=elapsed,solver='CBC via PuLP',
        pulp_version=pulp.__version__,cbc_path=str(solver.path),cbc_problem_status=pulp.LpStatus[model.problem.status],
        cbc_solution_status=pulp.LpSolution[model.problem.sol_status],variables=len(model.problem.variables()),constraints=len(model.problem.constraints),
        interpretation='Optimality applies only to the configured model and holding basis; a feasible time-limited incumbent is not proven optimal.')
