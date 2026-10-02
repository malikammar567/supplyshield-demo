"""Teaching stage 1: observed opening flow, before planning decisions.

Run with python3 phase2/first_week_flow.py from the project root.
No purchase releases, production decisions or factory shipments are modeled yet.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validate import validate


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    errors, data = validate(ROOT)
    require(not errors, str(errors))
    config = json.loads((ROOT / 'model_config.json').read_text())
    week = data['calendar'][0]['week_start']
    require(config['snapshot_date'] == week, 'Snapshot must match opening week')
    require(config['receipt_timing'] == 'start_of_week', 'Unsupported receipt timing')
    require(config['demand_timing'] == 'end_of_week', 'Unsupported demand timing')
    require(config['demand_policy'] == 'lost_sales', 'Unsupported demand policy')
    inventory = {(r['facility_id'], r['item_id']): int(r['on_hand_units'])
                 for r in data['inventory']}
    components = {r['item_id'] for r in data['items'] if r['item_type'] == 'component'}
    receipts = {}
    for order in data['open_orders']:
        if order['receipt_week_start'] == week:
            key = (order['destination_id'], order['component_id'])
            receipts[key] = receipts.get(key, 0) + int(order['quantity_units'])
    component_rows = []
    for facility, item in sorted(set(inventory) | set(receipts)):
        if item not in components:
            continue
        beginning = inventory.get((facility, item), 0)
        received = receipts.get((facility, item), 0)
        available = beginning + received
        require(available >= 0, 'Negative component availability')
        component_rows.append(dict(week_start=week, facility_id=facility,
                                   item_id=item, beginning_units=beginning,
                                   scheduled_receipts_units=received,
                                   available_before_production_units=available))
    # The input format contains no opening finished-goods transfers in transit.
    # Every factory-to-warehouse lane in this sample takes at least one week.
    warehouses = {r['facility_id'] for r in data['facilities']
                  if r['facility_type'] == 'warehouse'}
    for lane in data['transportation']:
        if lane['destination_id'] in warehouses:
            require(int(lane['lead_time_weeks']) >= 1,
                    'This opening-flow slice requires no same-week warehouse arrivals')
    warehouse_rows = []
    for demand in data['demand']:
        if demand['week_start'] != week:
            continue
        key = (demand['warehouse_id'], demand['product_id'])
        beginning = inventory.get(key, 0)
        requested = int(demand['demand_units'])
        received = 0
        fulfilled = min(beginning + received, requested)
        lost = requested - fulfilled
        ending = beginning + received - fulfilled
        require(beginning + received == fulfilled + ending, 'Inventory conservation failed')
        require(requested == fulfilled + lost, 'Demand conservation failed')
        require(min(fulfilled, lost, ending) >= 0, 'Negative warehouse flow')
        warehouse_rows.append(dict(week_start=week, warehouse_id=key[0],
                                   product_id=key[1], beginning_units=beginning,
                                   receipts_units=received, demand_units=requested,
                                   fulfilled_units=fulfilled, lost_sales_units=lost,
                                   ending_units=ending))
    requested = sum(r['demand_units'] for r in warehouse_rows)
    fulfilled = sum(r['fulfilled_units'] for r in warehouse_rows)
    result = dict(stage='1: opening inventory flow only', week_start=week,
                  phase1_validation='PASS', flow_checks='PASS',
                  demanded_units=requested, fulfilled_units=fulfilled,
                  lost_sales_units=sum(r['lost_sales_units'] for r in warehouse_rows),
                  unit_fill_rate=fulfilled / requested if requested else None,
                  limitations=['Production, purchase releases, factory shipments, costs and capacity checks are not implemented in this teaching slice.',
                               'No opening finished-goods transfers are supplied; opening warehouse pipeline is assumed empty.'])
    output = Path(__file__).resolve().parent / 'results'
    output.mkdir(exist_ok=True)
    for name, rows in [('week1_component_opening', component_rows),
                       ('week1_warehouse_flow', warehouse_rows)]:
        if rows:
            with (output / f'{name}.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    (output / 'week1_flow_checks.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
