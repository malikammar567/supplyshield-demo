"""Validated scenario overlays; never edit the company tables."""
import copy
from datetime import date, timedelta
from decimal import ROUND_CEILING

from phase2.engine import IdentityEffects, D, check, floor


class ScenarioEffects(IdentityEffects):
    def __init__(self, config, company):
        check(isinstance(config, dict), 'Scenario must be a JSON object')
        self.config = copy.deepcopy(config)
        common = {'scenario_id', 'type', 'start_week', 'duration_weeks', 'existing_firm_orders_policy'}
        definitions = {
            'supplier_capacity_loss': {'supplier_id', 'capacity_loss_percentage'},
            'supplier_shutdown': {'supplier_id'},
            'supplier_lead_time_increase': {'supplier_id', 'additional_lead_time_weeks'},
            'demand_surge': {'product_ids', 'warehouse_ids', 'demand_increase_percentage'},
            'purchase_cost_increase': {'supplier_id', 'cost_increase_percentage'},
            'transportation_cost_increase': {'lane_ids', 'cost_increase_percentage'},
        }
        kind = config.get('type')
        check(isinstance(kind, str) and kind in definitions, 'Unknown scenario type')
        expected = common | definitions[kind]
        check(set(config) == expected, f'Scenario fields must be exactly: {sorted(expected)}')
        check(isinstance(config['scenario_id'], str) and bool(config['scenario_id']) and
              all(c.isalnum() or c in '_-' for c in config['scenario_id']), 'Invalid scenario_id')
        check(config['scenario_id'] != 'baseline', 'scenario_id baseline is reserved for the unchanged comparison run')
        check(config['existing_firm_orders_policy'] == 'preserve_scheduled_receipts', 'Only preserved firm orders are supported')
        weeks = [r['week_start'] for r in company['tables']['calendar']]
        check(isinstance(config['start_week'], str) and config['start_week'] in weeks, 'Scenario start_week must match a planning week')
        check(type(config['duration_weeks']) is int and config['duration_weeks'] > 0, 'Duration must be a positive integer')
        self.start = date.fromisoformat(config['start_week'])
        try:
            self.end = self.start + timedelta(weeks=config['duration_weeks'])
        except (ValueError, OverflowError):
            raise ValueError('Scenario duration exceeds supported dates') from None
        suppliers = {r['supplier_id'] for r in company['tables']['suppliers']}
        if 'supplier_id' in config:
            check(isinstance(config['supplier_id'], str) and config['supplier_id'] in suppliers, 'Unknown supplier_id')
        self.kind = kind
        self.loss = self.number('capacity_loss_percentage', 100) if kind == 'supplier_capacity_loss' else D(100)
        if kind == 'supplier_lead_time_increase':
            extra = config['additional_lead_time_weeks']
            check(type(extra) is int and extra >= 0, 'additional_lead_time_weeks must be a nonnegative integer')
            try:
                last = date.fromisoformat(weeks[-1])
                for offer in company['tables']['supplier_offers']:
                    if offer['supplier_id'] == config['supplier_id']:
                        last + timedelta(weeks=extra + int(offer['lead_time_weeks']))
            except (OverflowError, ValueError):
                raise ValueError('Lead time exceeds supported dates') from None
        if kind in ('purchase_cost_increase', 'transportation_cost_increase'):
            self.cost_increase = self.number('cost_increase_percentage')
        if kind == 'transportation_cost_increase':
            self.selection('lane_ids', {r['lane_id'] for r in company['tables']['transportation']})
        if kind == 'demand_surge':
            self.increase = self.number('demand_increase_percentage')
            self.selection('product_ids', {r['item_id'] for r in company['tables']['items'] if r['item_type']=='finished_good'})
            self.selection('warehouse_ids', {r['facility_id'] for r in company['tables']['facilities'] if r['facility_type']=='warehouse'})

    def selection(self, field, allowed):
        values = self.config[field]
        check(isinstance(values,list) and bool(values) and all(isinstance(v,str) for v in values), f'{field} must be a nonempty list of IDs')
        check(len(set(values)) == len(values) and set(values) <= allowed, f'Invalid or duplicate {field}')

    def number(self, field, maximum=None):
        value = self.config[field]
        check(type(value) in (int, float), f'{field} must be a JSON number')
        value = D(value)
        check(value.is_finite() and value >= 0 and (maximum is None or value <= maximum), f'Invalid {field}')
        return value

    def active(self, week):
        return self.start <= date.fromisoformat(week) < self.end

    def capacity(self, supplier, component, week, normal):
        if self.kind in ('supplier_capacity_loss', 'supplier_shutdown') and self.active(week) and supplier == self.config['supplier_id']:
            return floor(D(normal) * (1 - self.loss / 100))
        return normal

    def lead_time(self, supplier, component, week, normal):
        if self.kind == 'supplier_lead_time_increase' and self.active(week) and supplier == self.config['supplier_id']:
            return normal + self.config['additional_lead_time_weeks']
        return normal

    def purchase_price(self, supplier, component, week, normal):
        if self.kind == 'purchase_cost_increase' and self.active(week) and supplier == self.config['supplier_id']:
            return normal * (1 + self.cost_increase / 100)
        return normal

    def freight_price(self, lane, week, normal):
        if self.kind == 'transportation_cost_increase' and self.active(week) and lane in self.config['lane_ids']:
            return normal * (1 + self.cost_increase / 100)
        return normal

    def demand(self, product, warehouse, week, normal):
        if (self.kind == 'demand_surge' and self.active(week) and product in self.config['product_ids']
                and warehouse in self.config['warehouse_ids']):
            return int((D(normal) * (1 + self.increase / 100)).to_integral_value(rounding=ROUND_CEILING))
        return normal
