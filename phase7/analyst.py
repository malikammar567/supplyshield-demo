"""Deterministic, supervised analyst. No model or network needed."""
import copy,json,re
from phase2.engine import D,check
from phase3.scenarios import ScenarioEffects
from phase5.data import ProblemData
from phase6 import services as api

TOOLS={'baseline','scenario','mitigation','optimization','information','results','assumptions','vulnerability'}
METRICS={'summary','cost','fill_rate','bottlenecks','plan','assumptions'}

class Clarify(ValueError):pass

def context(ds,scenario):return dict(dataset_id=ds['id'],scenario=copy.deepcopy(scenario),latest=None,previous=None)

def validate(ds,request):
    check(isinstance(request,dict) and set(request)=={'tool','arguments'},'Expected tool and arguments only.')
    tool=request['tool'];a=request['arguments']
    check(tool in TOOLS and isinstance(a,dict),'Unsupported analyst tool.')
    fields={'baseline':set(),'scenario':{'scenario'},'mitigation':{'scenario','target'},'optimization':{'scenario','target'},'information':{'table','id'},'results':{'metric','product_id','warehouse_id'},'assumptions':set(),'vulnerability':{'duration','start_week'}}
    check(set(a)==fields[tool],'Unexpected or missing tool arguments.')
    if 'scenario' in a:
        ScenarioEffects(a['scenario'],ds['company'])
        check(a['scenario']['duration_weeks']<=520,'Analyst duration limit is 520 weeks.')
        for field in ['demand_increase_percentage','cost_increase_percentage']:
            if field in a['scenario']:check(a['scenario'][field]<=10000,'Analyst increase limit is 10,000 percent.')
    if 'target' in a:
        check(type(a['target']) in (float,int) and D(a['target']).is_finite() and 0<=a['target']<=1,'Target must be a fraction from zero through one.')
        if tool=='optimization':
            _,cfg=api.defaults(ds,a['scenario']);cfg.update(service_target=a['target'],service_targets_to_run=[a['target']]);ProblemData(ds['company'],a['scenario'],cfg)
    if tool=='results':
        check(a['metric'] in METRICS,'Unsupported metric.')
        for field,table,idfield in [('product_id','items','item_id'),('warehouse_id','facilities','facility_id')]:
            allowed={r[idfield] for r in ds['company']['tables'][table] if r.get('item_type')=='finished_good' or r.get('facility_type')=='warehouse'}
            check(a[field] is None or a[field] in allowed,'Unknown '+field)
    if tool=='information':
        check(a['table'] in {'suppliers','items','facilities','inventory','supplier_offers'},'Unsupported information table.')
        check(a['id'] is None or any(a['id'] in r.values() for r in ds['company']['tables'][a['table']]),'Unknown information ID.')
    if tool=='vulnerability':
        check(type(a['duration']) is int and 1<=a['duration']<=520,'Duration must be a positive integer up to 520.')
        check(a['start_week'] in [r['week_start'] for r in ds['company']['tables']['calendar']],'Start week must be in the calendar.')
    return request

def parse(ds,question,ctx):
    check(isinstance(question,str) and 0<len(question.strip())<=2000,'Question must contain at most 2,000 characters.')
    q=question.lower();t=ds['company']['tables'];weeks=[r['week_start'] for r in t['calendar']]
    def matches(table,field):return [r[field] for r in t[table] if re.search(r'(?<![\w-])'+re.escape(r[field])+r'(?![\w-])',question,re.I)]
    suppliers=matches('suppliers','supplier_id');products=matches('items','item_id');warehouses=matches('facilities','facility_id')
    for token in re.findall(r'\b(?:S|P|W|C|L)\d+\b',question,re.I):
        if not any(token.lower()==str(v).lower() for rows in t.values() for r in rows for v in r.values()):raise Clarify('Unknown ID '+token+'. Choose an ID from Company Data.')
    for r in t['facilities']:
        if r['facility_type']=='warehouse' and r['facility_name'].split()[0].lower() in q:warehouses.append(r['facility_id'])
    for entity,selected in [('warehouse',warehouses),('product',products)]:
        explicit=re.search(r'\b(?:what about|impact on|affected at)\s+(?:the\s+)?'+entity+r'\s+([\w.-]+)',q)
        if explicit and not selected:raise Clarify('Unknown '+entity+' '+explicit[1]+'. Choose an ID from Company Data.')
    def req(tool,**a):return validate(ds,dict(tool=tool,arguments=a))
    if any(x in q for x in ('profit','otif','probability','news','risk score','cash flow')):raise Clarify('That metric is not calculated by this model. Ask about unit fill rate, unmet demand, committed cost or revenue exposure.')
    if 'assumption' in q or 'limitation' in q:return req('assumptions')
    if 'baseline' in q and not any(x in q for x in ('compare','cost','why')):return req('baseline')
    scenario=copy.deepcopy(ctx.get('scenario'))
    if ('largest vulnerability' in q or 'most vulnerable' in q) and not re.search(r'\bweeks?\b',q):
        raise Clarify('Specify a shutdown duration and start week to compare suppliers by calculated unmet demand, for example: compare supplier vulnerability for four weeks starting in the first planning week.')
    numbers={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'eight':8}
    duration_match=re.search(r'(\d+|one|two|three|four|five|six|eight)\s+(?:more\s+)?weeks?',q)
    duration=(numbers.get(duration_match[1],int(duration_match[1]) if duration_match[1].isdigit() else 0) if duration_match else None)
    start_match=re.search(r'\d{4}-\d{2}-\d{2}',q)
    start=start_match[0] if start_match else (weeks[0] if 'first planning week' in q or 'week 1' in q else None)
    if 'vulnerability' in q:
        if duration is None or start is None:raise Clarify('Specify duration and start week for the supplier shutdown comparison.')
        return req('vulnerability',duration=duration,start_week=start)
    if 'more weeks' in q:
        if scenario is None or duration is None:raise Clarify('Run a disruption first, then specify how many more weeks.')
        scenario['duration_weeks']+=duration;return req('scenario',scenario=scenario)
    percent=re.search(r'(-?\d+(?:\.\d+)?)\s*%',q);pct=float(percent[1]) if percent else None
    if any(x in q for x in ('optimiz','service target')):
        if any(x in q for x in ('explain','why','manager','plan')) and ctx.get('latest'):return req('results',metric='plan',product_id=None,warehouse_id=None)
        if pct is None:raise Clarify('What unit fill-rate target percentage should the optimizer require?')
        if scenario is None:raise Clarify('Configure and run a disruption before optimization.')
        return req('optimization',scenario=scenario,target=pct/100)
    if any(x in q for x in ('mitigation','cheapest tested','alternate supplier','expedited','strategy')):
        if scenario is None:raise Clarify('Which disruption should the mitigation strategies address? Run a scenario first.')
        if pct is None and 'cheapest' in q:raise Clarify('What fill-rate target percentage should the tested strategies meet?')
        return req('mitigation',scenario=scenario,target=pct/100 if pct is not None else .95)
    disruption=any(x in q for x in ('loses','loss','shutdown','shut down','demand','lead time','purchase price','transportation cost'))
    if disruption and not any(x in q for x in ('why','explain')):
        if duration is None or start is None:
            raise Clarify('Specify the duration and start week, for example “for four weeks starting in the first planning week”.')
        s=dict(scenario_id='analyst_scenario',start_week=start,duration_weeks=duration,existing_firm_orders_policy='preserve_scheduled_receipts')
        if 'demand' in q:
            if not products or pct is None:raise Clarify('Specify finished product IDs and a demand increase percentage.')
            wh=sorted(set(warehouses))
            if not wh and 'all warehouses' not in q:raise Clarify('Which warehouses should receive the demand surge? Specify IDs or say “all warehouses”.')
            s.update(type='demand_surge',product_ids=products,warehouse_ids=wh or [r['facility_id'] for r in t['facilities'] if r['facility_type']=='warehouse'],demand_increase_percentage=pct)
        else:
            if len(suppliers)!=1:raise Clarify('Specify exactly one supplier ID.')
            s['supplier_id']=suppliers[0]
            if 'shutdown' in q or 'shut down' in q:s['type']='supplier_shutdown'
            elif 'lead time' in q:raise Clarify('Use Scenario Lab to specify the additional lead time separately from disruption duration.')
            elif 'purchase price' in q:s.update(type='purchase_cost_increase',cost_increase_percentage=pct)
            else:
                if pct is None:raise Clarify('What capacity loss percentage should be applied?')
                s.update(type='supplier_capacity_loss',capacity_loss_percentage=pct)
        return req('scenario',scenario=s)
    if 'information' in q or 'inventory' in q or 'supplier offers' in q:
        return req('information',table='supplier_offers' if 'supplier' in q else 'inventory',id=(suppliers+products+warehouses or [None])[0])
    metric='cost' if 'cost' in q else 'fill_rate' if 'fill' in q else 'bottlenecks' if 'bottleneck' in q else 'summary'
    if any(x in q for x in ('affected','impact','what about','explain','why','bottleneck','fill','cost','result')):
        if not ctx.get('latest'):raise Clarify('Run an analysis first so I can explain verified results.')
        return req('results',metric=metric,product_id=(products or [None])[0],warehouse_id=(warehouses or [None])[0])
    raise Clarify('I support defined supply-chain questions. Ask for baseline, a dated disruption, mitigation comparison, optimization, impact details or assumptions.')

def execute(ds,request,ctx,progress=lambda x:None):
    validate(ds,request);check(ctx['dataset_id']==ds['id'],'Dataset changed. Start a new analyst context.')
    tool=request['tool'];a=request['arguments'];result=None
    if tool=='baseline':result=api.baseline(ds)
    elif tool=='scenario':result=api.scenario_run(ds,a['scenario'])
    elif tool in ('mitigation','optimization'):
        mc,opt=api.defaults(ds,a['scenario'])
        if tool=='mitigation':mc['settings']['target_unit_fill_rate']=a['target'];result=api.mitigation_run(ds,mc)
        else:
            opt.update(service_target=a['target'],service_targets_to_run=[a['target']]);result=api.optimization_run(ds,dict(scenario=a['scenario'],optimization=opt),progress)
    elif tool=='vulnerability':
        rows=[]
        for supplier in ds['company']['tables']['suppliers']:
            s=dict(scenario_id='supplier_screen',type='supplier_shutdown',supplier_id=supplier['supplier_id'],start_week=a['start_week'],duration_weeks=a['duration'],existing_firm_orders_policy='preserve_scheduled_receipts')
            r=api.scenario_run(ds,s);rows.append(dict(supplier_id=supplier['supplier_id'],unmet_units=r['run']['totals']['unmet_units'],result_id=r['key']))
        result=api.envelope(ds,'vulnerability',a,rows=sorted(rows,key=lambda r:-r['unmet_units']))
    elif tool=='information':result=api.envelope(ds,'information',a,rows=[r for r in ds['company']['tables'][a['table']] if a['id'] is None or a['id'] in r.values()])
    elif tool=='assumptions':result=api.envelope(ds,'assumptions',a,model=ds['company']['config'],policy=ds['company']['policy'])
    else:
        result=ctx.get('latest');check(result is not None and result['dataset_id']==ds['id'],'No current verified result.')
    if result.get('run'):
        check(result['run']['checks']['status']=='PASS','Engine output failed audit.')
        if result['kind']=='optimization':check(result['verification']['status']=='PASS','Optimization replay failed.')
    updated=copy.copy(ctx);updated['previous']=copy.deepcopy(request)
    if tool not in ('results','information','assumptions','vulnerability'):updated['latest']=result
    if 'scenario' in a:updated['scenario']=copy.deepcopy(a['scenario'])
    explanation,citations=explain(result,request)
    return updated,result,dict(request=request,result_id=result['key'],dataset_id=result['dataset_id'],schema_version=result.get('schema_version','1.0'),configuration_hash=result.get('configuration_hash'),request_configuration_hash=result.get('request_configuration_hash'),explanation=explanation,citations=citations)

def explain(result,request):
    citations={};lines=[]
    def field(path,value):citations[path]=value;return value
    if request['tool']=='results':lines.append('Retrieved the previous verified calculation; no new analysis was run.')
    if result.get('run'):
        run=result['run'];v=result['valuation'];t=run['totals']
        for k in ['demand_units','fulfilled_units','unmet_units','unit_fill_rate','potential_sales_revenue_exposure']:field('run.totals.'+k,t[k])
        for k in ['gross_committed_resource_cost','terminal_owned_inventory_value','pending_purchase_and_inbound_commitment']:field('valuation.'+k,v[k])
        fill=f"{100*t['unit_fill_rate']:.2f}%" if t['unit_fill_rate'] is not None else 'undefined (zero demand)'
        lines.append(f"{result['kind']}: demand {t['demand_units']:,}, fulfilled {t['fulfilled_units']:,}, unmet {t['unmet_units']:,}; unit fill rate {fill}. Committed modeled cost ${v['gross_committed_resource_cost']:,.2f}.")
        if result.get('baseline'):
            b=result['baseline']['totals'];field('baseline.totals',b)
            lines.append(f"Unchanged baseline: {b['fulfilled_units']:,} fulfilled, {b['unmet_units']:,} unmet. Additional unmet units: {t['unmet_units']-b['unmet_units']:,}.")
        lines.append(f"Ending owned inventory value ${v['terminal_owned_inventory_value']:,.2f}; outstanding PO commitment ${v['pending_purchase_and_inbound_commitment']:,.2f}. Revenue exposure ${t['potential_sales_revenue_exposure']:,.2f} is unmet units × selling price, not lost profit.")
        affected=sorted({r['product_id'] for r in run['warehouse_inventory'] if r['unmet_units']});lines.append('Products with unmet demand: '+(', '.join(affected) or 'none')+'.')
        if request['tool']=='results':
            a=request['arguments'];rows=[r for r in run['warehouse_inventory'] if (a['product_id'] is None or r['product_id']==a['product_id']) and (a['warehouse_id'] is None or r['warehouse_id']==a['warehouse_id'])]
            field('selected.warehouse_inventory',rows)
            lines.append(f"Selected product/warehouse cells: {sum(r['fulfilled_units'] for r in rows):,} fulfilled and {sum(r['unmet_units'] for r in rows):,} unmet.")
            if a['metric']=='cost' or a['metric']=='plan':lines.append('Cost depends on purchase and freight commitments, conversion and holding. Inventory depletion, fewer fulfilled units, sourcing and transport choices can change cost. No terminal reserve or future demand is imposed unless configured; this is not proof of recurring savings. Inspect the cost breakdown and terminal position.')
            if a['metric']=='bottlenecks':
                bound=[r for r in run['supplier_capacity'] if r['remaining_capacity_units']==0];field('binding.supplier_capacity',bound);lines.append(f"{len(bound)} supplier/component/week capacity rows are fully used; see calculation details. This alone does not prove each capacity has marginal economic value.")
        if result.get('solver'):
            s=result['solver'];field('solver',s);field('verification.status',result['verification']['status']);target=result['config']['optimization']['service_target'];field('config.optimization.service_target',target)
            lines.append(f"Solver {s['status']}; {'optimal within this model' if s['proven_optimal'] else 'feasible, not proven optimal'}; target {100*target:.2f}%; gap {100*s['relative_gap']:.3f}%" if s['relative_gap'] is not None else f"Solver {s['status']}; target {100*target:.2f}%; gap unavailable.")
            lines.append('Independent replay: '+result['verification']['status']+'. Aggregate service can hide product or warehouse shortages.')
    elif result.get('solver'):
        field('solver',result['solver']);lines.append('Solver status '+result['solver']['status']+'. No verified feasible plan returned; no target was relaxed.')
    elif result.get('comparison'):
        field('comparison.rows',result['comparison']['rows']);field('comparison.ranking',result['comparison']['ranking']);lines.append(result['comparison']['ranking']['target_statement']+'. Ranking covers tested configurations only. Prepared inventory is proactive and incurs acquisition and holding costs; reactive strategies share initial stock.')
    elif result.get('rows'):
        field('rows',result['rows']);lines.append('Retrieved engine/data rows are shown in calculation details. Supplier screening ranks calculated unmet demand for the specified shutdown only; it is not a probability or a universal risk score.')
    else:field('model',result.get('model'));field('policy',result.get('policy'));lines.append('The model uses weekly receipts, BOM consumption, constrained production, dated shipments and lost sales. Firm receipts are preserved and safety targets are already included in on-hand inventory.')
    lines.append(api.LIMITATIONS)
    return '\n\n'.join(lines),citations
