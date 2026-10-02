"""Exact moving-average holding MILP using bounded integer/continuous products.

For integer q = sum(2**k b_k), u*q = sum(2**k a_k), where
0<=a_k<=U*b_k, a_k<=u, a_k>=u-U*(1-b_k). This is exact, not a
piecewise approximation, because b_k is binary and 0<=u<=U.
"""
import math
import pulp
from phase2.engine import D, ZERO, check
from phase5.formulation import WeeklyModel


class ExactMovingAverageModel(WeeklyModel):
    def __init__(self,data):
        super().__init__(data)
        self.warm_steps=[];self.bits={};self.products_cache={};self.serial=0
        d=data;m=self.problem
        self.component_unit_bound={c:max([d.standard[d.factory,c]]+
            [r['unit_price']+r['freight'] for k,r in d.inbound.items() if k[2]==c]+
            [o['unit_price']+o['inbound_rate'] for o in d.firm if o['component_id']==c]) for c in d.components}
        self.factory_unit_bound={p:sum((self.component_unit_bound[c]*q for c,q in d.bom[p].items()),ZERO)+D(d.routes[p]['conversion_cost_usd_per_unit']) for p in d.products}
        component_qty_bound={c:d.initial.get((d.factory,c),0)+sum(o['quantity_units'] for o in d.firm if o['component_id']==c)+sum(cap for (i,s,item),cap in d.cap.items() if item==c) for c in d.components}
        product_qty_bound={p:sum(q for (loc,item),q in d.initial.items() if item==p)+d.n*int(d.hours/D(d.routes[p]['hours_per_unit'])) for p in d.products}
        cv={};fv={};wv={};shipment_value={};self.cost_inventory={}
        for i in range(d.n):
            component_units={}
            for c in d.components:
                before_quantity=((self.ic[i-1,c] if i else d.initial.get((d.factory,c),0))+
                    pulp.lpSum(v for k,v in self.q.items() if k[2]==c and d.inbound[k]['arrival']==i)+sum(o['quantity_units'] for o in d.firm if o['component_id']==c and o['receipt_index']==i))
                before_value=((cv[i-1,c] if i else d.initial.get((d.factory,c),0)*float(d.standard[d.factory,c]))+
                    pulp.lpSum(v*float(d.inbound[k]['unit_price']+d.inbound[k]['freight']) for k,v in self.q.items() if k[2]==c and d.inbound[k]['arrival']==i)+
                    sum(o['quantity_units']*float(o['unit_price']+o['inbound_rate']) for o in d.firm if o['component_id']==c and o['receipt_index']==i))
                q=self.quantity(before_quantity,component_qty_bound[c])
                unit=self.average(before_value,q,self.component_unit_bound[c],component_qty_bound[c])
                component_units[c]=unit
                cv[i,c]=self.multiply(unit,self.ic[i,c],self.component_unit_bound[c],component_qty_bound[c])
            for p in d.products:
                production_value=pulp.lpSum(self.multiply(component_units[c],self.z[i,p],self.component_unit_bound[c],int(d.hours/D(d.routes[p]['hours_per_unit'])))*per for c,per in d.bom[p].items())+self.z[i,p]*float(D(d.routes[p]['conversion_cost_usd_per_unit']))
                q=self.quantity((self.ip[i-1,p] if i else d.initial.get((d.factory,p),0))+self.z[i,p],product_qty_bound[p])
                value=(fv[i-1,p] if i else d.initial.get((d.factory,p),0)*float(d.standard[d.factory,p]))+production_value
                unit=self.average(value,q,self.factory_unit_bound[p],product_qty_bound[p])
                fv[i,p]=self.multiply(unit,self.ip[i,p],self.factory_unit_bound[p],product_qty_bound[p])
                for key,x in self.x.items():
                    if key[0]==i and key[2]==p:
                        route=d.outbound[key[0],key[1],key[3]]
                        shipment_value[key]=self.multiply(unit,x,self.factory_unit_bound[p],product_qty_bound[p])+x*float(route['freight'])
                for wh in d.warehouses:
                    arrivals=[key for key in self.x if key[1]==wh and key[2]==p and d.outbound[key[0],wh,key[3]]['arrival']==i]
                    q=self.quantity((self.iw[i-1,wh,p] if i else d.initial.get((wh,p),0))+pulp.lpSum(self.x[k] for k in arrivals),product_qty_bound[p])
                    value=(wv[i-1,wh,p] if i else d.initial.get((wh,p),0)*float(d.standard[wh,p]))+pulp.lpSum(shipment_value[k] for k in arrivals)
                    upper=max(d.standard[wh,p],self.factory_unit_bound[p]+max(r['freight'] for k,r in d.outbound.items() if k[1]==wh))
                    unit=self.average(value,q,upper,product_qty_bound[p])
                    wv[i,wh,p]=self.multiply(unit,self.iw[i,wh,p],upper,product_qty_bound[p])
        holding=pulp.lpSum([*cv.values(),*fv.values(),*wv.values()])+pulp.lpSum(value*(min(d.n,d.outbound[k[0],k[1],k[3]]['arrival'])-k[0]) for k,value in shipment_value.items())
        committed=(float(d.sunk_firm)+pulp.lpSum(v*float(d.inbound[k]['unit_price']+d.inbound[k]['freight']) for k,v in self.q.items())+
            pulp.lpSum(v*float(D(d.routes[k[1]]['conversion_cost_usd_per_unit'])) for k,v in self.z.items())+
            pulp.lpSum(v*float(d.outbound[k[0],k[1],k[3]]['freight']) for k,v in self.x.items()))
        m.setObjective(committed+float(d.holding_rate)*holding)
        self.cost_inventory=dict(component=cv,factory=fv,warehouse=wv)

    def quantity(self,expression,bound):
        self.serial+=1
        q=pulp.LpVariable(f'cost_quantity_{self.serial}',0,bound,cat='Integer')
        self.problem+=q==expression
        self.warm_steps.append(('quantity',q,expression))
        return q

    def average(self,value_expression,quantity,upper,quantity_bound):
        self.serial+=1
        unit=pulp.LpVariable(f'average_cost_{self.serial}',0,float(upper))
        self.warm_steps.append(('average',unit,value_expression,quantity))
        self.problem+=self.multiply(unit,quantity,upper,quantity_bound)==value_expression
        return unit

    def multiply(self,unit,quantity,upper,quantity_bound):
        cache=(unit.name,quantity.name)
        if cache in self.products_cache:return self.products_cache[cache]
        if quantity.name not in self.bits:
            bound=max(0,int(quantity_bound));quantity.upBound=min(quantity.upBound,bound) if quantity.upBound is not None else bound
            bits=[]
            for k in range(max(1,bound.bit_length())):
                self.serial+=1;b=pulp.LpVariable(f'quantity_bit_{self.serial}',cat='Binary')
                bits.append(b);self.warm_steps.append(('bit',b,quantity,k))
            self.problem+=quantity==pulp.lpSum((2**k)*b for k,b in enumerate(bits))
            self.bits[quantity.name]=bits
        terms=[]
        for k,b in enumerate(self.bits[quantity.name]):
            self.serial+=1;a=pulp.LpVariable(f'cost_product_{self.serial}',0,float(upper))
            self.problem+=a<=float(upper)*b
            self.problem+=a<=unit
            self.problem+=a>=unit-float(upper)*(1-b)
            self.warm_steps.append(('product',a,unit,b))
            terms.append((2**k)*a)
        expression=pulp.lpSum(terms);self.products_cache[cache]=expression
        return expression

    def seed(self,run):
        original={v:(v.lowBound,v.upBound) for v in [*self.q.values(),*self.y.values(),*self.z.values(),*self.x.values(),*self.f.values(),*self.ic.values(),*self.ip.values(),*self.iw.values()]}
        self.bind_benchmark(run)
        for var,(low,high) in original.items():
            var.setInitialValue(var.lowBound);var.lowBound=low;var.upBound=high
        for step in self.warm_steps:
            kind,var,*args=step
            if kind=='quantity':value=pulp.value(args[0])
            elif kind=='average':value=pulp.value(args[0])/pulp.value(args[1]) if pulp.value(args[1]) else 0
            elif kind=='bit':value=(round(pulp.value(args[0]))>>args[1])&1
            else:value=pulp.value(args[0])*pulp.value(args[1])
            # Clamp only tiny arithmetic noise around proven physical bounds.
            if var.lowBound is not None and value<var.lowBound and value>var.lowBound-1e-6:value=var.lowBound
            if var.upBound is not None and value>var.upBound and value<var.upBound+1e-6:value=var.upBound
            var.setInitialValue(value)
        for constraint in self.problem.constraints.values():
            residual=constraint.value()
            check(abs(residual)<1e-4 if constraint.sense==0 else residual*constraint.sense>=-1e-4,'Exact-model warm start does not satisfy constraints')

        self.has_warm_start=True
