"""Deterministic weekly baseline. Standard library only; no input writes.

Scenario effects implement a small injected interface; the baseline uses IdentityEffects.
See PLANNING_POLICIES.md for the intentionally heuristic planning decisions.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validate import validate

D = lambda x: Decimal(str(x))
ZERO = Decimal(0)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value):
    return int(D(value))


def floor(value):
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def apportioned(total, weights):
    """Whole units, exact total, stable ID tie-break; zero weights stay zero."""
    total_weight = sum((D(v) for v in weights.values()), ZERO)
    if total == 0 or total_weight == 0:
        return {k: 0 for k in weights}
    quotas = {k: D(total) * D(v) / total_weight for k, v in weights.items()}
    result = {k: floor(v) for k, v in quotas.items()}
    ranked = sorted(weights, key=lambda k: (-(quotas[k] - result[k]), k))
    for key in ranked[:total - sum(result.values())]:
        result[key] += 1
    return result


def input_hashes(root=ROOT):
    paths = sorted((Path(root) / 'data').glob('*.csv'))
    paths += [Path(root) / 'model_config.json', Path(root) / 'schema.json',
              Path(root) / 'phase2' / 'planning_policy.json']
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def load_company(root=ROOT, extension=None):
    root = Path(root)
    errors, tables = validate(root)
    check(not errors, 'Phase 1 validation failed: ' + '; '.join(errors))
    company = dict(tables=tables, config=json.loads((root / 'model_config.json').read_text()),
                   policy=json.loads((root / 'phase2/planning_policy.json').read_text()),
                   extension_assumption=None)
    check(company['config']['horizon_weeks'] == len(tables['calendar']), 'Configured horizon disagrees with calendar')
    if extension:
        # Every additional week/product/warehouse must be explicitly supplied.
        with Path(extension).open(newline='') as file:
            reader = csv.DictReader(file)
            check(reader.fieldnames == ['week_start', 'warehouse_id', 'product_id', 'demand_units'],
                  'Extension must have demand.csv headers')
            extra = list(reader)
        check(bool(extra), 'Extension is empty')
        last = date.fromisoformat(tables['calendar'][-1]['week_start'])
        dates = sorted({r['week_start'] for r in extra})
        for i, week in enumerate(dates, 1):
            check(date.fromisoformat(week) == last + timedelta(weeks=i), 'Extension weeks must be consecutive after the horizon')
        tables['calendar'].extend({'week_start': w} for w in dates)
        tables['demand'].extend(extra)
        company['config']['horizon_weeks'] += len(dates)
        company['extension_assumption'] = dict(source=str(Path(extension).resolve()),
                                                sha256=hashlib.sha256(Path(extension).read_bytes()).hexdigest(),
                                                weeks_added=len(dates))
    validate_company(company)
    return company


def validate_company(company):
    t, cfg, policy = company['tables'], company['config'], company['policy']
    expected = dict(time_bucket='week', demand_policy='lost_sales', production_lead_time_weeks=0,
                    receipt_timing='start_of_week', demand_timing='end_of_week',
                    supplier_capacity_scope='independent_per_supplier_component')
    for key, value in expected.items():
        check(cfg.get(key) == value, f'Unsupported model setting {key}')
    weeks = [r['week_start'] for r in t['calendar']]
    check(cfg['snapshot_date'] == weeks[0], 'Snapshot must match first week')
    check(cfg['horizon_weeks'] == len(weeks), 'Horizon mismatch')
    check(len(set(weeks)) == len(weeks), 'Duplicate calendar week')
    check(all(date.fromisoformat(w) == date.fromisoformat(weeks[0]) + timedelta(weeks=i)
              for i, w in enumerate(weeks)), 'Nonconsecutive calendar')
    factories = [r for r in t['facilities'] if r['facility_type'] == 'factory']
    check(len(factories) == 1, 'This engine supports exactly one factory')
    factory = factories[0]['facility_id']
    warehouses = sorted(r['facility_id'] for r in t['facilities'] if r['facility_type'] == 'warehouse')
    products = sorted(r['item_id'] for r in t['items'] if r['item_type'] == 'finished_good')
    check(set(policy['production_priority']) == set(products) and len(policy['production_priority']) == len(products),
          'production_priority must include every finished product exactly once')
    for key, value in dict(purchasing_rule='rolling_inventory_position',
                           sourcing_rule='baseline_shares_no_reallocation',
                           warehouse_allocation_rule='proportional_largest_remainder',
                           warehouse_buffer_rule='replenish_to_safety_target').items():
        check(policy.get(key) == value, f'Unsupported planning policy {key}')
    extra = policy['purchase_coverage_extra_weeks']
    check(type(extra) is int and extra >= 1, 'Coverage extra weeks must be a positive integer')
    routes = {r['product_id']: r for r in t['production']}
    check(len(routes) == len(t['production']) and set(routes) == set(products), 'Exactly one route per product required')
    check(all(r['facility_id'] == factory for r in routes.values()), 'Invalid route factory')
    lanes = {(r['origin_id'], r['destination_id']): r for r in t['transportation']}
    check(len(lanes) == len(t['transportation']), 'Parallel lanes require a routing policy; unsupported')
    for wh in warehouses:
        check((factory, wh) in lanes and integer(lanes[factory, wh]['lead_time_weeks']) >= 1,
              'Each warehouse requires a positive-lead-time factory lane')
    for offer in t['supplier_offers']:
        check((offer['supplier_id'], factory) in lanes, 'Supplier offer missing inbound lane')
        check(integer(offer['lead_time_weeks']) >= 1, 'Supplier lead time must be at least one week')
    demand_keys = set()
    for row in t['demand']:
        key = (row['week_start'], row['warehouse_id'], row['product_id'])
        check(key not in demand_keys, 'Duplicate demand key')
        demand_keys.add(key)
        value = D(row['demand_units'])
        check(value.is_finite() and value >= 0 and value == floor(value), 'Demand must be nonnegative whole units')
    check(demand_keys == {(w, wh, p) for w in weeks for wh in warehouses for p in products},
          'Demand must explicitly cover every week/warehouse/product, including zero values')


class IdentityEffects:
    """Zero-change interface, reused by scenario implementations."""
    def capacity(self, supplier, component, week, normal):
        return normal

    def lead_time(self, supplier, component, week, normal):
        return normal

    def purchase_price(self, supplier, component, week, normal):
        return normal

    def freight_price(self, lane, week, normal):
        return normal

    def demand(self, product, warehouse, week, normal):
        return normal


def simulate(company, effects=None, mitigation=None, explicit_plan=None, execution_config=None):
    """Return complete ledgers, using isolated state. Never mutate company/effects."""
    if explicit_plan is not None:
        from phase5.execution import replay
        check(mitigation is None, 'Explicit plans cannot be replaced by heuristic mitigation policies')
        check(effects is not None and hasattr(effects, 'config') and execution_config is not None,
              'Explicit execution requires scenario effects and its validated model configuration')
        return replay(company, effects.config, execution_config, explicit_plan)
    validate_company(company)
    company = copy.deepcopy(company)
    effects = effects or IdentityEffects()
    t, policy = company['tables'], company['policy']
    weeks = [r['week_start'] for r in t['calendar']]
    n = len(weeks)
    week_index = {w: i for i, w in enumerate(weeks)}
    date_at = lambda i: (date.fromisoformat(weeks[0]) + timedelta(weeks=i)).isoformat()
    factory_row = next(r for r in t['facilities'] if r['facility_type'] == 'factory')
    factory = factory_row['facility_id']
    factory_hours = D(factory_row['weekly_production_hours'])
    warehouses = sorted(r['facility_id'] for r in t['facilities'] if r['facility_type'] == 'warehouse')
    products = policy['production_priority']
    components = sorted(r['item_id'] for r in t['items'] if r['item_type'] == 'component')
    items = {r['item_id']: r for r in t['items']}
    bom = {p: {r['component_id']: integer(r['quantity_per_product']) for r in t['bom'] if r['product_id'] == p}
           for p in products}
    routes = {r['product_id']: r for r in t['production']}
    lanes = {(r['origin_id'], r['destination_id']): r for r in t['transportation']}
    offers = {(r['supplier_id'], r['component_id']): r for r in t['supplier_offers']}
    shares = {(r['supplier_id'], r['component_id']): D(r['baseline_share']) for r in t['sourcing_policy']}
    inv = defaultdict(int, {(r['facility_id'], r['item_id']): integer(r['on_hand_units']) for r in t['inventory']})
    safety = defaultdict(int, {(r['facility_id'], r['item_id']): integer(r['safety_stock_target_units']) for r in t['inventory']})
    demand = {(week_index[r['week_start']], r['warehouse_id'], r['product_id']):
              effects.demand(r['product_id'], r['warehouse_id'], r['week_start'], integer(r['demand_units']))
              for r in t['demand']}
    orders, transfers = [], []
    result = {k: [] for k in ['component_inventory', 'factory_inventory', 'warehouse_inventory', 'production',
                              'supplier_capacity', 'receipts', 'weekly', 'material_consumption',
                              'order_pipeline', 'shipment_pipeline']}
    for row in t['open_orders']:
        offer = offers[row['supplier_id'], row['component_id']]
        lane = lanes[row['supplier_id'], factory]
        orders.append(dict(order_id=row['order_id'], kind='firm', supplier_id=row['supplier_id'],
                           component_id=row['component_id'], destination_id=factory, quantity_units=integer(row['quantity_units']),
                           release_week=None, release_index=None, receipt_week=row['receipt_week_start'],
                           receipt_index=week_index[row['receipt_week_start']], lead_time_weeks=None,
                           unit_price=D(offer['unit_cost_usd']), inbound_rate=D(lane['cost_usd_per_unit']),
                           lane_id=lane['lane_id'], route_id=lane['lane_id']))
    initial_orders = sum(o['quantity_units'] for o in orders)
    for i, week in enumerate(weeks):
        beginning = inv.copy()
        component_receipts = defaultdict(int)
        warehouse_receipts = defaultdict(int)
        purchasing = inbound = conversion = outbound = ZERO
        new_purchase_commitment = new_freight_commitment = ZERO
        for order in orders:
            if order['receipt_index'] == i:
                qty = order['quantity_units']
                inv[factory, order['component_id']] += qty
                component_receipts[order['component_id']] += qty
                purchasing += qty * order['unit_price']
                inbound += qty * order['inbound_rate']
                result['receipts'].append(dict(week_start=week, order_id=order['order_id'], kind=order['kind'],
                                                supplier_id=order['supplier_id'], component_id=order['component_id'],
                                                quantity_units=qty, purchase_cost=qty * order['unit_price'],
                                                inbound_transport_cost=qty * order['inbound_rate']))
        for shipment in transfers:
            if shipment['arrival_index'] == i:
                key = (shipment['warehouse_id'], shipment['product_id'])
                inv[key] += shipment['quantity_units']
                warehouse_receipts[key] += shipment['quantity_units']
        # PO release decisions. Current week's production has not consumed stock yet.
        for c in components:
            source_weights = {s: share for (s, item), share in shares.items() if item == c and share > 0}
            effective_leads = {s: effects.lead_time(s, c, week, integer(offers[s, c]['lead_time_weeks'])) for s in source_weights}
            coverage = max(effective_leads.values()) + policy['purchase_coverage_extra_weeks']
            gross = sum(demand.get((k + integer(lanes[factory, wh]['lead_time_weeks']), wh, p), 0) * bom[p].get(c, 0)
                        for k in range(i, min(n, i + coverage)) for wh in warehouses for p in products)
            position = inv[factory, c] + sum(o['quantity_units'] for o in orders if o['component_id'] == c and o['receipt_index'] > i)
            target = gross + safety[factory, c]
            request = max(0, target - position)
            split = apportioned(request, source_weights)
            component_offers = {s: offers[s, c] for s, item in offers if item == c}
            capacities = {s: effects.capacity(s, c, week, integer(o['weekly_capacity_units']))
                          for s, o in component_offers.items()}
            moqs = {s: integer(o['minimum_order_units']) for s, o in component_offers.items()}
            original_releases = {s: min(capacities[s], max(moqs[s], split.get(s, 0)))
                                 if split.get(s, 0) and capacities[s] >= moqs[s] else 0
                                 for s in component_offers}
            releases = (mitigation.reallocate(week, c, split, original_releases, capacities, moqs)
                        if mitigation else original_releases)
            check(set(releases) == set(original_releases), 'Mitigation cannot add unqualified suppliers')
            for s, item in sorted(offers):
                if item != c:
                    continue
                offer = offers[s, c]
                normal = integer(offer['weekly_capacity_units'])
                cap = capacities[s]
                moq = integer(offer['minimum_order_units'])
                requested = split.get(s, 0)
                released = releases[s]
                check(type(released) is int and original_releases[s] <= released <= cap,
                      'Mitigation must preserve planned orders and stay within remaining capacity')
                check(not released or released >= moq, 'Mitigation order below MOQ')
                lead = effects.lead_time(s, c, week, integer(offer['lead_time_weeks']))
                lane = lanes[s, factory]
                freight = effects.freight_price(lane['lane_id'], week, D(lane['cost_usd_per_unit']))
                route_id = lane['lane_id']
                if mitigation:
                    lead, freight, route_id = mitigation.inbound(week, s, c, lane, lead, freight)
                result['supplier_capacity'].append(dict(week_start=week, supplier_id=s, component_id=c,
                    normal_capacity_units=normal, effective_capacity_units=cap, requested_share_units=requested,
                    released_units=released, utilization=D(released) / cap if cap else None,
                    requested_but_not_released_units=max(0, requested-released),
                    moq_extra_units=max(0, original_releases[s]-requested), lead_time_weeks=lead,
                    inventory_position_units=position, target_units=target, net_request_units=request,
                    original_release_units=original_releases[s], reallocated_units=released-original_releases[s],
                    remaining_capacity_units=cap-released))
                if released:
                    lane = lanes[s, factory]
                    price = effects.purchase_price(s, c, week, D(offer['unit_cost_usd']))
                    orders.append(dict(order_id=f'N{i+1:03d}-{s}-{c}', kind='new', supplier_id=s, component_id=c,
                        destination_id=factory, quantity_units=released, release_week=week, release_index=i,
                        receipt_week=date_at(i+lead), receipt_index=i+lead, lead_time_weeks=lead,
                        unit_price=price, inbound_rate=freight, lane_id=lane['lane_id'], route_id=route_id))
                    new_purchase_commitment += released * price
                    new_freight_commitment += released * freight
        hours_left = factory_hours
        consumed = defaultdict(int)
        production_qty, dispatch_qty = defaultdict(int), defaultdict(int)
        for p in products:
            requests = {}
            for wh in warehouses:
                lane = lanes[factory, wh]
                outbound_lead = integer(lane['lead_time_weeks'])
                if mitigation:
                    outbound_lead, _, _ = mitigation.outbound(week, lane, outbound_lead,
                        effects.freight_price(lane['lane_id'], week, D(lane['cost_usd_per_unit'])))
                arrival = i + outbound_lead
                if arrival >= n:
                    requests[wh] = 0
                    continue
                projected = inv[wh, p]
                for k in range(i, arrival):
                    if k > i:
                        projected += sum(s['quantity_units'] for s in transfers
                                         if s['arrival_index'] == k and s['warehouse_id'] == wh and s['product_id'] == p)
                    projected = max(0, projected - demand[k, wh, p])
                if arrival > i:  # Arrivals due this week are already in physical inventory.
                    projected += sum(s['quantity_units'] for s in transfers
                                     if s['arrival_index'] == arrival and s['warehouse_id'] == wh and s['product_id'] == p)
                requests[wh] = max(0, demand[arrival, wh, p] + safety[wh, p] - projected)
            total_request = sum(requests.values())
            needed = max(0, total_request - inv[factory, p])
            material_limits = {c: inv[factory, c] // q for c, q in bom[p].items()}
            hours_per_unit = D(routes[p]['hours_per_unit'])
            labor_limit = floor(hours_left / hours_per_unit)
            made = min([needed, labor_limit] + list(material_limits.values()))
            binding = [c for c, limit in material_limits.items() if limit == made] if made < needed else []
            if made < needed and labor_limit == made:
                binding.append('factory_labor')
            used_hours = made * hours_per_unit
            hours_left -= used_hours
            for c, q in bom[p].items():
                qty = made * q
                inv[factory, c] -= qty
                consumed[c] += qty
                result['material_consumption'].append(dict(week_start=week, product_id=p, component_id=c,
                                                           produced_units=made, bom_quantity=q, consumed_units=qty))
            inv[factory, p] += made
            production_qty[p] = made
            conversion_cost = made * D(routes[p]['conversion_cost_usd_per_unit'])
            conversion += conversion_cost
            result['production'].append(dict(week_start=week, product_id=p, warehouse_request_units=total_request,
                requested_production_units=needed, produced_units=made, unmade_requested_units=needed-made,
                labor_hours=used_hours, conversion_cost=conversion_cost, binding_constraints=';'.join(binding),
                material_limits=json.dumps(material_limits, sort_keys=True), labor_limit_units=labor_limit))
            allocations = apportioned(min(total_request, inv[factory, p]), requests)
            for wh, qty in allocations.items():
                if qty == 0:
                    continue
                lane = lanes[factory, wh]
                rate = effects.freight_price(lane['lane_id'], week, D(lane['cost_usd_per_unit']))
                lead = integer(lane['lead_time_weeks'])
                route_id = lane['lane_id']
                if mitigation:
                    lead, rate, route_id = mitigation.outbound(week, lane, lead, rate)
                transfers.append(dict(shipment_id=f'T{i+1:03d}-{wh}-{p}', release_week=week, release_index=i,
                    arrival_week=date_at(i+lead), arrival_index=i+lead, warehouse_id=wh, product_id=p,
                    quantity_units=qty, lead_time_weeks=lead, lane_id=lane['lane_id'], route_id=route_id, unit_freight_rate=rate,
                    outbound_transport_cost=qty*rate))
                inv[factory, p] -= qty
                dispatch_qty[p] += qty
                outbound += qty * rate
                if lead == 0:  # Explicit within-week delivery after production, before demand.
                    inv[wh, p] += qty
                    warehouse_receipts[wh, p] += qty
        fulfilled_total = demand_total = unmet_total = 0
        exposure = ZERO
        for wh in warehouses:
            for p in products:
                requested = demand[i, wh, p]
                fulfilled = min(inv[wh, p], requested)
                inv[wh, p] -= fulfilled
                unmet = requested - fulfilled
                value = unmet * D(items[p]['selling_price_usd'])
                result['warehouse_inventory'].append(dict(week_start=week, warehouse_id=wh, product_id=p,
                    beginning_units=beginning[wh, p], receipts_units=warehouse_receipts[wh, p], demand_units=requested,
                    fulfilled_units=fulfilled, unmet_units=unmet, ending_units=inv[wh, p],
                    unit_fill_rate=D(fulfilled)/requested if requested else None, potential_sales_revenue_exposure=value))
                demand_total += requested
                fulfilled_total += fulfilled
                unmet_total += unmet
                exposure += value
        for c in components:
            result['component_inventory'].append(dict(week_start=week, facility_id=factory, component_id=c,
                beginning_units=beginning[factory, c], receipts_units=component_receipts[c], consumed_units=consumed[c],
                ending_units=inv[factory, c], safety_target_units=safety[factory, c],
                below_safety_units=max(0, safety[factory, c]-inv[factory, c])))
        for p in products:
            result['factory_inventory'].append(dict(week_start=week, facility_id=factory, product_id=p,
                beginning_units=beginning[factory, p], produced_units=production_qty[p], shipped_units=dispatch_qty[p],
                ending_units=inv[factory, p]))
        for s, c in sorted(offers):
            selected = [o for o in orders if o['supplier_id'] == s and o['component_id'] == c]
            result['order_pipeline'].append(dict(week_start=week, supplier_id=s, component_id=c,
                beginning_units=sum(o['quantity_units'] for o in selected if o['receipt_index'] >= i and
                                    (o['release_index'] is None or o['release_index'] < i)),
                released_units=sum(o['quantity_units'] for o in selected if o['release_index'] == i),
                received_units=sum(o['quantity_units'] for o in selected if o['receipt_index'] == i),
                ending_units=sum(o['quantity_units'] for o in selected if o['receipt_index'] > i)))
        for wh in warehouses:
            for p in products:
                selected = [s for s in transfers if s['warehouse_id'] == wh and s['product_id'] == p]
                result['shipment_pipeline'].append(dict(week_start=week, warehouse_id=wh, product_id=p,
                    beginning_units=sum(s['quantity_units'] for s in selected if s['arrival_index'] >= i and s['release_index'] < i),
                    dispatched_units=sum(s['quantity_units'] for s in selected if s['release_index'] == i),
                    received_units=sum(s['quantity_units'] for s in selected if s['arrival_index'] == i),
                    ending_units=sum(s['quantity_units'] for s in selected if s['arrival_index'] > i)))
        result['weekly'].append(dict(week_start=week, demand_units=demand_total, fulfilled_units=fulfilled_total,
            unmet_units=unmet_total, unit_fill_rate=D(fulfilled_total)/demand_total if demand_total else None,
            produced_units=sum(production_qty.values()), factory_hours_used=factory_hours-hours_left,
            factory_hours_available=factory_hours, factory_utilization=(factory_hours-hours_left)/factory_hours,
            purchasing_cost=purchasing, conversion_cost=conversion, inbound_transport_cost=inbound,
            outbound_transport_cost=outbound, transportation_cost=inbound+outbound,
            total_operating_activity_cost=purchasing+conversion+inbound+outbound,
            new_purchase_commitment=new_purchase_commitment, new_inbound_freight_commitment=new_freight_commitment,
            potential_sales_revenue_exposure=exposure))
    result['orders'] = orders
    result['shipments'] = transfers
    result['pending_orders'] = [o for o in orders if o['receipt_index'] >= n]
    result['pending_shipments'] = [s for s in transfers if s['arrival_index'] >= n]
    totals = {k: sum((r[k] for r in result['weekly']), ZERO) for k in result['weekly'][0]
              if k not in ['week_start', 'unit_fill_rate', 'factory_utilization']}
    totals['unit_fill_rate'] = totals['fulfilled_units']/totals['demand_units'] if totals['demand_units'] else None
    totals['factory_utilization'] = totals['factory_hours_used']/totals['factory_hours_available']
    totals['pending_purchase_commitment'] = sum((o['quantity_units']*o['unit_price'] for o in result['pending_orders']), ZERO)
    totals['pending_inbound_freight_commitment'] = sum((o['quantity_units']*o['inbound_rate'] for o in result['pending_orders']), ZERO)
    result['totals'] = totals
    result['metadata'] = dict(weeks=weeks, policy=policy, extension_assumption=company['extension_assumption'],
                              firm_orders_units=initial_orders, planning_type='feasible rule-based; not optimized')
    result['checks'] = audit(result, company)
    return result


def audit(result, company):
    """Independently reconcile ledger flows, balances, timings and physical limits."""
    t = company['tables']
    initial = {(r['facility_id'], r['item_id']): integer(r['on_hand_units']) for r in t['inventory']}
    factory = next(r['facility_id'] for r in t['facilities'] if r['facility_type'] == 'factory')
    weeks = result['metadata']['weeks']
    n = len(weeks)
    offers = {(r['supplier_id'], r['component_id']): r for r in t['supplier_offers']}
    def total(rows, field, **criteria):
        return sum((r[field] for r in rows if all(r[k] == v for k,v in criteria.items())), ZERO)
    for ledger, item_key, inflow, outflow in [
        ('component_inventory', 'component_id', 'receipts_units', 'consumed_units'),
        ('factory_inventory', 'product_id', 'produced_units', 'shipped_units'),
        ('warehouse_inventory', 'product_id', 'receipts_units', 'fulfilled_units')]:
        last = dict(initial)
        for row in result[ledger]:
            facility = row.get('facility_id', row.get('warehouse_id'))
            key = (facility, row[item_key])
            check(row['beginning_units'] == last.get(key, 0), f'{ledger}: opening continuity')
            check(row['beginning_units'] + row[inflow] - row[outflow] == row['ending_units'], f'{ledger}: balance')
            check(all(row[k] >= 0 for k in ['beginning_units', inflow, outflow, 'ending_units']), f'{ledger}: negative flow')
            last[key] = row['ending_units']
    for row in result['component_inventory']:
        check(row['receipts_units'] == total(result['receipts'], 'quantity_units', week_start=row['week_start'], component_id=row['component_id']), 'Component receipt reconciliation')
        check(row['consumed_units'] == total(result['material_consumption'], 'consumed_units', week_start=row['week_start'], component_id=row['component_id']), 'BOM reconciliation')
    bom = {(r['product_id'], r['component_id']): integer(r['quantity_per_product']) for r in t['bom']}
    for row in result['material_consumption']:
        check(row['consumed_units'] == row['produced_units'] * bom[row['product_id'], row['component_id']], 'BOM quantity mismatch')
        check(row['produced_units'] == total(result['production'], 'produced_units', week_start=row['week_start'], product_id=row['product_id']), 'Production consumption mismatch')
    for row in result['factory_inventory']:
        check(row['produced_units'] == total(result['production'], 'produced_units', week_start=row['week_start'], product_id=row['product_id']), 'Factory production mismatch')
        check(row['shipped_units'] == total(result['shipments'], 'quantity_units', release_week=row['week_start'], product_id=row['product_id']), 'Factory dispatch mismatch')
    for row in result['warehouse_inventory']:
        check(row['demand_units'] == row['fulfilled_units'] + row['unmet_units'], 'Demand reconciliation')
        check(row['receipts_units'] == total(result['shipments'], 'quantity_units', arrival_week=row['week_start'], product_id=row['product_id'], warehouse_id=row['warehouse_id']), 'Warehouse receipt reconciliation')
    for order in result['orders']:
        if order['kind'] == 'new':
            check(order['receipt_index'] == order['release_index'] + order['lead_time_weeks'], 'PO timing')
            consolidated = total(result['orders'], 'quantity_units', kind='new', release_week=order['release_week'],
                                 supplier_id=order['supplier_id'], component_id=order['component_id'])
            check(consolidated >= integer(offers[order['supplier_id'], order['component_id']]['minimum_order_units']), 'PO MOQ')
        if order['receipt_index'] < n:
            check(total(result['receipts'], 'quantity_units', order_id=order['order_id']) == order['quantity_units'], 'PO received exactly once')
            check(all(r['week_start'] == order['receipt_week'] for r in result['receipts'] if r['order_id'] == order['order_id']), 'PO early receipt')
        else:
            check(total(result['receipts'], 'quantity_units', order_id=order['order_id']) == 0, 'Beyond-horizon PO received early')
    firm = {o['order_id']: o for o in result['orders'] if o['kind'] == 'firm'}
    check(set(firm) == {r['order_id'] for r in t['open_orders']}, 'Firm orders changed')
    for row in t['open_orders']:
        check(firm[row['order_id']]['quantity_units'] == integer(row['quantity_units']) and firm[row['order_id']]['receipt_week'] == row['receipt_week_start'], 'Firm receipt changed')
    for row in result['supplier_capacity']:
        check(0 <= row['released_units'] <= row['effective_capacity_units'], 'Offer capacity exceeded')
        check(row['released_units'] == total(result['orders'], 'quantity_units', kind='new', release_week=row['week_start'], supplier_id=row['supplier_id'], component_id=row['component_id']), 'PO release mismatch')
    for s in result['shipments']:
        check(s['arrival_index'] == s['release_index'] + s['lead_time_weeks'], 'Outbound timing')
    for name, keys, incoming in [('order_pipeline', ['supplier_id','component_id'], 'released_units'),
                                 ('shipment_pipeline', ['warehouse_id','product_id'], 'dispatched_units')]:
        previous = {}
        if name == 'order_pipeline':
            for order in t['open_orders']:
                key = (order['supplier_id'], order['component_id'])
                previous[key] = previous.get(key, 0) + integer(order['quantity_units'])
        for row in result[name]:
            key = tuple(row[k] for k in keys)
            check(row['beginning_units'] == previous.get(key,0), f'{name}: continuity')
            check(row['beginning_units'] + row[incoming] - row['received_units'] == row['ending_units'], f'{name}: conservation')
            check(row['ending_units'] >= 0, f'{name}: negative pipeline')
            previous[key] = row['ending_units']
    for row in result['weekly']:
        check(0 <= row['factory_hours_used'] <= row['factory_hours_available'], 'Factory labor exceeded')
        check(row['factory_hours_used'] == total(result['production'], 'labor_hours', week_start=row['week_start']), 'Labor reconciliation')
        for metric in ['demand_units', 'fulfilled_units', 'unmet_units', 'potential_sales_revenue_exposure']:
            check(row[metric] == total(result['warehouse_inventory'], metric, week_start=row['week_start']), 'Service aggregation')
        check(row['purchasing_cost'] == total(result['receipts'], 'purchase_cost', week_start=row['week_start']), 'Purchase cost reconciliation')
        check(row['inbound_transport_cost'] == total(result['receipts'], 'inbound_transport_cost', week_start=row['week_start']), 'Inbound cost reconciliation')
        check(row['conversion_cost'] == total(result['production'], 'conversion_cost', week_start=row['week_start']), 'Conversion cost reconciliation')
        check(row['outbound_transport_cost'] == total(result['shipments'], 'outbound_transport_cost', release_week=row['week_start']), 'Outbound cost reconciliation')
    return dict(status='PASS', checks=['inventory continuity and conservation', 'BOM and production reconciliation',
        'PO releases, MOQ and capacity', 'firm orders preserved', 'receipt and shipment timing',
        'shared labor capacity', 'demand reconciliation', 'cost ledger reconciliation', 'PO and shipment pipeline conservation'])


def serializable(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: serializable(v) for k,v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value


def write_result(result, output):
    output = Path(output)
    check(output.resolve() != ROOT and not output.resolve().is_relative_to(ROOT / 'data'),
          'Results must be written outside the original company data directory')
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in result.items():
        if not isinstance(rows, list):
            continue
        if not rows:
            # Remove an obsolete generated ledger if an earlier run had pending rows.
            (output / f'{name}.csv').unlink(missing_ok=True)
            continue
        with (output / f'{name}.csv').open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            # Decimal string conversion preserves exact money in audit ledgers.
            writer.writerows(rows)
    (output / 'result.json').write_text(json.dumps(serializable(result), indent=2, allow_nan=False) + '\n')
    (output / 'summary.json').write_text(json.dumps(serializable(dict(totals=result['totals'], checks=result['checks'], metadata=result['metadata'])), indent=2) + '\n')
