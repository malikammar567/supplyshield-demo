"""Compare independently simulated cases, not estimated disruption percentages."""
import csv
import json
from pathlib import Path
from phase2.engine import D, ZERO, serializable, check


METRICS = ['demand_units', 'fulfilled_units', 'unmet_units', 'purchasing_cost', 'conversion_cost',
           'inbound_transport_cost', 'outbound_transport_cost', 'transportation_cost',
           'total_operating_activity_cost', 'potential_sales_revenue_exposure',
           'new_purchase_commitment', 'new_inbound_freight_commitment']


def delta_row(base, case):
    row = {}
    for key in METRICS:
        row['baseline_' + key] = base[key]
        row['scenario_' + key] = case[key]
        row['delta_' + key] = case[key] - base[key]
    row['baseline_unit_fill_rate'] = base['unit_fill_rate']
    row['scenario_unit_fill_rate'] = case['unit_fill_rate']
    row['fill_rate_change_percentage_points'] = (100 * (case['unit_fill_rate'] - base['unit_fill_rate'])
                                                if base['unit_fill_rate'] is not None and case['unit_fill_rate'] is not None else None)
    return row


def compare(baseline, scenario):
    check(baseline['metadata']['weeks'] == scenario['metadata']['weeks'], 'Cases must have the same calendar')
    weekly = [dict(week_start=b['week_start'], **delta_row(b, s)) for b,s in zip(baseline['weekly'], scenario['weekly'])]
    pairs = sorted({(r['warehouse_id'], r['product_id']) for r in baseline['warehouse_inventory']})
    product_warehouse = []
    pair_weekly = []
    for wh, p in pairs:
        b = [r for r in baseline['warehouse_inventory'] if (r['warehouse_id'],r['product_id'])==(wh,p)]
        s = [r for r in scenario['warehouse_inventory'] if (r['warehouse_id'],r['product_id'])==(wh,p)]
        record = dict(warehouse_id=wh, product_id=p)
        for name, rows in [('baseline', b), ('scenario', s)]:
            for metric in ['demand_units', 'fulfilled_units', 'unmet_units', 'potential_sales_revenue_exposure']:
                record[f'{name}_{metric}'] = sum(r[metric] for r in rows)
            record[f'{name}_first_unmet_week'] = next((r['week_start'] for r in rows if r['unmet_units']), None)
            dem = record[f'{name}_demand_units']
            record[f'{name}_unit_fill_rate'] = D(record[f'{name}_fulfilled_units'])/dem if dem else None
        record['additional_unmet_units'] = record['scenario_unmet_units'] - record['baseline_unmet_units']
        record['delta_revenue_exposure'] = record['scenario_potential_sales_revenue_exposure'] - record['baseline_potential_sales_revenue_exposure']
        record['first_additional_unmet_week'] = next((y['week_start'] for x,y in zip(b,s) if y['unmet_units'] > x['unmet_units']), None)
        record['operationally_changed'] = any(any(x[k] != y[k] for k in ['demand_units','fulfilled_units','unmet_units']) for x,y in zip(b,s))
        record['fill_rate_change_percentage_points'] = (100*(record['scenario_unit_fill_rate']-record['baseline_unit_fill_rate'])
            if record['baseline_unit_fill_rate'] is not None and record['scenario_unit_fill_rate'] is not None else None)
        product_warehouse.append(record)
        for x,y in zip(b,s):
            pair_weekly.append(dict(week_start=x['week_start'], warehouse_id=wh, product_id=p,
                baseline_demand_units=x['demand_units'], scenario_demand_units=y['demand_units'],
                baseline_fulfilled_units=x['fulfilled_units'], scenario_fulfilled_units=y['fulfilled_units'],
                baseline_unmet_units=x['unmet_units'], scenario_unmet_units=y['unmet_units'],
                additional_unmet_units=y['unmet_units']-x['unmet_units'],
                delta_revenue_exposure=y['potential_sales_revenue_exposure']-x['potential_sales_revenue_exposure']))
    component_comparison = []
    for b,s in zip(baseline['component_inventory'], scenario['component_inventory']):
        row = dict(week_start=b['week_start'], component_id=b['component_id'])
        for key in ['beginning_units','receipts_units','consumed_units','ending_units','below_safety_units']:
            row['baseline_'+key], row['scenario_'+key] = b[key], s[key]
            row['delta_'+key] = s[key]-b[key]
        component_comparison.append(row)
    capacity_comparison = []
    for b,s in zip(baseline['supplier_capacity'], scenario['supplier_capacity']):
        row = dict(week_start=b['week_start'], supplier_id=b['supplier_id'], component_id=b['component_id'])
        for key in ['effective_capacity_units','released_units','utilization','lead_time_weeks']:
            row['baseline_'+key], row['scenario_'+key] = b[key], s[key]
        capacity_comparison.append(row)
    bottlenecks = []
    for b,s in zip(baseline['production'], scenario['production']):
        if b['unmade_requested_units'] or s['unmade_requested_units']:
            bottlenecks.append(dict(week_start=b['week_start'], product_id=b['product_id'],
                baseline_produced_units=b['produced_units'], scenario_produced_units=s['produced_units'],
                baseline_unmade_requested_units=b['unmade_requested_units'], scenario_unmade_requested_units=s['unmade_requested_units'],
                baseline_binding_constraints=b['binding_constraints'], scenario_binding_constraints=s['binding_constraints']))
    totals = delta_row(baseline['totals'], scenario['totals'])
    for name, run in [('baseline',baseline),('scenario',scenario)]:
        for key in ['pending_purchase_commitment','pending_inbound_freight_commitment']:
            totals[name+'_'+key] = run['totals'][key]
    return dict(totals=totals, weekly=weekly, product_warehouse=product_warehouse,
                product_warehouse_weekly=pair_weekly, components=component_comparison,
                supplier_capacity=capacity_comparison, bottlenecks=bottlenecks)


def write_comparison(comparison, output, config):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(serializable(comparison), indent=2, allow_nan=False)+'\n')
    (output / 'scenario_config.json').write_text(json.dumps(config, indent=2)+'\n')
    for name, rows in comparison.items():
        if isinstance(rows,list) and rows:
            with (output / f'comparison_{name}.csv').open('w',newline='') as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    t = comparison['totals']
    def money(v): return f'${v:,.2f}'
    def rate(v): return f'{100*v:.2f}%' if v is not None else 'N/A'
    lines = [f'# {config["scenario_id"]}', '', 'Calculated from two isolated runs of the same planning engine.', '',
             '| Measure | Baseline | Scenario | Change |', '|---|---:|---:|---:|']
    for key in ['demand_units','fulfilled_units','unmet_units']:
        lines.append(f'| {key} | {t["baseline_"+key]:,} | {t["scenario_"+key]:,} | {t["delta_"+key]:+,} |')
    pp = t['fill_rate_change_percentage_points']
    pp_label = f'{pp:+.2f} percentage points' if pp is not None else 'N/A'
    lines.append(f'| Unit fill rate | {rate(t["baseline_unit_fill_rate"])} | {rate(t["scenario_unit_fill_rate"])} | {pp_label} |')
    for key in ['purchasing_cost','conversion_cost','transportation_cost','total_operating_activity_cost','potential_sales_revenue_exposure']:
        lines.append(f'| {key} | {money(t["baseline_"+key])} | {money(t["scenario_"+key])} | {money(t["delta_"+key])} |')
    lines += ['', 'Potential sales revenue exposure is unmet units × selling price. It is not lost profit or an operating cost.',
              'Activity costs are recognized at receipt/production/dispatch; pending PO commitments are separate. Reduced costs can reflect reduced supply and service.',
              '', '## Product and warehouse detail', '', '| Warehouse | Product | Baseline unmet | Scenario unmet | First scenario unmet | First additional unmet |', '|---|---|---:|---:|---|---|']
    for row in comparison['product_warehouse']:
        lines.append(f'| {row["warehouse_id"]} | {row["product_id"]} | {row["baseline_unmet_units"]} | {row["scenario_unmet_units"]} | {row["scenario_first_unmet_week"] or "none"} | {row["first_additional_unmet_week"] or "none"} |')
    lines += ['', '## Horizon limits', '', 'The last supplied demand week is the end of the simulation, not the end of disruption risk. Inspect pending_orders.csv (when nonempty) and the JSON pending orders, ending stocks, component buffers and bottlenecks. No further demand is inferred.',
              'New releases arrive after each offer’s configured total lead time. Factory shipments then use the destination lane’s lead time. Pending orders and goods in transit may have effects beyond the calendar; no shortage within the horizon does not establish no disruption risk. The policy does not dispatch beyond known demand.',
              'Use --extension-demand with an explicitly populated demand CSV to analyze later weeks. Both cases use that same extension.', '']
    (output / 'REPORT.md').write_text('\n'.join(lines))
