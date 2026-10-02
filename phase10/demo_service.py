"""Bounded public demo operations. No uploads, credentials, providers or live solver."""
import copy,json
from phase2.engine import ROOT,check
from phase6 import datasets,services as api
from phase7 import analyst
from phase9.provenance import execution

COMPANIES={'ClearDesk Manufacturing':'cleardesk','Beacon Instruments':'beacon'}

def company(name):
    check(name in COMPANIES,'Choose one of the two fictional demo companies.')
    if COMPANIES[name]=='cleardesk':return datasets.demo()
    ds=datasets.load_upload((ROOT/'phase8/assets/beacon_company.zip').read_bytes())
    ds['source']='Fictional bundled sample'
    return ds

def scenario_config(ds,supplier,start,duration,loss):
    weeks=[r['week_start'] for r in ds['company']['tables']['calendar']]
    check(supplier in {r['supplier_id'] for r in ds['company']['tables']['suppliers']},'Choose a supplier in this company.')
    check(start in weeks,'Start week must be in the demo calendar.')
    check(type(duration) is int and 1<=duration<=len(weeks)-weeks.index(start),'Duration must fit the demo calendar.')
    check(type(loss) is int and 0<=loss<=100,'Capacity loss must be a whole percentage from 0 to 100.')
    cfg=api.default_scenario(ds)
    cfg.update(supplier_id=supplier,start_week=start,duration_weeks=duration,capacity_loss_percentage=loss)
    return cfg

def calculate(name,cfg=None,target=.95):
    ds=company(name)
    if cfg is None:return api.baseline(ds)
    check(cfg.get('type')=='supplier_capacity_loss','Public demo supports bounded supplier capacity-loss scenarios.')
    allowed=scenario_config(ds,cfg.get('supplier_id'),cfg.get('start_week'),cfg.get('duration_weeks'),cfg.get('capacity_loss_percentage'))
    check(cfg==allowed,'Use the public demo scenario settings; arbitrary configurations are not accepted.')
    if target is None:return api.scenario_run(ds,cfg)
    check(type(target) in (float,int) and target in (.90,.95,.99),'Choose a 90%, 95% or 99% target.')
    mc,_=api.defaults(ds,cfg);mc['settings']['target_unit_fill_rate']=target
    return api.mitigation_run(ds,mc)

def saved_optimization(name):
    ds=company(name)
    result=json.loads((ROOT/'phase9/results'/COMPANIES[name]/'optimization.json').read_text())
    check(result['dataset_id']==ds['id'],'Saved plan belongs to a different dataset.')
    check(result['engine_source_hash']==execution('check')['engine_source_hash'],'Saved plan uses an older engine. Regenerate it locally.')
    check(result.get('verification',{}).get('status')=='PASS' and result.get('run',{}).get('checks',{}).get('status')=='PASS','Saved plan has no passing verification.')
    check(api.current(result,ds,'optimization',result['config']),'Saved plan/configuration fingerprint does not match.')
    result['execution_origin']='saved_verified_example'
    return result

def answer(name,question,cfg,latest):
    """Validate deterministic intent before any calculation; never invoke providers."""
    ds=company(name)
    current_scenario=cfg
    if latest and latest['kind'] in ('optimization','mitigation'):current_scenario=latest['config']['scenario']
    elif latest and latest['kind']=='scenario':current_scenario=latest['config']
    ctx=analyst.context(ds,current_scenario)
    check(latest is None or latest['dataset_id']==ds['id'],'Choose a result from the active company.')
    ctx['latest']=copy.deepcopy(latest)
    request=analyst.parse(ds,question,ctx)
    tool=request['tool'];a=request['arguments']
    if tool in ('scenario','mitigation','optimization'):
        s=a['scenario'];check(s['type']=='supplier_capacity_loss','This public demo supports supplier capacity loss only.')
        bounded=scenario_config(ds,s.get('supplier_id'),s.get('start_week'),s.get('duration_weeks'),s.get('capacity_loss_percentage'))
        # Intent parser assigns its own scenario ID; normalize the public contract.
        request['arguments']['scenario']=bounded
    if tool=='optimization':
        result=saved_optimization(name)
        saved=result['config']['scenario'];wanted=request['arguments']['scenario']
        fields=('supplier_id','start_week','duration_weeks','capacity_loss_percentage')
        check(a['target']==.95 and all(saved[k]==wanted[k] for k in fields),'The public optimizer example covers the first supplier, 60% loss for four weeks, and a 95% target. Custom optimization is available locally.')
        text,citations=analyst.explain(result,request)
        return result,dict(request=request,explanation='Saved, independently verified optimizer example; no new solver run.\n\n'+text,citations=citations)
    check(tool!='vulnerability','Choose one supplier in the disruption controls; batch screening is available locally.')
    if tool=='mitigation':check(a['target'] in (.90,.95,.99),'Public comparison targets are 90%, 95% and 99%.')
    _,result,trace=analyst.execute(ds,request,ctx)
    return result,trace
