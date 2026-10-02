"""Execute finite mitigation candidates and rank on an explicit consistent cost basis."""
import copy
from decimal import ROUND_CEILING
from phase2.engine import D, ZERO, simulate, check
from phase3.scenarios import ScenarioEffects
from phase4.strategies import Mitigation, fields, number
from phase4.preparation import prepare
from phase4.valuation import value_run


def evaluation_settings(config):
    fields(config,'target_unit_fill_rate annual_holding_cost_rate ranking_measure assumptions','comparison settings')
    target=number(config['target_unit_fill_rate'],'target_unit_fill_rate',1)
    rate=number(config['annual_holding_cost_rate'],'annual_holding_cost_rate')
    check(config['ranking_measure']=='gross_committed_resource_cost','Unsupported ranking measure')
    check(isinstance(config['assumptions'],str) and bool(config['assumptions'].strip()),'Cost assumptions are required')
    return target,rate


def rank_candidates(rows):
    feasible=sorted((r for r in rows if r['status']=='FEASIBLE' and r['role']=='strategy'),
                    key=lambda r:(r['gross_committed_resource_cost'],r['strategy_id']))
    for i,row in enumerate(feasible,1): row['cost_rank_all_feasible_tested']=i
    qualifying=[r for r in feasible if r['meets_target']]
    for i,row in enumerate(qualifying,1): row['cost_rank_target_meeting_tested']=i
    return dict(lowest_cost_tested_strategy=feasible[0]['strategy_id'] if feasible else None,
                lowest_cost_tested_strategy_meeting_target=qualifying[0]['strategy_id'] if qualifying else None,
                target_statement=('Lowest-cost tested strategy meeting the target: '+qualifying[0]['strategy_id']
                                  if qualifying else 'No feasible tested mitigation strategy meets the target.'),
                optimization_claim='Finite configured candidates only; not a globally optimal solution.')


def summary_row(sid,role,mode,run,values,no_action,no_action_values,target):
    totals=run['totals']; reference=no_action['totals']
    fulfilled,unmet,demand=(totals[k] for k in ['fulfilled_units','unmet_units','demand_units'])
    reduction=reference['unmet_units']-unmet
    needed=int((target*demand).to_integral_value(rounding=ROUND_CEILING))
    first=next((r['week_start'] for r in run['weekly'] if r['unmet_units']),None)
    affected=sorted({r['product_id'] for r in run['warehouse_inventory'] if r['unmet_units']})
    affected_wh=sorted({r['warehouse_id'] for r in run['warehouse_inventory'] if r['unmet_units']})
    row=dict(strategy_id=sid,role=role,mode=mode,status='FEASIBLE',reason='',demand_units=demand,
        fulfilled_units=fulfilled,unmet_units=unmet,unit_fill_rate=totals['unit_fill_rate'],
        fill_rate_improvement_percentage_points=(100*(totals['unit_fill_rate']-reference['unit_fill_rate'])
              if totals['unit_fill_rate'] is not None and reference['unit_fill_rate'] is not None else None),
        shortage_reduction_units=reduction,shortage_reduction_percentage=(100*reduction/reference['unmet_units'] if reference['unmet_units'] else None),
        first_shortage_week=first,affected_products=';'.join(affected),affected_warehouses=';'.join(affected_wh),
        potential_sales_revenue_exposure=totals['potential_sales_revenue_exposure'],
        target_unit_fill_rate=target,meets_target=totals['unit_fill_rate'] is not None and fulfilled>=needed,
        target_shortfall_units=max(0,needed-int(fulfilled)),
        target_shortfall_percentage_points=max(ZERO,100*(target-totals['unit_fill_rate'])) if totals['unit_fill_rate'] is not None else None,
        cost_rank_all_feasible_tested=None,cost_rank_target_meeting_tested=None,
        supplier_capacity_limited_rows=sum(r['requested_but_not_released_units']>0 for r in run['supplier_capacity']),
        production_bottleneck_rows=sum(r['unmade_requested_units']>0 for r in run['production']),
        pending_order_units=sum(o['quantity_units'] for o in run['pending_orders']),
        physical_checks=run['checks']['status'],financial_checks=values['financial_reconciliation'])
    row.update({k:v for k,v in values.items() if not isinstance(v,list)})
    row['incremental_modeled_cost_vs_no_action']=values['gross_committed_resource_cost']-no_action_values['gross_committed_resource_cost']
    return row


def execute_comparison(company,scenario,configs,settings):
    target,rate=evaluation_settings(settings)
    effects=ScenarioEffects(scenario,company)
    original=copy.deepcopy(company)
    control=dict(strategy_id='internal_control',mode='reactive',start_week=scenario['start_week'],
        duration_weeks=1,reallocation=[],expedited_routes=[],preparation=None,assumptions='Unchanged control')
    _,empty=prepare(company,Mitigation(control,company,scenario['start_week']),rate)
    baseline=simulate(company)
    no_action=simulate(company,effects)
    no_values=value_run(company,no_action,empty,rate)
    artifacts={}
    rows=[]
    for sid,run,values in [('baseline',baseline,value_run(company,baseline,empty,rate)),('disruption_no_action',no_action,no_values)]:
        artifacts[sid]=dict(run=run,preparation=empty,valuation=values,config=None)
        rows.append(summary_row(sid,'reference','reference',run,values,no_action,no_values,target))
    seen={'baseline','disruption_no_action'}
    zero_check='not supplied'
    for index,config in enumerate(configs):
        try:
            policy=Mitigation(config,company,scenario['start_week'])
        except (ValueError,TypeError,KeyError,OverflowError) as error:
            rows.append(dict(strategy_id=f'invalid_config_{index+1}',role='strategy',mode='unknown',
                             status='INVALID_CONFIGURATION',reason=str(error),meets_target=False))
            continue
        sid=policy.config['strategy_id']
        check(sid not in seen,'Duplicate strategy ID: '+sid)
        seen.add(sid)
        variant,prep=prepare(company,policy,rate)
        if not prep['feasible']:
            rows.append(dict(strategy_id=sid,role='strategy',mode=policy.config['mode'],status='INFEASIBLE_PREPARATION',
                             reason='; '.join(prep['reasons']),meets_target=False))
            artifacts[sid]=dict(run=None,preparation=prep,valuation=None,config=config)
            continue
        run=simulate(variant,effects,policy)
        values=value_run(company,run,prep,rate)
        artifacts[sid]=dict(run=run,preparation=prep,valuation=values,config=config)
        if not config['reallocation'] and not config['expedited_routes'] and config['preparation'] is None:
            check(run==no_action,'Zero-action mitigation differs from disrupted reference')
            zero_check='PASS'
            continue  # Save audit artifact but do not duplicate/rank a no-action control.
        rows.append(summary_row(sid,'strategy',config['mode'],run,values,no_action,no_values,target))
    ranking=rank_candidates(rows)
    check(company==original,'Mitigation comparison mutated company inputs')
    return dict(rows=rows,ranking=ranking,settings=settings,scenario=scenario,zero_action_check=zero_check,
                company_unchanged=True),artifacts
