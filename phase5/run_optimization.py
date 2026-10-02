"""Execute and independently verify reactive optimization and service trade-offs."""
import argparse,copy,importlib.util,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if importlib.util.find_spec('pulp') is None and __name__=='__main__':
    interpreter=ROOT/'.venv-phase5/bin/python'
    if not interpreter.exists():raise SystemExit('Install the isolated solver: python3 -m venv .venv-phase5 && .venv-phase5/bin/python -m pip install -r phase5/requirements.txt')
    os.execv(str(interpreter),[str(interpreter),str(Path(__file__).resolve()),*sys.argv[1:]])
from phase2.engine import D,check,input_hashes,load_company,simulate,write_result,serializable
from phase3.scenarios import ScenarioEffects
from phase4.strategies import Mitigation
from phase4.preparation import prepare
from phase4.valuation import value_run
from phase4.run_mitigations import dump,write_csv
from phase5.data import ProblemData,standard_accounting,model_accounting
from phase5.formulation import WeeklyModel
from phase5.exact_formulation import ExactMovingAverageModel
from phase5.solver import solve
from phase5.execution import plan_from_run
from phase5.reconcile import reconcile


def service_tables(run):
    rows=[]
    for kind,field in [('product','product_id'),('warehouse','warehouse_id'),('week','week_start')]:
        for key in sorted({r[field] for r in run['warehouse_inventory']}):
            selected=[r for r in run['warehouse_inventory'] if r[field]==key]
            demand=sum(r['demand_units'] for r in selected);fulfilled=sum(r['fulfilled_units'] for r in selected)
            rows.append(dict(group_type=kind,group_id=key,demand_units=demand,fulfilled_units=fulfilled,
                unmet_units=demand-fulfilled,unit_fill_rate=D(fulfilled)/demand if demand else None))
    return rows


def case_row(name,run,standard,legacy):
    return dict(case=name,demand_units=run['totals']['demand_units'],fulfilled_units=run['totals']['fulfilled_units'],
        unmet_units=run['totals']['unmet_units'],unit_fill_rate=run['totals']['unit_fill_rate'],
        common_basis_cost=standard['total_committed_modeled_cost'],legacy_moving_average_cost=legacy['gross_committed_resource_cost'],
        holding_basis_difference=standard['total_committed_modeled_cost']-legacy['gross_committed_resource_cost'],
        purchasing_commitments=standard['purchasing_commitments'],conversion_cost=standard['conversion_cost'],
        transportation_commitments=standard['inbound_freight_commitments']+standard['outbound_freight_cost'],holding_cost=standard['holding_cost'],
        terminal_owned_inventory_value=standard['terminal_owned_inventory_value'],pending_commitments=standard['pending_purchase_and_freight_commitment'],
        potential_sales_revenue_exposure=run['totals']['potential_sales_revenue_exposure'])


def report(comparison,tradeoff,output):
    money=lambda v:f'${v:,.2f}' if v is not None else 'N/A'
    lines=['# Phase 5: verified reactive optimization','',
      '**Accounting boundary:** the default MILP minimizes committed purchases, conversion and freight plus exact moving-average holding, matching Phase 4. Bounded integer quantities allow exact binary linearization of quantity × unit value. A fixed-standard-cost model supplies a feasible warm start only. The configured holding basis is saved with each run.','',
      '| Case | Fulfilled | Unmet | Fill rate | Common-basis cost | Original moving-average cost |',
      '|---|---:|---:|---:|---:|---:|']
    for r in comparison['cases']:
        lines.append(f'| {r["case"]} | {r["fulfilled_units"]:,} | {r["unmet_units"]:,} | {100*r["unit_fill_rate"]:.2f}% | {money(r["common_basis_cost"])} | {money(r["legacy_moving_average_cost"])} |')
    lines+=['','## Cost-versus-service results','',
      '| Target | Solver status | Fulfilled | Unmet | Common-basis cost | Bound | Gap | Runtime | Replay |',
      '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for r in tradeoff:
        gap=f'{100*r["relative_gap"]:.6f}%' if r['relative_gap'] is not None else 'N/A'
        lines.append(f'| {100*r["target"]:.0f}% | {r["status"]} | {r.get("fulfilled_units","—")} | {r.get("unmet_units","—")} | {money(r["objective"])} | {money(r["best_bound"])} | {gap} | {r["runtime_seconds"]:.3f}s | {r.get("reconciliation","not executed")} |')
    lines+=['','OPTIMAL means CBC proved optimality within the exact moving-average model. OPTIMAL_FIXED_STANDARD_MODEL applies only when that optional accounting basis is configured. FEASIBLE_NOT_PROVEN is an incumbent without proof. Infeasible targets are never relaxed. Sunk firm commitments are included once in objective and bound; opening inventory cost is sunk and excluded from avoidable acquisition spending.','',
      '## Comparable benchmark and verified execution','',
      f'Benchmark representation: {comparison["benchmark_representation"]}. The Phase 4 expedited plan is fixed into the mathematical model to verify that the same allowed supplier, MOQ, route, inventory and 95% service constraints can represent it. It is also replayed without heuristic replanning.',
      'Every feasible solution is exported as plan.json and executed through the planning engine explicit-plan mode. Reconciliation compares every order, production, shipment, fulfilled quantity and weekly inventory variable, plus total objective and all constraint residuals. The same physical audit used in earlier phases checks stock, timing, labor, MOQ and firm receipts.',
      '', '## Service and horizon interpretation','',
      'The initial target is aggregate only. It permits concentrated shortages among products, warehouses or weeks. See service_by_group.csv and warehouse_inventory.csv in each target folder. Optional service_floors constrain selected products, warehouses or weeks; none are silently enabled.',
      'No terminal safety-stock minimum or future demand is imposed by default. Initial safety stock is consumable. The optimizer can spend down inventory and ignore heuristic replenishment targets. This is an intentional finite-horizon policy, not proof that operations recover beyond Week 8. Terminal stock and pending commitments are reported, and optional terminal floors can be configured explicitly.',
      'Purchases arriving after the horizon remain allowed so the benchmark remains representable, and are charged in full. Positive costs and no salvage credit discourage unnecessary purchases; MOQ can still create leftovers. Outstanding purchase quantities are not owned stock for holding cost. Dispatched finished goods in transit are owned and charged holding through the horizon.',
      'Suppliers are already qualified, and the existing single factory is fixed. Binary variables open weekly purchase orders, not new facilities. There are no supplier activation fees, alternative factories, substitute components, preparedness optimization or invented demand.',
      '', '## Detailed artifacts','',
      'Each target folder contains solver log, model.lp, solver status/bound/runtime, executable plan, full replay ledgers, cost reconciliation, standard and legacy accounting, terminal inventory, supplier-capacity usage and service detail. Comparison CSVs include cost components, incremental costs and potential sales revenue exposure (not profit). See ../FORMULATION.md, ../TEACHING.md and ../ANSWER_KEY.md.']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'phase5/config.json')
    parser.add_argument('--scenario',type=Path,default=ROOT/'phase3/scenarios/s01_capacity_loss.json')
    parser.add_argument('--output',type=Path,default=ROOT/'phase5/results')
    parser.add_argument('--extension-demand',type=Path)
    args=parser.parse_args()
    check(args.output.resolve()!=ROOT and not args.output.resolve().is_relative_to(ROOT/'data'),'Unsafe output directory')
    before=input_hashes();company=load_company(extension=args.extension_demand)
    config=json.loads(args.config.read_text());scenario=json.loads(args.scenario.read_text())
    data=ProblemData(company,scenario,config);args.output.mkdir(parents=True,exist_ok=True)
    bench_cfg=json.loads((ROOT/'phase4/strategies/expedited_transport.json').read_text())
    policy=Mitigation(bench_cfg,company,scenario['start_week']);_,empty=prepare(company,policy,D(config['annual_holding_cost_rate']))
    benchmark=simulate(company,data.effects,policy)
    references={'baseline':simulate(company),'disruption_no_action':simulate(company,data.effects),'phase4_expedited_benchmark':benchmark}
    comparison=dict(cases=[],benchmark_representation='not checked',config=config,scenario=scenario)
    for name,run in references.items():
        folder=args.output/name;write_result(run,folder)
        accounting=model_accounting(data,run);legacy=value_run(company,run,empty,D(config['annual_holding_cost_rate']))
        dump(folder/'common_accounting.json',accounting);dump(folder/'legacy_accounting.json',legacy)
        comparison['cases'].append(case_row(name,run,accounting,legacy))
    # Verify representability under precisely the initial model settings, not all trade-off targets.
    try:
        replayed=simulate(company,data.effects,explicit_plan=plan_from_run(benchmark),execution_config=config)
        bm=ExactMovingAverageModel(data) if config['holding_basis']=='moving_average_exact' else WeeklyModel(data)
        if isinstance(bm,ExactMovingAverageModel):bm.seed(replayed)
        bm.bind_benchmark(replayed);binfo=solve(bm,args.output/'benchmark_fixed_model')
        check(binfo['feasible_incumbent'],'Benchmark cannot satisfy model constraints')
        brec=reconcile(bm,replayed,binfo)
        dump(args.output/'benchmark_fixed_model/verification.json',dict(solver=binfo,reconciliation=brec))
        comparison['benchmark_representation']='PASS: fixed-model feasibility and independent replay'
    except ValueError as error:
        comparison['benchmark_representation']='NOT COMPARABLE: '+str(error)
    tradeoff=[]
    targets=sorted(set(config['service_targets_to_run']+[config['service_target']]))
    for target in targets:
        cfg=dict(config,service_target=target);d=ProblemData(company,scenario,cfg);model=WeeklyModel(d)
        folder=args.output/f'target_{round(target*100):03d}'
        if config['holding_basis']=='moving_average_exact':
            warm_data=ProblemData(company,scenario,dict(cfg,holding_basis='fixed_standard_value'))
            warm=WeeklyModel(warm_data);warm_info=solve(warm,folder/'warm_start')
            dump(folder/'warm_start/solver.json',warm_info)
            model=ExactMovingAverageModel(d)
            if warm_info['feasible_incumbent']:
                warm_run=simulate(company,d.effects,explicit_plan=warm.export_plan(),execution_config=cfg)
                reconcile(warm,warm_run,warm_info)
                model.seed(warm_run)
        info=solve(model,folder)
        row=dict(target=target,**info);dump(folder/'solver.json',info);dump(folder/'config.json',cfg)
        if info['feasible_incumbent']:
            plan=model.export_plan();dump(folder/'plan.json',plan)
            run=simulate(company,d.effects,explicit_plan=plan,execution_config=cfg)
            verification=reconcile(model,run,info);legacy=value_run(company,run,empty,D(config['annual_holding_cost_rate']))
            write_result(run,folder);dump(folder/'reconciliation.json',verification);dump(folder/'legacy_accounting.json',legacy)
            write_csv(folder/'service_by_group.csv',service_tables(run))
            write_csv(folder/'binding_capacities.csv',[r for r in run['supplier_capacity'] if r['released_units']==r['effective_capacity_units']])
            labor=[]
            for week in d.weeks:
                used=sum((D(r['produced_units'])*D(d.routes[r['product_id']]['hours_per_unit']) for r in run['production'] if r['week_start']==week),D(0))
                labor.append(dict(week_start=week,used_hours=used,available_hours=d.hours,remaining_hours=d.hours-used,binding=abs(d.hours-used)<D('0.00001')))
            write_csv(folder/'factory_capacity.csv',labor)
            write_csv(folder/'terminal_inventory.csv',[r for name in ['component_inventory','factory_inventory','warehouse_inventory'] for r in run[name] if r['week_start']==d.weeks[-1]])
            row.update(fulfilled_units=run['totals']['fulfilled_units'],unmet_units=run['totals']['unmet_units'],
                unit_fill_rate=run['totals']['unit_fill_rate'],legacy_moving_average_cost=legacy['gross_committed_resource_cost'],reconciliation='PASS')
            if target==config['service_target']:
                comparison['cases'].append(case_row('optimized_reactive',run,verification['accounting'],legacy))
                if comparison['benchmark_representation'].startswith('PASS') and info['proven_optimal']:
                    check(info['objective']<=float(model_accounting(data,benchmark)['total_committed_modeled_cost'])+0.001,
                          'A feasible comparable benchmark is cheaper than the proven optimum')
        else:
            # Prevent stale executable plans from a previous successful run being reused.
            for name in ['plan.json','result.json','reconciliation.json']:(folder/name).unlink(missing_ok=True)
        tradeoff.append(row)
        print(f'Target {target:.0%}: {info["status"]}, cost {info["objective"]}, replay {row.get("reconciliation","N/A")}')
    reference=comparison['cases'][1]['common_basis_cost'];bench=comparison['cases'][2]['common_basis_cost']
    for row in comparison['cases']:
        row['incremental_cost_vs_no_action']=row['common_basis_cost']-reference
        row['cost_difference_vs_comparable_benchmark']=row['common_basis_cost']-bench
    check(before==input_hashes(),'Original input files changed')
    comparison['source_integrity']=dict(status='PASS',before=before,after=input_hashes())
    dump(args.output/'comparison.json',comparison);write_csv(args.output/'comparison.csv',comparison['cases'])
    dump(args.output/'service_tradeoff.json',tradeoff);write_csv(args.output/'service_tradeoff.csv',tradeoff)
    report(comparison,tradeoff,args.output)
    print(f'Verified report: {args.output/"REPORT.md"}')


if __name__=='__main__':main()
