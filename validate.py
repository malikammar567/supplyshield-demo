"""Phase 1: check input integrity, not production feasibility. Standard library only."""
import csv, json, math, sys
from pathlib import Path
from datetime import date, timedelta

KEYS = {
 'suppliers':['supplier_id'],'items':['item_id'],'facilities':['facility_id'],
 'bom':['product_id','component_id'],'production':['facility_id','product_id'],
 'supplier_offers':['supplier_id','component_id'],'sourcing_policy':['supplier_id','component_id'],
 'inventory':['facility_id','item_id'],'transportation':['lane_id'],'calendar':['week_start'],
 'demand':['week_start','warehouse_id','product_id'],'open_orders':['order_id']}
NUMERIC = {'selling_price_usd','weekly_production_hours','quantity_per_product','hours_per_unit',
 'conversion_cost_usd_per_unit','weekly_capacity_units','unit_cost_usd','lead_time_weeks',
 'minimum_order_units','baseline_share','on_hand_units','safety_stock_target_units',
 'cost_usd_per_unit','demand_units','quantity_units'}
INTEGER = {'weekly_capacity_units','lead_time_weeks','minimum_order_units','on_hand_units',
 'safety_stock_target_units','demand_units','quantity_units','quantity_per_product'}
POSITIVE = {'quantity_per_product','hours_per_unit','weekly_capacity_units','quantity_units'}

def validate(root):
    errors=[]; data={}
    schema=json.loads((root/'schema.json').read_text())
    for name,spec in schema['tables'].items():
        try:
            with (root/'data'/f'{name}.csv').open(newline='') as f:
                reader=csv.DictReader(f)
                if reader.fieldnames != spec['columns']: errors.append(f'{name}: incorrect columns/order')
                rows=list(reader)
        except FileNotFoundError:
            errors.append(f'{name}: file missing'); continue
        data[name]=rows; seen=set()
        for idx,r in enumerate(rows,2):
            label=f'{name} row {idx}'
            if any(v is None or v=='' for v in r.values()) or None in r:
                errors.append(f'{label}: empty or malformed row'); continue
            key=tuple(r.get(k) for k in KEYS[name])
            if key in seen: errors.append(f'{label}: duplicate key {key}')
            seen.add(key)
            for field in NUMERIC.intersection(r):
                try:
                    n=float(r[field])
                    if not math.isfinite(n) or n<0: raise ValueError()
                    if field in INTEGER and not n.is_integer(): raise ValueError()
                    if field in POSITIVE and n<=0: raise ValueError()
                    if field=='baseline_share' and n>1: raise ValueError()
                except ValueError: errors.append(f'{label}: invalid {field}={r[field]}')
    if errors: return errors,data
    ids={name:{r[KEYS[name][0]]:r for r in data[name]} for name in ['suppliers','items','facilities']}
    for r in data['items']:
        if r['item_type'] not in ['component','finished_good']: errors.append('Invalid item type')
        if r['uom']!='EA': errors.append('This model supports EA units only')
    for r in data['facilities']:
        if r['facility_type'] not in ['factory','warehouse']: errors.append('Invalid facility type')
        if r['facility_type']=='factory' and float(r['weekly_production_hours'])<=0:
            errors.append('Factory production hours must be positive')
    weeks={r['week_start'] for r in data['calendar']}
    try:
        ds=[date.fromisoformat(r['week_start']) for r in data['calendar']]
        if not ds or ds != sorted(ds) or any(b-a != timedelta(days=7) for a,b in zip(ds,ds[1:])):
            errors.append('calendar: must be ordered consecutive weeks')
    except ValueError: errors.append('calendar: invalid ISO date')
    def ref(value,table,kind=None):
        found=ids[table].get(value)
        if not found: errors.append(f'Unknown {table} ID: {value}'); return
        if kind and found.get('item_type',found.get('facility_type'))!=kind:
            errors.append(f'{value}: expected {kind}')
    for r in data['bom']:
        ref(r['product_id'],'items','finished_good'); ref(r['component_id'],'items','component')
    offers={(r['supplier_id'],r['component_id']):r for r in data['supplier_offers']}
    sums={}
    for r in data['supplier_offers']:
        ref(r['supplier_id'],'suppliers'); ref(r['component_id'],'items','component')
        if float(r['minimum_order_units'])>float(r['weekly_capacity_units']): errors.append('MOQ exceeds offer capacity')
    for r in data['sourcing_policy']:
        if (r['supplier_id'],r['component_id']) not in offers: errors.append('Sourcing policy has no matching offer')
        sums[r['component_id']]=sums.get(r['component_id'],0)+float(r['baseline_share'])
    for component in [k for k,v in ids['items'].items() if v['item_type']=='component']:
        if abs(sums.get(component,0)-1)>1e-9: errors.append(f'{component}: baseline shares must sum to 1')
    for r in data['production']:
        ref(r['facility_id'],'facilities','factory'); ref(r['product_id'],'items','finished_good')
    for r in data['inventory']:
        ref(r['facility_id'],'facilities'); ref(r['item_id'],'items')
        if r['facility_id'] in ids['facilities'] and r['item_id'] in ids['items']:
            kind=ids['items'][r['item_id']]['item_type']
            location=ids['facilities'][r['facility_id']]['facility_type']
            if kind=='component' and location!='factory': errors.append('Components must be at a factory in this model')
    for r in data['demand']:
        ref(r['warehouse_id'],'facilities','warehouse'); ref(r['product_id'],'items','finished_good')
        if r['week_start'] not in weeks: errors.append('Demand week outside calendar')
    for r in data['transportation']:
        for side in ['origin','destination']:
            typ=r[side+'_type']
            if typ not in ['supplier','facility']: errors.append('Invalid lane endpoint type')
            else: ref(r[side+'_id'],'suppliers' if typ=='supplier' else 'facilities')
    lanes={(r['origin_id'],r['destination_id']):r for r in data['transportation']}
    for r in data['open_orders']:
        if (r['supplier_id'],r['component_id']) not in offers: errors.append('Open order has no supplier offer')
        ref(r['destination_id'],'facilities','factory')
        if r['receipt_week_start'] not in weeks: errors.append('Open order receipt outside calendar')
        if (r['supplier_id'],r['destination_id']) not in lanes: errors.append('Open order has no transport lane')
    finished={k for k,v in ids['items'].items() if v['item_type']=='finished_good'}
    for p in finished:
        if not any(r['product_id']==p for r in data['bom']): errors.append(f'{p}: missing BOM')
        if not any(r['product_id']==p for r in data['production']): errors.append(f'{p}: missing production route')
    for r in data['demand']:
        plants=[x['facility_id'] for x in data['production'] if x['product_id']==r['product_id']]
        if not any((p,r['warehouse_id']) in lanes for p in plants): errors.append('No factory-to-demand-warehouse lane')
    return errors,data

if __name__=='__main__':
    root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent
    errors,data=validate(root)
    report={'status':'FAIL' if errors else 'PASS','table_counts':{k:len(v) for k,v in data.items()},
            'errors':errors,'scope':'Data integrity only; service, cost and time-phased feasibility are Phase 2.'}
    (root/'validation_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    sys.exit(bool(errors))
