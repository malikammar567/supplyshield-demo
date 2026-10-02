"""Explicit-plan event execution. No optimization or heuristic planning is run here.

Uses the Phase 2 input contract, ledger format and independent audit. All choices
are taken from the supplied plan and checked before inventory can move.
"""
from collections import defaultdict
from phase2.engine import D,ZERO,check,audit,integer
from phase4.strategies import whole
from phase5.data import ProblemData


def replay(company,scenario,config,plan):
    data=ProblemData(company,scenario,config)
    inventory=defaultdict(int,data.initial)
    orders=[dict(o) for o in data.firm];transfers=[]
    groups={name:{} for name in ['orders','production','shipments','fulfillment']}
    definitions={'orders':['week_start','supplier_id','component_id','route_id'],
        'production':['week_start','product_id'],'shipments':['week_start','warehouse_id','product_id','route_id'],
        'fulfillment':['week_start','warehouse_id','product_id']}
    check(set(plan)=={'orders','production','shipments','fulfillment','metadata'},'Explicit plan requires four decision tables and metadata')
    for name,keys in definitions.items():
        check(isinstance(plan[name],list),'Decision table must be a list')
        for row in plan[name]:
            check(set(row)==set(keys)|{'quantity_units'},'Unexpected explicit decision fields')
            key=tuple(row[k] for k in keys)
            check(row['week_start'] in data.index,'Decision week outside calendar')
            check(key not in groups[name],'Duplicate decision key')
            groups[name][key]=whole(row['quantity_units'],'plan quantity')
            if name=='orders':
                check((data.index[key[0]],key[1],key[2],key[3]) in data.inbound,'Unqualified offer or unavailable inbound route')
            elif name=='shipments':
                check(key[2] in data.products and (data.index[key[0]],key[1],key[3]) in data.outbound,'Invalid product or unavailable outbound route')
            elif name=='production':check(key[1] in data.products,'Unknown production product')
            else:check((data.index[key[0]],key[1],key[2]) in data.demand,'Unknown fulfillment cell')
    result={k:[] for k in ['component_inventory','factory_inventory','warehouse_inventory','production','supplier_capacity',
                          'receipts','weekly','material_consumption','order_pipeline','shipment_pipeline']}
    for i,week in enumerate(data.weeks):
        beginning=inventory.copy();received=defaultdict(int);wh_received=defaultdict(int)
        purchasing=inbound=conversion=outbound=new_purchase=new_freight=ZERO
        for o in orders:
            if o['receipt_index']==i:
                q=o['quantity_units'];inventory[data.factory,o['component_id']]+=q;received[o['component_id']]+=q
                purchasing+=q*o['unit_price'];inbound+=q*o['inbound_rate']
                result['receipts'].append(dict(week_start=week,order_id=o['order_id'],kind=o['kind'],supplier_id=o['supplier_id'],
                    component_id=o['component_id'],quantity_units=q,purchase_cost=q*o['unit_price'],inbound_transport_cost=q*o['inbound_rate']))
        for s in transfers:
            if s['arrival_index']==i:
                inventory[s['warehouse_id'],s['product_id']]+=s['quantity_units'];wh_received[s['warehouse_id'],s['product_id']]+=s['quantity_units']
        for (supplier,c),offer in sorted(data.offers.items()):
            selected=[(key,qty) for key,qty in groups['orders'].items() if key[:3]==(week,supplier,c)]
            qty=sum(q for key,q in selected);cap=data.cap[i,supplier,c]
            check(qty<=cap,'Explicit plan exceeds supplier/component capacity across transport modes')
            check(qty==0 or qty>=integer(offer['minimum_order_units']),'Explicit consolidated order below MOQ')
            result['supplier_capacity'].append(dict(week_start=week,supplier_id=supplier,component_id=c,
                normal_capacity_units=integer(offer['weekly_capacity_units']),effective_capacity_units=cap,released_units=qty,
                utilization=D(qty)/cap if cap else None,remaining_capacity_units=cap-qty))
            for key,q in selected:
                if not q:continue
                route=data.inbound[i,supplier,c,key[3]]
                orders.append(dict(order_id=f'PLAN-{i}-{supplier}-{c}-{key[3]}',kind='new',supplier_id=supplier,component_id=c,
                    destination_id=data.factory,quantity_units=q,release_week=week,release_index=i,receipt_week=data.date_at(route['arrival']),
                    receipt_index=route['arrival'],lead_time_weeks=route['lead'],unit_price=route['unit_price'],inbound_rate=route['freight'],
                    lane_id=route['lane_id'],route_id=key[3]))
                new_purchase+=q*route['unit_price'];new_freight+=q*route['freight']
        consumed=defaultdict(int);made=defaultdict(int);shipped=defaultdict(int);hours=ZERO
        for p in data.products:
            q=groups['production'].get((week,p),0);made[p]=q
            use=q*D(data.routes[p]['hours_per_unit']);hours+=use
            check(hours<=data.hours,'Explicit plan exceeds shared factory labor')
            for c,per in data.bom[p].items():
                check(inventory[data.factory,c]>=q*per,'Explicit production uses unavailable components')
                inventory[data.factory,c]-=q*per;consumed[c]+=q*per
                result['material_consumption'].append(dict(week_start=week,product_id=p,component_id=c,produced_units=q,bom_quantity=per,consumed_units=q*per))
            inventory[data.factory,p]+=q;cost=q*D(data.routes[p]['conversion_cost_usd_per_unit']);conversion+=cost
            result['production'].append(dict(week_start=week,product_id=p,produced_units=q,labor_hours=use,conversion_cost=cost))
            for key,units in groups['shipments'].items():
                if key[0]!=week or key[2]!=p or not units:continue
                _,wh,_,mode=key;route=data.outbound[i,wh,mode]
                check(inventory[data.factory,p]>=units,'Explicit shipment uses unavailable finished goods')
                inventory[data.factory,p]-=units;shipped[p]+=units;outbound+=units*route['freight']
                transfers.append(dict(shipment_id=f'PLAN-{i}-{wh}-{p}-{mode}',release_week=week,release_index=i,
                    arrival_week=data.date_at(route['arrival']),arrival_index=route['arrival'],warehouse_id=wh,product_id=p,
                    quantity_units=units,lead_time_weeks=route['lead'],lane_id=route['lane_id'],route_id=mode,
                    unit_freight_rate=route['freight'],outbound_transport_cost=units*route['freight']))
                if route['arrival']==i:
                    inventory[wh,p]+=units;wh_received[wh,p]+=units
        demand_total=fulfilled_total=unmet_total=0;exposure=ZERO
        for wh in data.warehouses:
            for p in data.products:
                demand=data.demand[i,wh,p];fulfilled=groups['fulfillment'].get((week,wh,p),0)
                check(fulfilled<=demand and fulfilled<=inventory[wh,p],'Explicit fulfillment exceeds demand or arrived stock')
                inventory[wh,p]-=fulfilled;unmet=demand-fulfilled;value=unmet*D(data.items[p]['selling_price_usd'])
                result['warehouse_inventory'].append(dict(week_start=week,warehouse_id=wh,product_id=p,beginning_units=beginning[wh,p],
                    receipts_units=wh_received[wh,p],demand_units=demand,fulfilled_units=fulfilled,unmet_units=unmet,ending_units=inventory[wh,p],
                    unit_fill_rate=D(fulfilled)/demand if demand else None,potential_sales_revenue_exposure=value))
                demand_total+=demand;fulfilled_total+=fulfilled;unmet_total+=unmet;exposure+=value
        for c in data.components:
            result['component_inventory'].append(dict(week_start=week,facility_id=data.factory,component_id=c,beginning_units=beginning[data.factory,c],
                receipts_units=received[c],consumed_units=consumed[c],ending_units=inventory[data.factory,c]))
        for p in data.products:
            result['factory_inventory'].append(dict(week_start=week,facility_id=data.factory,product_id=p,beginning_units=beginning[data.factory,p],
                produced_units=made[p],shipped_units=shipped[p],ending_units=inventory[data.factory,p]))
        for s,c in sorted(data.offers):
            selected=[o for o in orders if o['supplier_id']==s and o['component_id']==c]
            result['order_pipeline'].append(dict(week_start=week,supplier_id=s,component_id=c,
                beginning_units=sum(o['quantity_units'] for o in selected if o['receipt_index']>=i and (o['release_index'] is None or o['release_index']<i)),
                released_units=sum(o['quantity_units'] for o in selected if o['release_index']==i),
                received_units=sum(o['quantity_units'] for o in selected if o['receipt_index']==i),
                ending_units=sum(o['quantity_units'] for o in selected if o['receipt_index']>i)))
        for wh in data.warehouses:
            for p in data.products:
                selected=[s for s in transfers if s['warehouse_id']==wh and s['product_id']==p]
                result['shipment_pipeline'].append(dict(week_start=week,warehouse_id=wh,product_id=p,
                    beginning_units=sum(s['quantity_units'] for s in selected if s['arrival_index']>=i and s['release_index']<i),
                    dispatched_units=sum(s['quantity_units'] for s in selected if s['release_index']==i),
                    received_units=sum(s['quantity_units'] for s in selected if s['arrival_index']==i),
                    ending_units=sum(s['quantity_units'] for s in selected if s['arrival_index']>i)))
        result['weekly'].append(dict(week_start=week,demand_units=demand_total,fulfilled_units=fulfilled_total,unmet_units=unmet_total,
            unit_fill_rate=D(fulfilled_total)/demand_total if demand_total else None,produced_units=sum(made.values()),factory_hours_used=hours,
            factory_hours_available=data.hours,factory_utilization=hours/data.hours,purchasing_cost=purchasing,conversion_cost=conversion,
            inbound_transport_cost=inbound,outbound_transport_cost=outbound,transportation_cost=inbound+outbound,
            total_operating_activity_cost=purchasing+conversion+inbound+outbound,new_purchase_commitment=new_purchase,
            new_inbound_freight_commitment=new_freight,potential_sales_revenue_exposure=exposure))
    result['orders']=orders;result['shipments']=transfers
    result['pending_orders']=[o for o in orders if o['receipt_index']>=data.n]
    result['pending_shipments']=[s for s in transfers if s['arrival_index']>=data.n]
    totals={k:sum((r[k] for r in result['weekly']),ZERO) for k in result['weekly'][0] if k not in ['week_start','unit_fill_rate','factory_utilization']}
    totals['unit_fill_rate']=totals['fulfilled_units']/totals['demand_units'] if totals['demand_units'] else None
    totals['factory_utilization']=totals['factory_hours_used']/totals['factory_hours_available']
    totals['pending_purchase_commitment']=sum((o['quantity_units']*o['unit_price'] for o in result['pending_orders']),ZERO)
    totals['pending_inbound_freight_commitment']=sum((o['quantity_units']*o['inbound_rate'] for o in result['pending_orders']),ZERO)
    result['totals']=totals;result['metadata']=dict(weeks=data.weeks,policy=company['policy'],extension_assumption=company['extension_assumption'],
        firm_orders_units=sum(o['quantity_units'] for o in data.firm),planning_type='explicit plan execution; heuristic decisions bypassed')
    result['checks']=audit(result,company)
    check(totals['fulfilled_units']>=data.target*totals['demand_units'],'Explicit plan fails aggregate service target')
    for floor in config['service_floors']:
        keys=set(data.selected_demand(floor));rows=[r for r in result['warehouse_inventory'] if (data.index[r['week_start']],r['warehouse_id'],r['product_id']) in keys]
        check(sum(r['fulfilled_units'] for r in rows)>=D(floor['min_fill_rate'])*sum(r['demand_units'] for r in rows),'Explicit plan fails a service floor')
    for floor in config['terminal_inventory_floors']:
        check(inventory[floor['facility_id'],floor['item_id']]>=floor['min_units'],'Explicit plan fails terminal floor')
    return result


def plan_from_run(run):
    return dict(orders=[dict(week_start=o['release_week'],supplier_id=o['supplier_id'],component_id=o['component_id'],route_id=o['route_id'],quantity_units=o['quantity_units']) for o in run['orders'] if o['kind']=='new'],
        production=[dict(week_start=r['week_start'],product_id=r['product_id'],quantity_units=r['produced_units']) for r in run['production']],
        shipments=[dict(week_start=s['release_week'],warehouse_id=s['warehouse_id'],product_id=s['product_id'],route_id=s['route_id'],quantity_units=s['quantity_units']) for s in run['shipments']],
        fulfillment=[dict(week_start=r['week_start'],warehouse_id=r['warehouse_id'],product_id=r['product_id'],quantity_units=r['fulfilled_units']) for r in run['warehouse_inventory']],metadata=dict(source='exported verified run'))
