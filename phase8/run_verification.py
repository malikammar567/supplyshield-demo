"""Real second-company upload → engines → optimizer → free analyst → exports."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from phase2.engine import input_hashes
from phase6 import datasets,services as api
from phase7 import analyst,providers

def workflow():
    before=input_hashes();raw=(ROOT/'phase8/assets/beacon_company.zip').read_bytes()
    preview=datasets.inspect_upload(raw)
    if preview['candidate'] is None:raise ValueError(preview['report'])
    ds=preview['candidate'];base=api.baseline(ds);scenario=api.default_scenario(ds)
    case=api.scenario_run(ds,scenario);mc,opt=api.defaults(ds,scenario)
    mitigation=api.mitigation_run(ds,mc)
    opt.update(solver_time_limit_seconds=10,service_target=.95,service_targets_to_run=[.95])
    optimized=api.optimization_run(ds,dict(scenario=scenario,optimization=opt))
    assert optimized['run'] is not None and optimized['verification']['status']=='PASS',optimized['solver']
    ctx=analyst.context(ds,scenario);question='What happens if supplier NORTH loses 60% of capacity for four weeks starting in the first planning week?'
    request,mode,note=providers.interpret(providers.MODES[0],ds,question,ctx)
    ctx,answer,trace=analyst.execute(ds,request,ctx)
    assert answer['run']['totals']==case['run']['totals']
    trace.update(question=question,active_mode=mode,note=note)
    followup=analyst.parse(ds,'What about warehouse WEST?',ctx);_,_,warehouse_trace=analyst.execute(ds,followup,ctx)
    assert warehouse_trace['citations']['selected.warehouse_inventory']
    assert input_hashes()==before
    return ds,dict(baseline=base,scenario=case,mitigation=mitigation,optimization=optimized),trace

def main():
    ds,results,trace=workflow();out=ROOT/'phase8/results';out.mkdir(exist_ok=True)
    ctx=analyst.context(ds,results['optimization']['config']['scenario']);ctx['latest']=results['optimization']
    request=analyst.parse(ds,'Explain the optimized plan',ctx)
    _,_,optimization_trace=analyst.execute(ds,request,ctx)
    optimization_trace.update(active_mode=providers.MODES[0],question='Explain the optimized plan',note='Retrieved the verified optimization result from this workflow; no model used.')
    for name,result in results.items():
        (out/(name+'.json')).write_bytes(api.json_bytes(result));(out/(name+'.zip')).write_bytes(api.export_zip(result))
    (out/'dataset_profile.json').write_bytes(api.json_bytes(ds['profile']))
    (out/'validation_report.json').write_bytes(api.json_bytes(ds['validation_report']))
    (out/'dataset_assumptions.json').write_bytes(api.json_bytes(ds['profile']['assumptions']))
    (out/'analyst_trace.json').write_bytes(api.json_bytes(trace))
    (out/'optimization_analyst_trace.json').write_bytes(api.json_bytes(optimization_trace))
    lines=['# Beacon Instruments calculated results','All data are synthetic. No provider or model was used.','', '| Case | Demand | Fulfilled | Unmet | Fill rate | Committed cost USD |','|---|---:|---:|---:|---:|---:|']
    for name in ['baseline','scenario','optimization']:
        r=results[name];t=r['run']['totals'];v=r['valuation'];lines.append(f"| {name} | {t['demand_units']} | {t['fulfilled_units']} | {t['unmet_units']} | {100*t['unit_fill_rate']:.2f}% | {v['gross_committed_resource_cost']:.2f} |")
    lines+=['','## Mitigation comparison',api.report(results['mitigation']),'','## Optimization status',api.report(results['optimization']),'','## Free analyst answer',trace['explanation']]
    (out/'EXECUTIVE_REPORT.md').write_text('\n'.join(lines))
    print('\n'.join(lines[:9]));print(results['optimization']['solver']['status']);print('Independent replay:',results['optimization']['verification']['status'])

if __name__=='__main__':main()
