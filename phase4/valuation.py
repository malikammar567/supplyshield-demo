"""Moving-average flow valuation and consistent committed-cost comparison.

No financial values influence production. This replays verified physical flows.
"""
from collections import defaultdict
from phase2.engine import D, ZERO, check, integer

TOL=D('0.000001')


def value_run(original_company, run, preparation, annual_rate):
    t=original_company['tables']
    factory=next(r['facility_id'] for r in t['facilities'] if r['facility_type']=='factory')
    offers={(r['supplier_id'],r['component_id']):r for r in t['supplier_offers']}
    lanes={(r['origin_id'],r['destination_id']):r for r in t['transportation']}
    component_values=defaultdict(lambda:ZERO)
    for share in t['sourcing_policy']:
        s,c=share['supplier_id'],share['component_id']
        component_values[c]+=D(share['baseline_share'])*(D(offers[s,c]['unit_cost_usd'])+D(lanes[s,factory]['cost_usd_per_unit']))
    routes={r['product_id']:r for r in t['production']}
    product_values={p:sum((integer(b['quantity_per_product'])*component_values[b['component_id']] for b in t['bom'] if b['product_id']==p),ZERO)+D(r['conversion_cost_usd_per_unit']) for p,r in routes.items()}
    accounts={}
    def add(key,units,value,prep_value=ZERO):
        if key not in accounts:
            accounts[key]=dict(units=0,value=ZERO,prepared_value=ZERO)
        a=accounts[key]
        a['units']+=units; a['value']+=value; a['prepared_value']+=prep_value
    def take(key,units):
        a=accounts.setdefault(key,dict(units=0,value=ZERO,prepared_value=ZERO))
        check(0<=units<=a['units'],f'Valuation physical stock mismatch: {key}')
        if units==0:
            return ZERO,ZERO
        if units==a['units']:
            value,prepared=a['value'],a['prepared_value']
        else:
            fraction=D(units)/a['units']
            value,prepared=a['value']*fraction,a['prepared_value']*fraction
        a['units']-=units; a['value']-=value; a['prepared_value']-=prepared
        return value,prepared
    opening=ZERO
    for row in t['inventory']:
        item,facility=row['item_id'],row['facility_id']
        if item in component_values:
            rate=component_values[item]
        else:
            rate=product_values[item]+(D(lanes[factory,facility]['cost_usd_per_unit']) if facility!=factory else ZERO)
        value=integer(row['on_hand_units'])*rate
        add((facility,item),integer(row['on_hand_units']),value)
        opening+=value
    for row in preparation['extra_inventory']:
        add((factory,row['component_id']),row['quantity_units'],row['acquisition_value'],row['acquisition_value'])
    pipeline={}
    weekly=[]; inventory_values=[]
    fulfilled_cost=prepared_fulfilled=holding=ZERO
    for week in run['metadata']['weeks']:
        for r in run['receipts']:
            if r['week_start']==week:
                add((factory,r['component_id']),r['quantity_units'],r['purchase_cost']+r['inbound_transport_cost'])
        for sid,entry in list(pipeline.items()):
            if entry['arrival_week']==week:
                add((entry['warehouse_id'],entry['product_id']),entry['quantity_units'],entry['value'],entry['prepared_value'])
                del pipeline[sid]
        for prod in run['production']:
            if prod['week_start']!=week:
                continue
            p=prod['product_id']; value=prod['conversion_cost']; tag=ZERO
            for consumption in run['material_consumption']:
                if consumption['week_start']==week and consumption['product_id']==p:
                    v,prepared=take((factory,consumption['component_id']),consumption['consumed_units'])
                    value+=v; tag+=prepared
            add((factory,p),prod['produced_units'],value,tag)
            for shipment in run['shipments']:
                if shipment['release_week']!=week or shipment['product_id']!=p:
                    continue
                v,prepared=take((factory,p),shipment['quantity_units'])
                v+=shipment['outbound_transport_cost']
                if shipment['arrival_week']==week:
                    add((shipment['warehouse_id'],p),shipment['quantity_units'],v,prepared)
                else:
                    pipeline[shipment['shipment_id']]=dict(shipment,value=v,prepared_value=prepared)
        sold_cost=ZERO
        for row in run['warehouse_inventory']:
            if row['week_start']==week:
                value,tag=take((row['warehouse_id'],row['product_id']),row['fulfilled_units'])
                fulfilled_cost+=value; prepared_fulfilled+=tag; sold_cost+=value
        # Cross-check all valued physical units against independent engine ledgers.
        for ledger,item,location in [('component_inventory','component_id','facility_id'),('factory_inventory','product_id','facility_id'),('warehouse_inventory','product_id','warehouse_id')]:
            for row in run[ledger]:
                if row['week_start']==week:
                    a=accounts.setdefault((row[location],row[item]),dict(units=0,value=ZERO,prepared_value=ZERO))
                    check(a['units']==row['ending_units'],'Physical and valued inventory do not reconcile')
        onhand_value=sum((a['value'] for a in accounts.values()),ZERO)
        transit_value=sum((s['value'] for s in pipeline.values()),ZERO)
        cost=(onhand_value+transit_value)*annual_rate/52
        holding+=cost
        weekly.append(dict(week_start=week,on_hand_value=onhand_value,owned_transit_value=transit_value,
                           holding_cost=cost,fulfilled_cost=sold_cost))
        for (location,item),a in sorted(accounts.items()):
            inventory_values.append(dict(week_start=week,facility_id=location,item_id=item,quantity_units=a['units'],
                                        inventory_value=a['value'],prepared_acquisition_value_remaining=a['prepared_value']))
    ending=sum((a['value'] for a in accounts.values()),ZERO)+sum((s['value'] for s in pipeline.values()),ZERO)
    prepared_remaining=sum((a['prepared_value'] for a in accounts.values()),ZERO)+sum((s['prepared_value'] for s in pipeline.values()),ZERO)
    firm_material=sum((o['quantity_units']*o['unit_price'] for o in run['orders'] if o['kind']=='firm'),ZERO)
    firm_freight=sum((o['quantity_units']*o['inbound_rate'] for o in run['orders'] if o['kind']=='firm'),ZERO)
    all_material=firm_material+run['totals']['new_purchase_commitment']
    all_inbound=firm_freight+run['totals']['new_inbound_freight_commitment']
    total_holding=holding+preparation['holding_cost']
    committed=preparation['acquisition_cost']+all_material+all_inbound+run['totals']['conversion_cost']+run['totals']['outbound_transport_cost']+total_holding
    pending=sum((o['quantity_units']*(o['unit_price']+o['inbound_rate']) for o in run['pending_orders']),ZERO)
    consumed=opening+committed-ending-pending
    check(abs(consumed-(fulfilled_cost+total_holding))<TOL,'Financial resource reconciliation failed')
    check(abs(preparation['acquisition_cost']-prepared_remaining-prepared_fulfilled)<TOL,'Prepared inventory value not conserved')
    return dict(opening_inventory_proxy_value=opening,
        committed_purchasing_cost=all_material,committed_inbound_transport_cost=all_inbound,
        recognized_purchasing_cost=run['totals']['purchasing_cost'],conversion_cost=run['totals']['conversion_cost'],
        recognized_transportation_cost=run['totals']['transportation_cost'],
        committed_transportation_cost=all_inbound+run['totals']['outbound_transport_cost'],
        preparation_material_acquisition=preparation['material_acquisition_cost'],
        preparation_inbound_acquisition=preparation['inbound_acquisition_cost'],
        preparation_acquisition_cost=preparation['acquisition_cost'],preparation_holding_cost=preparation['holding_cost'],
        horizon_holding_cost=holding,total_holding_cost=total_holding,
        gross_committed_resource_cost=committed,terminal_owned_inventory_value=ending,
        pending_purchase_and_inbound_commitment=pending,inventory_adjusted_resource_consumption_cost=consumed,
        prepared_acquisition_value_remaining=prepared_remaining,prepared_acquisition_value_fulfilled=prepared_fulfilled,
        modeled_fulfilled_cost=fulfilled_cost,financial_reconciliation='PASS',weekly=weekly,
        inventory_values=inventory_values,terminal_inventory=inventory_values[-len(accounts):] if accounts else [],
        terminal_transfers=list(pipeline.values()))
