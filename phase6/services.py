"""Application adapters. All planning, valuation and optimization stay in Phases 2–5."""
import copy,csv,io,json,tempfile,zipfile
from datetime import datetime,timezone
from pathlib import Path
from phase2.engine import ROOT,D,simulate,serializable,check
from phase3.scenarios import ScenarioEffects
from phase3.comparison import compare
from phase4.comparison import execute_comparison
from phase4.valuation import value_run
from phase6.datasets import digest

LIMITATIONS='Unit fill rate is fulfilled units / demand, not OTIF or stockout probability. Lower disruption cost may reflect less production, not savings. Revenue exposure is not lost profit. Terminal inventory and outstanding commitments affect comparisons. Aggregate service may hide product/warehouse shortages. Solver conclusions apply only to configured actions, horizon and assumptions. Firm receipts are preserved. No future demand is invented.'
EMPTY=dict(extra_inventory=[],holding_cost=D(0),acquisition_cost=D(0),material_acquisition_cost=D(0),inbound_acquisition_cost=D(0))


def key(ds,kind,config):return digest(dict(dataset=ds['id'],content_hash=digest(ds['company']),schema_version=ds.get('schema_version','1.0'),kind=kind,config=config))


def envelope(ds,kind,config,**payload):
    from phase8.contract import profile
    from phase9.provenance import execution
    p=profile(ds)
    return dict(key=key(ds,kind,config),dataset_id=ds['id'],dataset_name=ds['name'],schema_version=ds.get('schema_version','1.0'),configuration_hash=p['configuration_hash'],request_configuration_hash=digest(config),kind=kind,config=copy.deepcopy(config),
                created_at=datetime.now(timezone.utc).isoformat(),limitations=LIMITATIONS,dataset_profile=p,dataset_assumptions=p['assumptions'],validation_report=ds.get('validation_report',dict(status='PASS',source='Bundled validated demo / programmatic company')),**execution(kind),**payload)


def current(result,ds,kind,config):return bool(result and result['key']==key(ds,kind,config))


def valued(company,run,rate=.2):return value_run(company,run,EMPTY,D(rate))


def baseline(ds):
    run=simulate(ds['company'])
    return envelope(ds,'baseline',dict(annual_holding_cost_rate=.2),run=run,valuation=valued(ds['company'],run))


def scenario_run(ds,config):
    c=ds['company'];effects=ScenarioEffects(config,c)
    base=simulate(c);run=simulate(c,effects)
    return envelope(ds,'scenario',config,run=run,baseline=base,comparison=compare(base,run),valuation=valued(c,run),baseline_valuation=valued(c,base))


def mitigation_run(ds,config):
    comparison,artifacts=execute_comparison(ds['company'],config['scenario'],config['strategies'],config['settings'])
    return envelope(ds,'mitigation',config,comparison=comparison,artifacts=artifacts)


def optimization_run(ds,config,progress=lambda message:None):
    from phase5.data import ProblemData
    from phase5.formulation import WeeklyModel
    from phase5.exact_formulation import ExactMovingAverageModel
    from phase5.solver import solve
    from phase5.reconcile import reconcile
    c=ds['company'];scenario=config['scenario'];settings=config['optimization']
    check(settings['holding_basis']=='moving_average_exact','The application compares exact Phase 4 moving-average costs only.')
    d=ProblemData(c,scenario,settings)
    with tempfile.TemporaryDirectory(prefix='resilience-solver-') as directory:
        path=Path(directory)
        progress('Finding a feasible starting plan…')
        warm=WeeklyModel(ProblemData(c,scenario,dict(settings,holding_basis='fixed_standard_value')))
        winfo=solve(warm,path/'warm')
        progress('Solving with exact moving-average holding costs…')
        model=ExactMovingAverageModel(d)
        if winfo['feasible_incumbent']:
            wrun=simulate(c,d.effects,explicit_plan=warm.export_plan(),execution_config=settings)
            reconcile(warm,wrun,winfo);model.seed(wrun)
        info=solve(model,path/'exact')
        result=envelope(ds,'optimization',config,solver=info,warm_solver=winfo,run=None,plan=None,verification=None)
        result['solver_log']=(path/'exact/cbc.log').read_text()
        result['model_lp']=(path/'exact/model.lp').read_text()
        if info['feasible_incumbent']:
            progress('Independently replaying quantities, arrivals, inventory and costs…')
            plan=model.export_plan();run=simulate(c,d.effects,explicit_plan=plan,execution_config=settings)
            verification=reconcile(model,run,info)
            result.update(plan=plan,run=run,verification=verification,valuation=valued(c,run,settings['annual_holding_cost_rate']))
        return result


def default_scenario(ds):
    c=ds['company'];weeks=c['tables']['calendar']
    return dict(scenario_id='interactive_scenario',type='supplier_capacity_loss',start_week=weeks[0]['week_start'],
        duration_weeks=min(4,len(weeks)),existing_firm_orders_policy='preserve_scheduled_receipts',
        supplier_id=c['tables']['suppliers'][0]['supplier_id'],capacity_loss_percentage=60)


def defaults(ds,scenario):
    """Demo assumptions only for the exact demo fingerprint. Uploads need explicit route/preparation assumptions."""
    from phase6.datasets import demo
    c=ds['company'];weeks=[r['week_start'] for r in c['tables']['calendar']];start=scenario['start_week']
    settings=json.loads((ROOT/'phase4/comparison_config.json').read_text())
    if ds['id']==demo()['id']:
        strategies=[json.loads(p.read_text()) for p in sorted((ROOT/'phase4/strategies').glob('*.json'))]
        for s in strategies:s.update(start_week=start,duration_weeks=len(weeks)-weeks.index(start))
        routes=json.loads((ROOT/'phase5/config.json').read_text())['expedited_routes']
    else:
        offers=c['tables']['supplier_offers'];source=scenario.get('supplier_id',c['tables']['suppliers'][0]['supplier_id'])
        rules=[]
        for offer in offers:
            if offer['supplier_id']!=source:continue
            alternatives=sorted({o['supplier_id'] for o in offers if o['component_id']==offer['component_id'] and o['supplier_id']!=source})
            if alternatives:rules.append(dict(component_id=offer['component_id'],from_supplier_id=source,alternate_supplier_ids=alternatives))
        strategies=[dict(strategy_id='qualified_alternates',mode='reactive',start_week=start,duration_weeks=len(weeks)-weeks.index(start),
            reallocation=rules,expedited_routes=[],preparation=None,assumptions='Qualified offers only. No expedited lanes or preparedness assumptions supplied.')]
        routes=[]
    opt=json.loads((ROOT/'phase5/config.json').read_text())
    opt.update(response_start_week=start,response_duration_weeks=len(weeks)-weeks.index(start),expedited_routes=routes)
    return dict(scenario=scenario,strategies=strategies,settings=settings),opt


def group_service(run):
    rows=[]
    for kind,fields in [('product',['product_id']),('warehouse',['warehouse_id']),('week',['week_start']),('product / warehouse',['product_id','warehouse_id'])]:
        groups={}
        for r in run['warehouse_inventory']:
            k=tuple(r[f] for f in fields);g=groups.setdefault(k,dict(demand=0,fulfilled=0,unmet=0,first_shortage=None))
            g['demand']+=r['demand_units'];g['fulfilled']+=r['fulfilled_units'];g['unmet']+=r['unmet_units']
            if r['unmet_units'] and g['first_shortage'] is None:g['first_shortage']=r['week_start']
        for k,g in groups.items():rows.append(dict(group=kind,selection=' / '.join(k),**g,fill_rate=g['fulfilled']/g['demand'] if g['demand'] else None))
    return rows


def json_bytes(value):
    from phase9.provenance import sanitize
    return json.dumps(sanitize(serializable(value)),indent=2,allow_nan=False).encode()


def csv_bytes(rows):
    s=io.StringIO();fields=list(dict.fromkeys(k for r in rows for k in r))
    if fields:
        writer=csv.DictWriter(s,fieldnames=fields);writer.writeheader()
        # Protect spreadsheet consumers from formulas contained in uploaded IDs.
        for row in serializable(rows):
            writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})
    return s.getvalue().encode()


def report(result):
    lines=['# SupplyShield executive run report',f"Execution ID: {result.get('run_id','legacy run')}",f"Engine version: {result.get('engine_version','legacy')}",f"Engine source hash: {result.get('engine_source_hash','not recorded')}",f"Schema: {result.get('schema_version','legacy')}",f"Model/policy hash: {result.get('configuration_hash','not recorded')}",f"Request hash: {result.get('request_configuration_hash','not recorded')}",f"Dataset: {result['dataset_name']}",f"Dataset fingerprint: {result['dataset_id']}",f"Run fingerprint: {result['key']}",f"Created: {result['created_at']}",f"Type: {result['kind']}",'',LIMITATIONS,'',
           'All costs are USD. Committed modeled cost includes firm and new orders (including pending), conversion, freight and moving-average holding. Opening owned stock is sunk. Holding defaults to a synthetic 20% annual rate; see the saved configuration. Prepared inventory is proactive and includes acquisition and preparation holding costs.']
    if result.get('solver'):
        s=result['solver'];lines += ['',f"Solver status: {s['status']}; proven optimal: {s['proven_optimal']}; runtime: {s['runtime_seconds']:.2f}s; bound: {s['best_bound']}; relative gap: {s['relative_gap']}",f"Replay: {result['verification']['status'] if result.get('verification') else 'No verified plan'}"]
    if result.get('run'):
        t=result['run']['totals'];v=result['valuation']
        lines += ['',f"Demand {t['demand_units']}; fulfilled {t['fulfilled_units']}; unmet {t['unmet_units']}; fill rate {t['unit_fill_rate']}",f"Committed modeled cost: ${v['gross_committed_resource_cost']:,.2f}",f"Ending owned inventory value: ${v['terminal_owned_inventory_value']:,.2f}; outstanding PO commitment: ${v['pending_purchase_and_inbound_commitment']:,.2f}"]
    if result['kind']=='mitigation':lines+=['',result['comparison']['ranking']['target_statement']]
    lines+=['','## Configuration and assumptions','```json',json_bytes(result['config']).decode(),'```']
    from phase9.provenance import redact
    return redact('\n'.join(lines))


def export_zip(result):
    stream=io.BytesIO()
    class StableZip(zipfile.ZipFile):
        def writestr(self,name,data,*args,**kwargs):
            from phase9.provenance import redact
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            if not name.endswith('.json'):
                if isinstance(data,bytes):data=redact(data.decode()).encode()
                else:data=redact(data)
            return super().writestr(info,data,*args,**kwargs)
    with StableZip(stream,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json_bytes({k:result.get(k) for k in ['run_id','project_version','engine_version','engine_source_hash','engine_source_files','execution_origin','key','dataset_id','dataset_name','schema_version','configuration_hash','request_configuration_hash','kind','created_at']}))
        z.writestr('configuration.json',json_bytes(result['config']));z.writestr('REPORT.md',report(result))
        def put(name,value):
            if isinstance(value,dict):
                z.writestr(name+'.json',json_bytes(value))
                for k,v in value.items():
                    if isinstance(v,list) and v and isinstance(v[0],dict):z.writestr(name+'_'+k+'.csv',csv_bytes(v))
        for name in ['dataset_profile','dataset_assumptions','validation_report','run','valuation','baseline','comparison','verification','solver','plan']:
            if result.get(name) is not None:put(name,result[name])
        if result.get('run'):z.writestr('service_by_group.csv',csv_bytes(group_service(result['run'])))
        for i,(sid,artifact) in enumerate(result.get('artifacts',{}).items()):
            # ZIP path is generated, never interpolated from uploaded identifiers.
            z.writestr(f'cases/{i}/identity.json',json_bytes(dict(strategy_id=sid)))
            for name in ['run','valuation','preparation','config']:put(f'cases/{i}/{name}',artifact[name])
        for name in ['solver_log','model_lp']:
            if name in result:z.writestr(name+'.txt',result[name])
    return stream.getvalue()
