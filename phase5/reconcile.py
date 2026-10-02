"""Compare independently executed ledgers with every model decision and stock variable."""
from phase2.engine import D,check
from phase5.data import model_accounting


def reconcile(model,run,solver_info):
    d=model.data
    actual={}
    for o in run['orders']:
        if o['kind']=='new':actual[o['release_index'],o['supplier_id'],o['component_id'],o['route_id']]=o['quantity_units']
    def same(variables,observed,label):
        check(set(observed)<=set(variables),label+': unexpected execution rows')
        for key,var in variables.items():
            check(abs(var.value()-observed.get(key,0))<1e-5,label+': solver/replay mismatch '+str(key))
    same(model.q,actual,'orders')
    same(model.z,{(d.index[r['week_start']],r['product_id']):r['produced_units'] for r in run['production']},'production')
    same(model.x,{(s['release_index'],s['warehouse_id'],s['product_id'],s['route_id']):s['quantity_units'] for s in run['shipments']},'shipments')
    same(model.f,{(d.index[r['week_start']],r['warehouse_id'],r['product_id']):r['fulfilled_units'] for r in run['warehouse_inventory']},'fulfillment')
    for variables,ledger,keys in [(model.ic,'component_inventory',['component_id']),(model.ip,'factory_inventory',['product_id']),
                                  (model.iw,'warehouse_inventory',['warehouse_id','product_id'])]:
        same(variables,{(d.index[r['week_start']],*[r[k] for k in keys]):r['ending_units'] for r in run[ledger]},ledger)
    for constraint in model.problem.constraints.values():
        residual=constraint.value()
        check((abs(residual)<1e-4 if constraint.sense==0 else residual*constraint.sense>=-1e-4),'Solver constraint residual invalid')
    cost=model_accounting(d,run)
    if hasattr(model,'cost_inventory'):
        import pulp
        valued={(r['week_start'],r['facility_id'],r['item_id']):r['inventory_value'] for r in cost['inventory_values']}
        for kind,variables in model.cost_inventory.items():
            for key,expression in variables.items():
                i=key[0];location=key[1] if kind=='warehouse' else d.factory;item=key[-1]
                check(abs(D(pulp.value(expression))-valued.get((d.weeks[i],location,item),D(0)))<D('0.001'),
                      'Moving-average inventory value/replay mismatch '+str(key))
    check(abs(D(solver_info['objective'])-cost['total_committed_modeled_cost'])<D('0.001'),'Objective/replay cost mismatch')
    return dict(status='PASS',quantity_tolerance=1e-5,cost_tolerance_usd=0.001,
        checks=['every purchase, production, shipment and fulfillment decision','all weekly component, factory and warehouse inventories',
                'all mathematical constraint residuals','booked order timing and existing firm receipts via engine audit',
                'committed objective including pending POs, freight, conversion and configured holding'],accounting=cost)
