"""Incremental preparedness procurement before the supplied company snapshot."""
import copy
from collections import defaultdict
from datetime import date, timedelta
from phase2.engine import D, ZERO, integer, check


def prepare(company, policy, annual_rate):
    config = policy.config['preparation']
    if config is None:
        return copy.deepcopy(company), dict(feasible=True, orders=[], capacity=[], inventory=[], extra_inventory=[],
            material_acquisition_cost=ZERO, inbound_acquisition_cost=ZERO, acquisition_cost=ZERO,
            holding_cost=ZERO, target_shortfalls={}, assumptions='No preparation; original snapshot is unchanged.')
    t = company['tables']
    factory = next(r['facility_id'] for r in t['facilities'] if r['facility_type']=='factory')
    snapshot = date.fromisoformat(company['config']['snapshot_date'])
    offers = policy.offers
    lanes = {(r['origin_id'],r['destination_id']):r for r in t['transportation']}
    reserved = {(r['supplier_id'],r['component_id']):r['quantity_units'] for r in config['other_weekly_commitments']}
    targets = {r['component_id']:r for r in config['targets']}
    known = defaultdict(int)
    for firm in t['open_orders']:
        key = firm['supplier_id'],firm['component_id']
        release = date.fromisoformat(firm['receipt_week_start']) - timedelta(weeks=integer(offers[key]['lead_time_weeks']))
        known[release.isoformat(),*key] += integer(firm['quantity_units'])
    orders, capacity, inventory = [], [], []
    onhand = defaultdict(int)
    rates = {c:D(offers[r['supplier_id'],c]['unit_cost_usd'])+D(lanes[r['supplier_id'],factory]['cost_usd_per_unit'])
             for c,r in targets.items()}
    holding = ZERO
    reasons=[]
    for i in range(-config['weeks'],0):
        week_date = snapshot+timedelta(weeks=i)
        week=week_date.isoformat()
        beginning=dict(onhand)
        received=defaultdict(int)
        for order in orders:
            if order['receipt_index']==i:
                onhand[order['component_id']]+=order['quantity_units']
                received[order['component_id']]+=order['quantity_units']
        releases={}
        for c,target in targets.items():
            s=target['supplier_id']
            offer=offers[s,c]
            cap=integer(offer['weekly_capacity_units'])
            committed=reserved[s,c]+known[week,s,c]
            future=sum(o['quantity_units'] for o in orders if o['component_id']==c and o['receipt_index']>i)
            remaining=max(0,target['extra_units']-onhand[c]-future)
            lead=integer(offer['lead_time_weeks'])
            moq=integer(offer['minimum_order_units'])
            spare=max(0,cap-committed)
            qty=min(spare,max(moq,remaining)) if remaining and spare>=moq and i+lead<0 else 0
            releases[s,c]=qty
            if qty:
                lane=lanes[s,factory]
                orders.append(dict(order_id=f'PREP-{i+config["weeks"]+1:03d}-{s}-{c}', supplier_id=s,
                    component_id=c,quantity_units=qty,release_week=week,release_index=i,
                    receipt_week=(week_date+timedelta(weeks=lead)).isoformat(),receipt_index=i+lead,
                    lead_time_weeks=lead,unit_price=D(offer['unit_cost_usd']),inbound_rate=D(lane['cost_usd_per_unit']),
                    material_cost=qty*D(offer['unit_cost_usd']),inbound_cost=qty*D(lane['cost_usd_per_unit'])))
        for (s,c),offer in sorted(offers.items()):
            other=reserved.get((s,c),0)
            firm=known[week,s,c]
            qty=releases.get((s,c),0)
            cap=integer(offer['weekly_capacity_units'])
            if other+firm>cap:
                reasons.append(f'{week} {s}/{c}: assumed other workload plus firm releases exceeds capacity')
            check(qty<=max(0,cap-other-firm),'Preparation capacity double counted')
            capacity.append(dict(week_start=week,supplier_id=s,component_id=c,capacity_units=cap,
                assumed_other_committed_units=other,known_firm_inferred_releases=firm,preparation_released_units=qty,
                remaining_capacity_units=max(0,cap-other-firm-qty)))
        for c,target in targets.items():
            value=onhand[c]*rates[c]
            cost=value*annual_rate/52
            holding+=cost
            inventory.append(dict(week_start=week,component_id=c,beginning_units=beginning.get(c,0),
                received_units=received[c],ending_units=onhand[c],ending_value=value,holding_cost=cost))
            check(beginning.get(c,0)+received[c]==onhand[c],'Preparation inventory conservation')
    shortages={c:max(0,r['extra_units']-onhand[c]) for c,r in targets.items()}
    extra=[dict(component_id=c,quantity_units=onhand[c],landed_unit_value=rates[c],
                acquisition_value=onhand[c]*rates[c],extra_safety_target_units=targets[c]['extra_units']) for c in targets]
    for order in orders:
        check(order['receipt_index']==order['release_index']+order['lead_time_weeks'] and order['receipt_index']<0,
              'Preparation order must arrive strictly before snapshot')
    check(sum(r['quantity_units'] for r in extra)==sum(o['quantity_units'] for o in orders), 'Preparation receipts missing')
    material=sum((o['material_cost'] for o in orders),ZERO)
    freight=sum((o['inbound_cost'] for o in orders),ZERO)
    variant=copy.deepcopy(company)
    for row in extra:
        record=next((r for r in variant['tables']['inventory'] if r['facility_id']==factory and r['item_id']==row['component_id']),None)
        if record is None:
            record=dict(facility_id=factory,item_id=row['component_id'],on_hand_units='0',safety_stock_target_units='0')
            variant['tables']['inventory'].append(record)
        record['on_hand_units']=str(integer(record['on_hand_units'])+row['quantity_units'])
        record['safety_stock_target_units']=str(integer(record['safety_stock_target_units'])+row['extra_safety_target_units'])
    if any(shortages.values()):
        reasons.append('Requested extra stock cannot all arrive before snapshot with the available preparation period/capacity')
    return variant,dict(feasible=not reasons,reasons=reasons,orders=orders,capacity=capacity,inventory=inventory,
        extra_inventory=extra,material_acquisition_cost=material,inbound_acquisition_cost=freight,
        acquisition_cost=material+freight,holding_cost=holding,target_shortfalls=shortages,
        assumptions='Incremental procurement only; original snapshot anchors normal business. Firm release weeks inferred from original lead times. Other commitments are synthetic configured reservations.')
