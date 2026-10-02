"""Validated mathematical-model data, independent of solver and execution."""
from datetime import date,timedelta
from phase2.engine import D, ZERO, check, integer, validate_company
from phase3.scenarios import ScenarioEffects
from phase4.strategies import Mitigation, number, whole, fields


class ProblemData:
    def __init__(self,company,scenario,config):
        validate_company(company)
        fields(config,'service_target service_targets_to_run annual_holding_cost_rate holding_basis service_floors terminal_inventory_floors response_start_week response_duration_weeks expedited_routes solver_time_limit_seconds assumptions','Phase 5 config')
        self.company,self.scenario,self.config=company,scenario,config
        self.target=number(config['service_target'],'service_target',1)
        check(isinstance(config['service_targets_to_run'],list) and config['service_targets_to_run'],'Targets required')
        for target in config['service_targets_to_run']:number(target,'service target',1)
        self.holding_rate=number(config['annual_holding_cost_rate'],'holding rate')/52
        check(config['holding_basis'] in ('fixed_standard_value','moving_average_exact'),'Unsupported optimization holding basis')
        whole(config['solver_time_limit_seconds'],'solver time limit',1)
        check(bool(config['assumptions']),'Explicit assumptions required')
        t=company['tables']
        self.weeks=[r['week_start'] for r in t['calendar']]; self.n=len(self.weeks)
        self.index={w:i for i,w in enumerate(self.weeks)}
        self.factory=next(r['facility_id'] for r in t['facilities'] if r['facility_type']=='factory')
        self.hours=D(next(r['weekly_production_hours'] for r in t['facilities'] if r['facility_id']==self.factory))
        self.products=list(company['policy']['production_priority'])
        self.components=sorted(r['item_id'] for r in t['items'] if r['item_type']=='component')
        self.warehouses=sorted(r['facility_id'] for r in t['facilities'] if r['facility_type']=='warehouse')
        self.items={r['item_id']:r for r in t['items']}
        self.routes={r['product_id']:r for r in t['production']}
        self.bom={p:{r['component_id']:integer(r['quantity_per_product']) for r in t['bom'] if r['product_id']==p} for p in self.products}
        self.offers={(r['supplier_id'],r['component_id']):r for r in t['supplier_offers']}
        self.lanes={(r['origin_id'],r['destination_id']):r for r in t['transportation']}
        self.initial={(r['facility_id'],r['item_id']):integer(r['on_hand_units']) for r in t['inventory']}
        self.effects=ScenarioEffects(scenario,company)
        mitigation=Mitigation(dict(strategy_id='optimization_routes',mode='reactive',start_week=config['response_start_week'],
            duration_weeks=config['response_duration_weeks'],reallocation=[],expedited_routes=config['expedited_routes'],
            preparation=None,assumptions=config['assumptions']),company,scenario['start_week'])
        self.demand={(self.index[r['week_start']],r['warehouse_id'],r['product_id']):
            self.effects.demand(r['product_id'],r['warehouse_id'],r['week_start'],integer(r['demand_units'])) for r in t['demand']}
        self.cap={}; self.inbound={}; self.outbound={}
        for i,week in enumerate(self.weeks):
            for (s,c),offer in self.offers.items():
                self.cap[i,s,c]=self.effects.capacity(s,c,week,integer(offer['weekly_capacity_units']))
                lane=self.lanes[s,self.factory]
                lead=self.effects.lead_time(s,c,week,integer(offer['lead_time_weeks']))
                freight=self.effects.freight_price(lane['lane_id'],week,D(lane['cost_usd_per_unit']))
                price=self.effects.purchase_price(s,c,week,D(offer['unit_cost_usd']))
                options=[(lead,freight,lane['lane_id']),mitigation.inbound(week,s,c,lane,lead,freight)]
                for lag,rate,mode in options:
                    self.inbound[i,s,c,mode]=dict(lead=lag,arrival=i+lag,unit_price=price,freight=rate,lane_id=lane['lane_id'])
            for wh in self.warehouses:
                lane=self.lanes[self.factory,wh]
                lead=integer(lane['lead_time_weeks']); rate=self.effects.freight_price(lane['lane_id'],week,D(lane['cost_usd_per_unit']))
                for lag,freight,mode in [(lead,rate,lane['lane_id']),mitigation.outbound(week,lane,lead,rate)]:
                    self.outbound[i,wh,mode]=dict(lead=lag,arrival=i+lag,freight=freight,lane_id=lane['lane_id'])
        self.firm=[]
        for r in t['open_orders']:
            s,c=r['supplier_id'],r['component_id']; offer=self.offers[s,c]; lane=self.lanes[s,self.factory]
            self.firm.append(dict(order_id=r['order_id'],kind='firm',supplier_id=s,component_id=c,destination_id=self.factory,
                quantity_units=integer(r['quantity_units']),release_week=None,release_index=None,
                receipt_week=r['receipt_week_start'],receipt_index=self.index[r['receipt_week_start']],lead_time_weeks=None,
                unit_price=D(offer['unit_cost_usd']),inbound_rate=D(lane['cost_usd_per_unit']),lane_id=lane['lane_id'],route_id=lane['lane_id']))
        self.sunk_firm=sum((o['quantity_units']*(o['unit_price']+o['inbound_rate']) for o in self.firm),ZERO)
        # Reference values are fixed at undisrupted baseline shares/costs, identical in every comparison.
        component_values={c:ZERO for c in self.components}
        for r in t['sourcing_policy']:
            s,c=r['supplier_id'],r['component_id']
            component_values[c]+=D(r['baseline_share'])*(D(self.offers[s,c]['unit_cost_usd'])+D(self.lanes[s,self.factory]['cost_usd_per_unit']))
        self.standard={(self.factory,c):v for c,v in component_values.items()}
        for p in self.products:
            cost=sum((component_values[c]*q for c,q in self.bom[p].items()),ZERO)+D(self.routes[p]['conversion_cost_usd_per_unit'])
            self.standard[self.factory,p]=cost
            for wh in self.warehouses:self.standard[wh,p]=cost+D(self.lanes[self.factory,wh]['cost_usd_per_unit'])
        check(isinstance(config['service_floors'],list),'service_floors must be a list')
        for floor in config['service_floors']:
            fields(floor,'product_id warehouse_id week_start min_fill_rate','service floor')
            check(floor['product_id'] is None or floor['product_id'] in self.products,'Unknown product floor')
            check(floor['warehouse_id'] is None or floor['warehouse_id'] in self.warehouses,'Unknown warehouse floor')
            check(floor['week_start'] is None or floor['week_start'] in self.weeks,'Unknown week floor')
            number(floor['min_fill_rate'],'service floor',1)
        check(isinstance(config['terminal_inventory_floors'],list),'terminal floors must be a list')
        seen=set()
        for floor in config['terminal_inventory_floors']:
            fields(floor,'facility_id item_id min_units','terminal floor')
            key=floor['facility_id'],floor['item_id']
            check(key in self.standard and key not in seen,'Unknown/duplicate terminal stock floor')
            seen.add(key);whole(floor['min_units'],'terminal min units')

    def date_at(self,i):return (date.fromisoformat(self.weeks[0])+timedelta(weeks=i)).isoformat()

    def selected_demand(self,floor):
        return [key for key in self.demand if
            (floor['product_id'] is None or key[2]==floor['product_id']) and
            (floor['warehouse_id'] is None or key[1]==floor['warehouse_id']) and
            (floor['week_start'] is None or self.weeks[key[0]]==floor['week_start'])]


def standard_accounting(data,run):
    holding=ZERO; weekly=[]
    for i,week in enumerate(data.weeks):
        stock=ZERO
        for ledger,location,item in [('component_inventory','facility_id','component_id'),('factory_inventory','facility_id','product_id'),('warehouse_inventory','warehouse_id','product_id')]:
            stock+=sum((r['ending_units']*data.standard[r[location],r[item]] for r in run[ledger] if r['week_start']==week),ZERO)
        transit=sum((s['quantity_units']*data.standard[s['warehouse_id'],s['product_id']] for s in run['shipments'] if s['release_index']<=i<s['arrival_index']),ZERO)
        cost=(stock+transit)*data.holding_rate;holding+=cost
        weekly.append(dict(week_start=week,standard_on_hand_value=stock,standard_transit_value=transit,holding_cost=cost))
    purchase=sum((o['quantity_units']*o['unit_price'] for o in run['orders']),ZERO)
    inbound=sum((o['quantity_units']*o['inbound_rate'] for o in run['orders']),ZERO)
    conversion=run['totals']['conversion_cost'];outbound=run['totals']['outbound_transport_cost']
    objective=purchase+inbound+conversion+outbound+holding
    return dict(purchasing_commitments=purchase,inbound_freight_commitments=inbound,conversion_cost=conversion,
        outbound_freight_cost=outbound,holding_cost=holding,total_committed_modeled_cost=objective,
        sunk_firm_commitments=data.sunk_firm,avoidable_modeled_cost=objective-data.sunk_firm,
        opening_inventory_sunk_standard_value=sum((q*data.standard[key] for key,q in data.initial.items()),ZERO),
        terminal_standard_inventory_value=weekly[-1]['standard_on_hand_value']+weekly[-1]['standard_transit_value'],
        pending_purchase_and_freight_commitment=sum((o['quantity_units']*(o['unit_price']+o['inbound_rate']) for o in run['pending_orders']),ZERO),weekly=weekly)


def model_accounting(data,run):
    result=standard_accounting(data,run)
    result['holding_basis']=data.config['holding_basis']
    result['terminal_owned_inventory_value']=result['terminal_standard_inventory_value']
    if data.config['holding_basis']=='moving_average_exact':
        from phase4.valuation import value_run
        empty=dict(extra_inventory=[],holding_cost=ZERO,acquisition_cost=ZERO,material_acquisition_cost=ZERO,inbound_acquisition_cost=ZERO)
        valued=value_run(data.company,run,empty,D(data.config['annual_holding_cost_rate']))
        result['holding_cost']=valued['total_holding_cost']
        result['total_committed_modeled_cost']=valued['gross_committed_resource_cost']
        result['avoidable_modeled_cost']=result['total_committed_modeled_cost']-data.sunk_firm
        result['terminal_owned_inventory_value']=valued['terminal_owned_inventory_value']
        result['inventory_values']=valued['inventory_values']
        result['weekly']=valued['weekly']
    return result
