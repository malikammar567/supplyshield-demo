"""Run Phase 4: python3 phase4/run_mitigations.py."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from phase2.engine import load_company,write_result,serializable,input_hashes,check
from phase4.comparison import execute_comparison


def dump(path,value):
    path.write_text(json.dumps(serializable(value),indent=2,allow_nan=False)+'\n')


def write_csv(path,rows):
    if not rows:
        path.unlink(missing_ok=True)
        return
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def write_report(comparison,output):
    rows=comparison['rows']; target=comparison['settings']['target_unit_fill_rate']
    money=lambda v:f'-${abs(v):,.2f}' if v<0 else f'${v:,.2f}'
    rate=lambda v:f'{100*v:.2f}%' if v is not None else 'N/A'
    labels={'baseline':'Baseline (no disruption)','disruption_no_action':'Disruption: no action',
            'alternate_supplier':'Alternate supplier','expedited_transport':'Expedited transport',
            'prepared_inventory':'Prepared inventory','alternate_plus_expedite':'Alternate + expedited',
            'prepared_plus_alternate':'Prepared + alternate'}
    lines=['# Phase 4: verified mitigation comparison','',f'Target unit fill rate: **{100*target:.2f}%**. Original horizon and company data are preserved. All route and preparedness assumptions are synthetic and configurable.','',
           '| Case | Mode | Fulfilled | Unmet | Fill rate | Improvement vs no action | Shortage reduction | Meets target |',
           '|---|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        label=labels.get(r['strategy_id'],r['strategy_id'])
        if r['status']!='FEASIBLE':
            lines.append(f'| {label} | {r["status"]} | — | — | — | — | — | No |')
            continue
        pp=r['fill_rate_improvement_percentage_points']; pct=r['shortage_reduction_percentage']
        lines.append(f'| {label} | {r["mode"]} | {r["fulfilled_units"]:,} | {r["unmet_units"]:,} | {rate(r["unit_fill_rate"])} | {pp:+.2f} pp | {r["shortage_reduction_units"]:,} ({pct:.2f}%) | {"Yes" if r["meets_target"] else "No"} |' if pp is not None and pct is not None else
                     f'| {label} | {r["mode"]} | {r["fulfilled_units"]:,} | {r["unmet_units"]:,} | {rate(r["unit_fill_rate"])} | N/A | N/A | {"Yes" if r["meets_target"] else "No"} |')
    lines+=['','**'+comparison['ranking']['target_statement']+'**','',
            'This is the lowest-cost qualifying option among the tested configurations, not a global optimum. Baseline and no action are reference cases, not mitigation candidates. Physical feasibility is distinct from meeting the service target.','',
            '## Consistent cost comparison','',
            'Ranking uses gross committed resource cost: all preparation acquisition, firm and new purchase/freight commitments (including pending orders), conversion, dispatched outbound freight and holding. No resale or salvage credit is assumed. Remaining stock and outstanding supplies are shown separately below. Potential sales revenue exposure is not lost profit.','',
            '| Case | Committed purchases¹ | Conversion | Committed transportation¹ | Preparation acquisition | Holding² | Ranking cost | Increment vs no action | Revenue exposure |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['status']!='FEASIBLE':continue
        cells=[money(r[k]) for k in ['committed_purchasing_cost','conversion_cost','committed_transportation_cost','preparation_acquisition_cost','total_holding_cost','gross_committed_resource_cost','incremental_modeled_cost_vs_no_action','potential_sales_revenue_exposure']]
        lines.append('| '+labels.get(r['strategy_id'],r['strategy_id'])+' | '+' | '.join(cells)+' |')
    lines+=['','¹ Excludes preparation acquisition, shown separately. Includes outstanding horizon PO commitments, so receipt delays do not masquerade as savings. ² Includes preparation holding plus horizon holding at the same 20% annual synthetic rate (unless changed in comparison_config.json).','',
            '## Terminal position and service gaps','',
            '| Case | Ending owned inventory value | Outstanding PO value | Prepared acquisition value still in stock | First shortage | Affected products | Target gap (units) |',
            '|---|---:|---:|---:|---|---|---:|']
    for r in rows:
        if r['status']!='FEASIBLE':continue
        lines.append(f'| {labels.get(r["strategy_id"],r["strategy_id"])} | {money(r["terminal_owned_inventory_value"])} | {money(r["pending_purchase_and_inbound_commitment"])} | {money(r["prepared_acquisition_value_remaining"])} | {r["first_shortage_week"] or "none"} | {r["affected_products"] or "none"} | {r["target_shortfall_units"]} |')
    lines+=['','Terminal stock is valued using the documented moving-average cost replay, not a promised realizable value. Original stock uses explicit baseline cost proxies. Prepared value follows purchased components into unsold finished goods; it is not added a second time to total inventory.','',
            '## Validity and boundaries','',
            '- Zero-action mitigation reproduces the disrupted reference exactly: '+comparison['zero_action_check']+'.',
            '- Supplier/component capacity is reserved for original allocations before alternate orders. Consolidated orders obey MOQ and qualification restrictions.',
            '- Inbound expediting preserves two assumed processing weeks for S01. Outbound zero-week transit means delivery within the production week before end-week demand; it is an explicit synthetic assumption.',
            '- Preparation runs six weeks before the snapshot for the saved demonstrations. Extra stock is purchased and arrives before use; capacity reservations, inferred firm release weeks and holding costs are recorded.',
            '- Flow and valuation audits reconcile physical stock, pipelines, material usage, labor, freight, outstanding orders and prepared acquisition value.',
            '- Cost ranking is a committed-resource budget comparison, not profit, cash flow or optimization. Missing fees, overhead, obsolescence and true salvage values could change the ranking.',
            '- The eight-week horizon does not establish full recovery or eliminate later risk. Extend only with explicit demand assumptions.',
            '', '## Detailed files','',
            '`comparison.csv` and `comparison.json` contain complete metrics and ranking. Each case folder has the engine ledgers, weekly comparison, first shortages by warehouse/product, capacity limits, bottlenecks, valuation/holding ledgers, terminal stock and outstanding orders. Prepared cases also contain preparation orders, capacity, inventory and costs.','',
            'See `../METHODOLOGY.md`, `../TEACHING.md` and `../ANSWER_KEY.md`. All exercises are optional.']
    failed=[r for r in rows if r['status']!='FEASIBLE']
    if failed:
        lines+=['','## Invalid or infeasible candidates','']+[f'- {r["strategy_id"]}: {r["status"]}: {r["reason"]}' for r in failed]
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',type=Path,default=ROOT/'phase3/scenarios/s01_capacity_loss.json')
    parser.add_argument('--strategy',type=Path,help='One strategy instead of all saved strategies')
    parser.add_argument('--settings',type=Path,default=ROOT/'phase4/comparison_config.json')
    parser.add_argument('--target-fill-rate',type=float,help='Fraction from 0 through 1; e.g. 0.95')
    parser.add_argument('--extension-demand',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'phase4/results')
    args=parser.parse_args()
    check(args.output.resolve()!=ROOT and not args.output.resolve().is_relative_to(ROOT/'data'),'Choose a results directory outside original company inputs')
    before=input_hashes()
    scenario=json.loads(args.scenario.read_text()); settings=json.loads(args.settings.read_text())
    if args.target_fill_rate is not None:settings['target_unit_fill_rate']=args.target_fill_rate
    paths=[args.strategy] if args.strategy else sorted((ROOT/'phase4/strategies').glob('*.json'))
    configs=[json.loads(p.read_text()) for p in paths]
    company=load_company(extension=args.extension_demand)
    comparison,artifacts=execute_comparison(company,scenario,configs,settings)
    args.output.mkdir(parents=True,exist_ok=True)
    provenance={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.scenario,args.settings,*paths]}
    comparison['provenance']=dict(company_sha256=before,configuration_sha256=provenance,target_override=args.target_fill_rate)
    dump(args.output/'comparison.json',comparison); write_csv(args.output/'comparison.csv',comparison['rows'])
    reference=artifacts['disruption_no_action']['run']
    for sid,artifact in artifacts.items():
        folder=args.output/sid; folder.mkdir(parents=True,exist_ok=True)
        dump(folder/'strategy_config.json',artifact['config'])
        prep=artifact['preparation']; dump(folder/'preparation.json',prep)
        for key in ['orders','capacity','inventory','extra_inventory']:
            write_csv(folder/f'preparation_{key}.csv',prep[key])
        if artifact['run'] is None:
            # A failed rerun must not leave a previous successful simulation presented as current.
            generated=['component_inventory','factory_inventory','warehouse_inventory','production',
                'supplier_capacity','receipts','weekly','material_consumption','order_pipeline',
                'shipment_pipeline','orders','shipments','pending_orders','pending_shipments',
                'valuation_weekly','valuation_inventory_values','valuation_terminal_inventory',
                'valuation_terminal_transfers','first_shortages','weekly_comparison',
                'capacity_constraints','production_bottlenecks']
            for name in generated:(folder/f'{name}.csv').unlink(missing_ok=True)
            for name in ['result.json','summary.json','valuation.json']:(folder/name).unlink(missing_ok=True)
            dump(folder/'status.json',dict(status='INFEASIBLE_PREPARATION',reasons=prep['reasons']))
            continue
        dump(folder/'status.json',dict(status='FEASIBLE',physical_checks='PASS',financial_checks='PASS'))
        run=artifact['run']; run['metadata']['phase4_provenance']=comparison['provenance']
        write_result(run,folder)
        values=artifact['valuation']; dump(folder/'valuation.json',values)
        for key in ['weekly','inventory_values','terminal_inventory','terminal_transfers']:
            write_csv(folder/f'valuation_{key}.csv',values[key])
        first=[]
        for wh,p in sorted({(r['warehouse_id'],r['product_id']) for r in run['warehouse_inventory']}):
            selected=[r for r in run['warehouse_inventory'] if r['warehouse_id']==wh and r['product_id']==p]
            first.append(dict(warehouse_id=wh,product_id=p,unmet_units=sum(r['unmet_units'] for r in selected),
                              first_shortage_week=next((r['week_start'] for r in selected if r['unmet_units']),None)))
        write_csv(folder/'first_shortages.csv',first)
        weekly=[]
        for row,base,cost in zip(run['weekly'],reference['weekly'],values['weekly']):
            weekly.append(dict(row,holding_cost=cost['holding_cost'],no_action_unmet_units=base['unmet_units'],
                shortage_reduction_units=base['unmet_units']-row['unmet_units'],
                fill_rate_improvement_percentage_points=100*(row['unit_fill_rate']-base['unit_fill_rate']) if row['unit_fill_rate'] is not None and base['unit_fill_rate'] is not None else None))
        write_csv(folder/'weekly_comparison.csv',weekly)
        write_csv(folder/'capacity_constraints.csv',[r for r in run['supplier_capacity'] if r['requested_but_not_released_units']>0 or r['remaining_capacity_units']==0])
        write_csv(folder/'production_bottlenecks.csv',[r for r in run['production'] if r['unmade_requested_units']>0])
    check(input_hashes()==before,'Original company data changed')
    dump(args.output/'input_integrity.json',dict(status='PASS',before=before,after=input_hashes()))
    write_report(comparison,args.output)
    for r in comparison['rows']:
        if r['status']=='FEASIBLE':
            print(f'{r["strategy_id"]}: fulfilled {r["fulfilled_units"]}/{r["demand_units"]}, unmet {r["unmet_units"]}; cost ${r["gross_committed_resource_cost"]:,.2f}; target {r["meets_target"]}')
        else:print(f'{r["strategy_id"]}: {r["status"]}: {r["reason"]}')
    print(comparison['ranking']['target_statement'])
    print(f'Input integrity PASS. Report: {args.output/"REPORT.md"}')
    return 1 if any(r['status']!='FEASIBLE' for r in comparison['rows']) else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except ValueError as error:raise SystemExit(f'Invalid configuration or failed verification: {error}')
