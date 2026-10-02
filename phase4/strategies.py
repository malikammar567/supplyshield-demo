"""Validated mitigation policies: qualified reallocation and transport execution."""
import copy
from datetime import date, timedelta
from phase2.engine import D, check, integer


def fields(obj, names, label):
    check(isinstance(obj, dict) and set(obj) == set(names.split()), f'{label}: expected fields {names}')


def number(value, label, maximum=None):
    check(type(value) in (int, float), f'{label}: expected a number')
    value = D(value)
    check(value.is_finite() and value >= 0 and (maximum is None or value <= maximum), f'{label}: invalid value')
    return value


def whole(value, label, minimum=0):
    check(type(value) is int and value >= minimum, f'{label}: expected integer >= {minimum}')
    return value


class Mitigation:
    def __init__(self, config, company, disruption_start):
        fields(config, 'strategy_id mode start_week duration_weeks reallocation expedited_routes preparation assumptions', 'strategy')
        self.config = copy.deepcopy(config)
        sid = config['strategy_id']
        check(isinstance(sid, str) and bool(sid) and all(c.isalnum() or c in '_-' for c in sid)
              and sid not in ('baseline', 'disruption_no_action'), 'Invalid/reserved strategy_id')
        check(config['mode'] in ('reactive', 'proactive'), 'mode must be reactive or proactive')
        check(isinstance(config['assumptions'], str) and config['assumptions'].strip(), 'Explicit assumptions are required')
        weeks = [r['week_start'] for r in company['tables']['calendar']]
        check(config['start_week'] in weeks and config['start_week'] >= disruption_start, 'Response must start on a planning week at/after the disruption')
        self.start = date.fromisoformat(config['start_week'])
        try:
            self.end = self.start + timedelta(weeks=whole(config['duration_weeks'], 'duration_weeks', 1))
        except OverflowError:
            raise ValueError('duration exceeds supported dates') from None
        t = company['tables']
        self.offers = {(r['supplier_id'],r['component_id']):r for r in t['supplier_offers']}
        self.lanes = {r['lane_id']:r for r in t['transportation']}
        check(isinstance(config['reallocation'],list), 'reallocation must be a list')
        self.rules = {}
        for rule in config['reallocation']:
            fields(rule,'component_id from_supplier_id alternate_supplier_ids','reallocation')
            c,s,alternates = rule['component_id'],rule['from_supplier_id'],rule['alternate_supplier_ids']
            check(isinstance(c,str) and isinstance(s,str) and (s,c) in self.offers, 'Unqualified source offer')
            check(c not in self.rules, 'Only one source-reallocation rule per component is supported')
            check(isinstance(alternates,list) and bool(alternates) and all(isinstance(a,str) for a in alternates), 'Alternates must be a nonempty ID list')
            check(len(set(alternates))==len(alternates) and s not in alternates, 'Duplicate/self alternate')
            check(all((a,c) in self.offers for a in alternates), 'Unqualified alternate supplier/component')
            self.rules[c] = rule
        check(isinstance(config['expedited_routes'],list), 'expedited_routes must be a list')
        self.routes = {}
        route_ids = set(self.lanes)
        for route in config['expedited_routes']:
            fields(route,'route_id lane_id kind original_transport_weeks expedited_transport_weeks cost_usd_per_unit','expedited route')
            lid = route['lane_id']
            check(isinstance(lid,str) and lid in self.lanes and lid not in self.routes, 'Unknown/duplicate expedited lane')
            rid = route['route_id']
            check(isinstance(rid,str) and rid and rid not in route_ids, 'Route ID must be unique and different from the original lane')
            route_ids.add(rid)
            lane = self.lanes[lid]
            standard = whole(route['original_transport_weeks'],'original_transport_weeks',1)
            fast = whole(route['expedited_transport_weeks'],'expedited_transport_weeks')
            check(fast < standard, 'Expedited route must actually shorten transportation')
            rate = number(route['cost_usd_per_unit'],'expedited cost')
            check(rate >= D(lane['cost_usd_per_unit']), 'Demo expedite rate must not undercut the standard rate')
            if route['kind']=='inbound':
                check(lane['origin_type']=='supplier', 'Inbound expedited lane must originate at a supplier')
                offers = [o for (s,c),o in self.offers.items() if s==lane['origin_id']]
                check(bool(offers) and all(standard <= integer(o['lead_time_weeks']) and
                      integer(o['lead_time_weeks'])-standard+fast >= 1 for o in offers),
                      'Expediting cannot remove processing time or create same-week inbound supply')
            elif route['kind']=='outbound':
                check(lane['origin_type']=='facility' and standard==integer(lane['lead_time_weeks']), 'Outbound route must match its original lane transit')
            else:
                raise ValueError('Unknown expedited route kind')
            self.routes[lid] = route
        prep = config['preparation']
        check((config['mode']=='proactive') == (prep is not None), 'Only proactive strategies may have preparation, and must supply it')
        if prep is not None:
            fields(prep,'weeks targets other_weekly_commitments','preparation')
            whole(prep['weeks'],'preparation weeks',1)
            check(prep['weeks'] <= 520, 'Preparation supports at most 520 weeks')
            check(isinstance(prep['targets'],list) and bool(prep['targets']), 'Preparation targets required')
            check(isinstance(prep['other_weekly_commitments'],list), 'Preparation commitments must be explicit')
            reservations = {}
            for row in prep['other_weekly_commitments']:
                fields(row,'supplier_id component_id quantity_units','preparation commitment')
                key = (row['supplier_id'],row['component_id'])
                check(key in self.offers and key not in reservations,'Unknown/duplicate reserved offer')
                qty = whole(row['quantity_units'],'reserved quantity')
                check(qty <= integer(self.offers[key]['weekly_capacity_units']), 'Reservation exceeds offer capacity')
                reservations[key]=qty
            seen = set()
            for target in prep['targets']:
                fields(target,'supplier_id component_id extra_units','preparation target')
                s,c = target['supplier_id'],target['component_id']
                check((s,c) in self.offers and (s,c) in reservations, 'Preparation source must be qualified and have an explicit reservation')
                check(c not in seen,'Only one preparation source per component supported')
                seen.add(c)
                whole(target['extra_units'],'extra_units',1)

    def active(self, week):
        return self.start <= date.fromisoformat(week) < self.end

    def reallocate(self, week, component, split, original, capacities, moqs):
        released = dict(original)
        if not self.active(week) or component not in self.rules:
            return released
        rule = self.rules[component]
        source = rule['from_supplier_id']
        shortage = min(max(0,split.get(source,0)-original[source]),max(0,sum(split.values())-sum(original.values())))
        for alternate in rule['alternate_supplier_ids']:
            if shortage <= 0:
                break
            spare = capacities[alternate] - released[alternate]
            if spare <= 0:
                continue
            increment = min(shortage, spare)
            if released[alternate] == 0:
                if spare < moqs[alternate]:
                    continue
                increment = min(spare,max(increment,moqs[alternate]))
            released[alternate] += increment
            shortage = max(0,shortage-increment)
        return released

    def inbound(self, week, supplier, component, lane, lead, rate):
        route = self.routes.get(lane['lane_id'])
        if route and route['kind']=='inbound' and self.active(week):
            lead = lead-route['original_transport_weeks']+route['expedited_transport_weeks']
            check(lead >= 1, 'Inbound acceleration exceeds actual lead time')
            return lead,D(route['cost_usd_per_unit']),route['route_id']
        return lead,rate,lane['lane_id']

    def outbound(self, week, lane, lead, rate):
        route = self.routes.get(lane['lane_id'])
        if route and route['kind']=='outbound' and self.active(week):
            return route['expedited_transport_weeks'],D(route['cost_usd_per_unit']),route['route_id']
        return lead,rate,lane['lane_id']
