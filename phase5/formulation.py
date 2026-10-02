"""Pure weekly MILP formulation. No simulation, file output or orchestration."""
import pulp
from phase2.engine import D,check


class WeeklyModel:
    def __init__(self,data):
        self.data=data; self.problem=pulp.LpProblem('ReactiveSupplyChain',pulp.LpMinimize)
        self.variables={};serial=0
        def variable(group,key,upper=None,binary=False,cost=0):
            nonlocal serial
            serial+=1
            v=pulp.LpVariable(f'{group}_{serial}',lowBound=0,upBound=upper,cat='Binary' if binary else 'Integer')
            self.variables[group,key]=v
            return v
        self.q={k:variable('order',k,data.cap[k[:3]]) for k in data.inbound}
        self.y={k:variable('order_open',k,1,True) for k in data.cap}
        self.z={(i,p):variable('production',(i,p),int(data.hours/D(data.routes[p]['hours_per_unit']))) for i in range(data.n) for p in data.products}
        self.x={(i,wh,p,mode):variable('shipment',(i,wh,p,mode)) for i,wh,mode in data.outbound for p in data.products}
        self.f={k:variable('fulfilled',k,demand) for k,demand in data.demand.items()}
        self.ic={(i,c):variable('component_stock',(i,c)) for i in range(data.n) for c in data.components}
        self.ip={(i,p):variable('factory_stock',(i,p)) for i in range(data.n) for p in data.products}
        self.iw={k:variable('warehouse_stock',k) for k in data.demand}
        m=self.problem
        for (i,s,c),cap in data.cap.items():
            qty=pulp.lpSum(v for k,v in self.q.items() if k[:3]==(i,s,c))
            m+=qty<=cap*self.y[i,s,c],f'capacity_{i}_{s}_{c}'
            m+=qty>=int(data.offers[s,c]['minimum_order_units'])*self.y[i,s,c],f'moq_{i}_{s}_{c}'
        for i in range(data.n):
            for c in data.components:
                initial=self.ic[i-1,c] if i else data.initial.get((data.factory,c),0)
                firm=sum(o['quantity_units'] for o in data.firm if o['receipt_index']==i and o['component_id']==c)
                receipts=pulp.lpSum(v for k,v in self.q.items() if k[2]==c and data.inbound[k]['arrival']==i)
                use=pulp.lpSum(self.z[i,p]*data.bom[p].get(c,0) for p in data.products)
                m+=self.ic[i,c]==initial+firm+receipts-use,f'component_balance_{i}_{c}'
            m+=pulp.lpSum(self.z[i,p]*float(D(data.routes[p]['hours_per_unit'])) for p in data.products)<=float(data.hours),f'labor_{i}'
            for p in data.products:
                initial=self.ip[i-1,p] if i else data.initial.get((data.factory,p),0)
                dispatch=pulp.lpSum(v for k,v in self.x.items() if k[0]==i and k[2]==p)
                m+=self.ip[i,p]==initial+self.z[i,p]-dispatch,f'factory_balance_{i}_{p}'
                for wh in data.warehouses:
                    initial=self.iw[i-1,wh,p] if i else data.initial.get((wh,p),0)
                    arrivals=pulp.lpSum(v for k,v in self.x.items() if k[1]==wh and k[2]==p and data.outbound[k[0],wh,k[3]]['arrival']==i)
                    m+=self.iw[i,wh,p]==initial+arrivals-self.f[i,wh,p],f'warehouse_balance_{i}_{wh}_{p}'
        m+=pulp.lpSum(self.f.values())>=float(data.target*sum(data.demand.values())),'aggregate_service'
        for j,floor in enumerate(data.config['service_floors']):
            keys=data.selected_demand(floor)
            m+=pulp.lpSum(self.f[k] for k in keys)>=float(D(floor['min_fill_rate'])*sum(data.demand[k] for k in keys)),f'service_floor_{j}'
        for j,floor in enumerate(data.config['terminal_inventory_floors']):
            facility,item=floor['facility_id'],floor['item_id']
            stock=(self.ic[data.n-1,item] if item in data.components else self.ip[data.n-1,item]) if facility==data.factory else self.iw[data.n-1,facility,item]
            m+=stock>=floor['min_units'],f'terminal_floor_{j}'
        costs=[float(data.sunk_firm)]
        costs += [v*float(data.inbound[k]['unit_price']+data.inbound[k]['freight']) for k,v in self.q.items()]
        costs += [v*float(D(data.routes[k[1]]['conversion_cost_usd_per_unit'])) for k,v in self.z.items()]
        for k,v in self.x.items():
            i,wh,p,mode=k;route=data.outbound[i,wh,mode]
            transit_weeks=min(data.n,route['arrival'])-i
            costs.append(v*float(route['freight']+data.standard[wh,p]*data.holding_rate*transit_weeks))
        costs += [v*float(data.standard[data.factory,k[1]]*data.holding_rate) for k,v in self.ic.items()]
        costs += [v*float(data.standard[data.factory,k[1]]*data.holding_rate) for k,v in self.ip.items()]
        costs += [v*float(data.standard[k[1],k[2]]*data.holding_rate) for k,v in self.iw.items()]
        m+=pulp.lpSum(costs),'total_committed_cost_standard_holding'

    def export_plan(self):
        data=self.data
        def qty(v):
            value=v.value();check(value is not None and abs(value-round(value))<1e-5,'Solver has no integral executable incumbent')
            return int(round(value))
        return dict(orders=[dict(week_start=data.weeks[i],supplier_id=s,component_id=c,route_id=mode,quantity_units=qty(v)) for (i,s,c,mode),v in self.q.items() if qty(v)],
            production=[dict(week_start=data.weeks[i],product_id=p,quantity_units=qty(v)) for (i,p),v in self.z.items() if qty(v)],
            shipments=[dict(week_start=data.weeks[i],warehouse_id=wh,product_id=p,route_id=mode,quantity_units=qty(v)) for (i,wh,p,mode),v in self.x.items() if qty(v)],
            fulfillment=[dict(week_start=data.weeks[i],warehouse_id=wh,product_id=p,quantity_units=qty(v)) for (i,wh,p),v in self.f.items() if qty(v)],
            metadata=dict(source='CBC integer solution',holding_basis=data.config['holding_basis']))

    def bind_benchmark(self,run):
        """Fix every variable to a replayed benchmark; feasibility is then testable by CBC."""
        d=self.data
        q={(o['release_index'],o['supplier_id'],o['component_id'],o['route_id']):o['quantity_units'] for o in run['orders'] if o['kind']=='new'}
        x={(s['release_index'],s['warehouse_id'],s['product_id'],s['route_id']):s['quantity_units'] for s in run['shipments']}
        z={(d.index[r['week_start']],r['product_id']):r['produced_units'] for r in run['production']}
        f={(d.index[r['week_start']],r['warehouse_id'],r['product_id']):r['fulfilled_units'] for r in run['warehouse_inventory']}
        check(set(q)<=set(self.q) and set(x)<=set(self.x),'Benchmark routes cannot be represented in this model')
        stocks=[(self.ic,{(d.index[r['week_start']],r['component_id']):r['ending_units'] for r in run['component_inventory']}),
                (self.ip,{(d.index[r['week_start']],r['product_id']):r['ending_units'] for r in run['factory_inventory']}),
                (self.iw,{(d.index[r['week_start']],r['warehouse_id'],r['product_id']):r['ending_units'] for r in run['warehouse_inventory']})]
        for variables,values in [(self.q,q),(self.x,x),(self.z,z),(self.f,f),*stocks]:
            for key,v in variables.items():v.lowBound=v.upBound=values.get(key,0)
        for k,v in self.y.items():v.lowBound=v.upBound=int(any(qty for key,qty in q.items() if key[:3]==k))
