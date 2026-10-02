"""Trusted, versioned upload contract. CSV text is data, never instructions."""
import csv,json,math,re
from datetime import date,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from validate import KEYS,NUMERIC,INTEGER,POSITIVE

ROOT=Path(__file__).resolve().parents[1]
TRUSTED=json.loads((ROOT/'schema.json').read_text())['tables']
SCHEMA_VERSION='1.1'
SUPPORTED_VERSIONS={'1.0','1.1'}

def schema():
    return dict(version=SCHEMA_VERSION,model_config_version='1.0',tables={k:dict(columns=v['columns'],primary_key=KEYS[k]) for k,v in TRUSTED.items()})

class PackageError(ValueError):
    def __init__(self,report):
        self.report=report
        super().__init__('Dataset validation failed: '+'; '.join(e['file']+': '+e['message'] for e in report['errors'][:20]))

def validate_package(root):
    """Accumulate actionable errors; no engine is invoked for invalid inputs."""
    root=Path(root);errors=[];tables={};cfg={};policy={};declared={}
    def error(file,message,row=None,column=None,code='invalid_data'):
        errors.append(dict(file=file,row=row,column=column,code=code,message=message))
    for name in ['schema.json','model_config.json','phase2/planning_policy.json']:
        try:
            value=json.loads((root/name).read_text())
            if not isinstance(value,dict):raise ValueError('expected an object')
            if name=='schema.json':declared=value
            elif name=='model_config.json':cfg=value
            else:policy=value
        except (OSError,ValueError) as e:error(name,str(e))
    version=declared.get('version')
    if not isinstance(version,str) or version not in SUPPORTED_VERSIONS:error('schema.json','Unsupported schema version; supported: 1.0 (legacy), 1.1.',column='version',code='unsupported_version')
    if cfg.get('model_config_version','1.0' if version=='1.0' else None)!='1.0':error('model_config.json','Unsupported or missing model_config_version; use 1.0.',column='model_config_version',code='unsupported_version')
    if policy.get('policy_version')!='1.0':error('phase2/planning_policy.json','Unsupported policy_version; use 1.0.',column='policy_version',code='unsupported_version')
    model_keys=set(json.loads((ROOT/'model_config.json').read_text()))|{'model_config_version'}
    policy_keys=set(json.loads((ROOT/'phase2/planning_policy.json').read_text()))
    if set(cfg)-model_keys:error('model_config.json','Unsupported configuration fields: '+', '.join(sorted(set(cfg)-model_keys)))
    if set(policy)-policy_keys:error('phase2/planning_policy.json','Unsupported policy fields: '+', '.join(sorted(set(policy)-policy_keys)))
    declared_tables=declared.get('tables',{})
    if not isinstance(declared_tables,dict):declared_tables={}
    if set(declared_tables)!=set(TRUSTED):error('schema.json','Expected exactly the documented 12 tables.')
    for name,spec in TRUSTED.items():
        file='data/'+name+'.csv'
        declaration=declared_tables.get(name,{})
        if not isinstance(declaration,dict) or declaration.get('columns')!=spec['columns']:error('schema.json','Unsupported columns for '+name,column=name)
        if version=='1.1' and isinstance(declaration,dict) and declaration.get('primary_key')!=KEYS[name]:error('schema.json','Unsupported or missing primary_key for '+name,column=name)
        try:
            with (root/file).open(newline='') as handle:
                reader=csv.DictReader(handle);rows=list(reader)
                if reader.fieldnames!=spec['columns']:
                    error(file,'Incorrect headers: expected '+', '.join(spec['columns']),code='headers');continue
        except OSError:error(file,'Missing required file',code='missing_file');continue
        tables[name]=rows;seen=set()
        for i,r in enumerate(rows,2):
            if None in r or any(v is None or not v.strip() for v in r.values()):
                error(file,name+f' row {i}: empty or malformed row',i,code='malformed_row');continue
            key=tuple(r[k] for k in KEYS[name])
            if key in seen:error(file,name+f' row {i}: duplicate key {key}',i,code='duplicate_key')
            seen.add(key)
            for f,v in r.items():
                if len(v)>4096:error(file,'Field exceeds 4096 characters',i,f)
                if f.endswith('_id') and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',v):error(file,'IDs must use 1–64 letters, digits, dots, hyphens or underscores, starting with a letter or digit.',i,f)
                if f in NUMERIC:
                    try:
                        n=Decimal(v)
                        if not n.is_finite() or not math.isfinite(float(n)) or n<0 or (f in INTEGER and n!=n.to_integral_value()) or (f in POSITIVE and n<=0) or (f=='baseline_share' and n>1):raise ValueError()
                    except (ValueError,OverflowError,InvalidOperation):error(file,name+f' row {i}: invalid {f}={v}',i,f)
    # Preserve independent table errors, but skip relationship checks on malformed tables.
    if errors:return dict(status='FAIL',schema_version=version,errors=errors,mapping=mapping(tables),profile=None)
    t=tables;items={r['item_id']:r for r in t['items']};fac={r['facility_id']:r for r in t['facilities']};sup={r['supplier_id'] for r in t['suppliers']}
    if set(items)&set(fac) or set(items)&sup or set(fac)&sup:error('data','Supplier, item and facility ID namespaces must be disjoint to avoid ambiguous Analyst references.')
    masters=list(items)+list(fac)+list(sup)
    if len({v.lower() for v in masters})!=len(masters):error('data','Master IDs must remain unique when compared without case, for unambiguous Analyst parsing.')
    def ref(value,collection,file,row,column,kind=None):
        if value not in collection:error(file,f'{column} references missing ID {value}',row,column,'foreign_key');return
        if kind and collection[value].get('item_type',collection[value].get('facility_type'))!=kind:error(file,f'{value}: expected {kind}',row,column,'relationship')
    for i,r in enumerate(t['items'],2):
        if r['item_type'] not in ('component','finished_good'):error('data/items.csv','Invalid item type',i,'item_type')
        if r['uom']!='EA':error('data/items.csv','Unsupported unit; EA only',i,'uom','unsupported_unit')
    for i,r in enumerate(t['facilities'],2):
        if r['facility_type'] not in ('factory','warehouse'):error('data/facilities.csv','Invalid facility type',i,'facility_type')
    factories=[k for k,v in fac.items() if v['facility_type']=='factory'];warehouses=[k for k,v in fac.items() if v['facility_type']=='warehouse'];products=[k for k,v in items.items() if v['item_type']=='finished_good'];components=[k for k,v in items.items() if v['item_type']=='component']
    if len(factories)!=1:error('data/facilities.csv','Unsupported network: exactly one factory is required.',code='unsupported_network')
    if not warehouses or not products or not components or not sup:error('data','At least one supplier, component, finished product and warehouse is required.')
    if any(float(fac[f]['weekly_production_hours'])<=0 for f in factories):error('data/facilities.csv','Factory hours must be positive.')
    weeks=[r['week_start'] for r in t['calendar']]
    if len(weeks)>520 or len(weeks)*len(warehouses)*len(products)>200000:
        error('data/calendar.csv','Model size exceeds 520 weeks or 200,000 week/warehouse/product demand cells.',code='model_size_limit')
        return dict(status='FAIL',schema_version=version,errors=errors,mapping=mapping(t),profile=None)
    try:
        dates=[date.fromisoformat(w) for w in weeks]
        if not dates or any(b-a!=timedelta(days=7) for a,b in zip(dates,dates[1:])):raise ValueError()
    except ValueError:error('data/calendar.csv','Invalid dates or nonconsecutive ordered calendar weeks.')
    for name,relations in {
        'bom':[('product_id',items,'finished_good'),('component_id',items,'component')],
        'production':[('facility_id',fac,'factory'),('product_id',items,'finished_good')],
        'supplier_offers':[('supplier_id',sup,None),('component_id',items,'component')],
        'sourcing_policy':[('supplier_id',sup,None),('component_id',items,'component')],
        'inventory':[('facility_id',fac,None),('item_id',items,None)],
        'demand':[('warehouse_id',fac,'warehouse'),('product_id',items,'finished_good')],
        'open_orders':[('supplier_id',sup,None),('component_id',items,'component'),('destination_id',fac,'factory')]
    }.items():
        for i,r in enumerate(t[name],2):
            for f,collection,kind in relations:ref(r[f],collection,'data/'+name+'.csv',i,f,kind)
    offers={(r['supplier_id'],r['component_id']):r for r in t['supplier_offers']}
    sums={}
    for i,r in enumerate(t['sourcing_policy'],2):
        if (r['supplier_id'],r['component_id']) not in offers:error('data/sourcing_policy.csv','Policy has no qualified supplier offer',i,code='qualification')
        sums[r['component_id']]=sums.get(r['component_id'],0)+float(r['baseline_share'])
    for c in components:
        if abs(sums.get(c,0)-1)>1e-9:error('data/sourcing_policy.csv',c+': sourcing shares must total 100%.')
    for i,r in enumerate(t['supplier_offers'],2):
        if float(r['minimum_order_units'])>float(r['weekly_capacity_units']):error('data/supplier_offers.csv','MOQ exceeds capacity',i)
        if float(r['lead_time_weeks'])<1:error('data/supplier_offers.csv','Release-to-receipt lead time must be at least one week',i)
    for i,r in enumerate(t['inventory'],2):
        if r['item_id'] in components and r['facility_id'] not in factories:error('data/inventory.csv','Components must be stored at the factory',i,code='unsupported_network')
    lanes={}
    for i,r in enumerate(t['transportation'],2):
        for side in ['origin','destination']:
            typ=r[side+'_type']
            if typ not in ('supplier','facility'):error('data/transportation.csv','Invalid endpoint type',i,side+'_type')
            else:ref(r[side+'_id'],sup if typ=='supplier' else fac,'data/transportation.csv',i,side+'_id')
        pair=(r['origin_id'],r['destination_id'])
        if pair in lanes:error('data/transportation.csv','Unsupported parallel lanes; use explicit expedited strategy routes.',i,code='unsupported_network')
        lanes[pair]=r
        if not ((r['origin_type']=='supplier' and r['destination_id'] in factories and r['destination_type']=='facility') or (r['origin_id'] in factories and r['origin_type']=='facility' and r['destination_id'] in warehouses and r['destination_type']=='facility')):
            error('data/transportation.csv','Unsupported network: lanes must connect supplier→factory or factory→warehouse.',i,code='unsupported_network')
    for w in warehouses:
        if len(factories)==1 and ((factories[0],w) not in lanes or float(lanes[factories[0],w]['lead_time_weeks'])<1):error('data/transportation.csv','Warehouse '+w+' needs a positive-lead-time factory lane.')
    for s,c in offers:
        if len(factories)==1 and (s,factories[0]) not in lanes:error('data/transportation.csv','Supplier '+s+' has no inbound factory lane.')
    for p in products:
        if not any(r['product_id']==p for r in t['bom']):error('data/bom.csv',p+': missing BOM components.')
        if sum(r['product_id']==p for r in t['production'])!=1:error('data/production.csv',p+': exactly one production route required.',code='unsupported_network')
    expected={(w,h,p) for w in weeks for h in warehouses for p in products}
    actual={(r['week_start'],r['warehouse_id'],r['product_id']) for r in t['demand']}
    if actual!=expected:error('data/demand.csv','Demand must cover every calendar week, product and connected warehouse, including explicit zeros.')
    for i,r in enumerate(t['open_orders'],2):
        if r['receipt_week_start'] not in weeks:error('data/open_orders.csv','Invalid date: firm receipt must match a calendar week.',i,'receipt_week_start')
        if (r['supplier_id'],r['component_id']) not in offers:error('data/open_orders.csv','Firm order has no qualified offer',i)
        if (r['supplier_id'],r['destination_id']) not in lanes:error('data/open_orders.csv','Firm order has no inbound lane',i)
    if cfg.get('currency')!='USD':error('model_config.json','Unsupported currency; USD only.',column='currency')
    if not isinstance(cfg.get('company'),str) or not cfg['company'].strip():error('model_config.json','Company name is required.')
    if type(cfg.get('horizon_weeks')) is not int or cfg['horizon_weeks']!=len(weeks):error('model_config.json','Horizon must equal the calendar row count.')
    if not weeks or cfg.get('snapshot_date')!=weeks[0]:error('model_config.json','Snapshot date must equal the first calendar week.')
    from phase2.engine import validate_company
    if not errors:
        try:validate_company(dict(tables=t,config=cfg,policy=policy))
        except (ValueError,KeyError,TypeError,IndexError) as e:error('configuration',str(e),code='unsupported_configuration')
    return dict(status='FAIL' if errors else 'PASS',schema_version=version,errors=errors,mapping=mapping(t),profile=None)

def mapping(tables):
    return [dict(file='data/'+k+'.csv',table=k,columns=v['columns'],primary_key=KEYS[k],row_count=len(tables.get(k,[]))) for k,v in TRUSTED.items()]

def profile(ds):
    from phase6.datasets import digest
    c=ds['company'];t=c['tables'];weeks=[r['week_start'] for r in t['calendar']];warnings=[]
    for component in [r['item_id'] for r in t['items'] if r['item_type']=='component']:
        qualified=sorted({r['supplier_id'] for r in t['supplier_offers'] if r['component_id']==component})
        active=sorted({r['supplier_id'] for r in t['sourcing_policy'] if r['component_id']==component and float(r['baseline_share'])>0})
        if len(active)==1:warnings.append(component+': single-sourced baseline policy.')
        if len(qualified)<2:warnings.append(component+': no qualified alternate supplier.')
    for r in t['supplier_offers']:
        if float(r['lead_time_weeks'])>=len(weeks):warnings.append(r['supplier_id']+'/'+r['component_id']+': lead time reaches beyond the planning horizon; new-order impact may appear later.')
    warnings+=['Aggregate fill rate can hide product and warehouse failures.','No demand is assumed beyond the calendar. Outstanding orders may arrive afterward.','Supplier capacities are independent per component, not a shared supplier-wide pool.']
    return dict(dataset_id=ds['id'],schema_version=ds.get('schema_version','1.0'),configuration_hash=digest(dict(model=c['config'],policy=c['policy'])),company=ds['name'],row_counts={k:len(v) for k,v in t.items()},ids={k:[r[f] for r in t[k]] for k,f in [('suppliers','supplier_id'),('items','item_id'),('facilities','facility_id')]},horizon=dict(start=weeks[0],end=weeks[-1],weeks=len(weeks)),units='EA',currency='USD',relationships=dict(bom=len(t['bom']),qualified_offers=len(t['supplier_offers']),lanes=len(t['transportation']),firm_purchase_orders=len(t['open_orders'])),supported=['One factory','One-level component-to-finished-product BOM','Multiple suppliers, products and warehouses','Weekly integer material flows','Lost sales','Qualified offers and reactive strategies'],unsupported=['Multiple factories','Multi-stage BOMs','Warehouse transfers / multi-echelon networks','Substitute components','Shared supplier-wide capacity','Currency conversion','Live ERP integration'],assumptions=dict(model=c['config'],planning=c['policy']),warnings=warnings,optimization=dict(structurally_suitable=True,feasibility='Not guaranteed: a solver must check the requested target.',scale='Large inputs can exceed local memory or solver time; upload limits are security limits, not performance promises.'))
