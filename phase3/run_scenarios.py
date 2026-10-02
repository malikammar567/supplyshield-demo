"""Run baseline and selected/all scenario overlays with identical starting data."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase2.engine import load_company, simulate, write_result, input_hashes, check, serializable
from phase3.scenarios import ScenarioEffects
from phase3.comparison import compare, write_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--scenario', type=Path)
    choice.add_argument('--all', action='store_true')
    parser.add_argument('--extension-demand', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'phase3/results')
    args = parser.parse_args()
    paths = sorted((ROOT/'phase3/scenarios').glob('*.json')) if args.all else [args.scenario or ROOT/'phase3/scenarios/s01_capacity_loss.json']
    before = input_hashes()
    company = load_company(extension=args.extension_demand)
    # Validate every supplied scenario before running or writing any results.
    cases = [(json.loads(p.read_text()), p) for p in paths]
    effects = [ScenarioEffects(config, company) for config, p in cases]
    ids = [config['scenario_id'] for config,p in cases]
    check(len(ids)==len(set(ids)), 'Duplicate scenario IDs')
    baseline = simulate(company)
    baseline['metadata']['input_sha256'] = before
    write_result(baseline, args.output/'baseline')
    summaries = []
    for (config, path), effect in zip(cases, effects):
        case = simulate(company, effect)
        case['metadata']['input_sha256'] = before
        case['metadata']['scenario'] = config
        comparison = compare(baseline, case)
        folder = args.output/config['scenario_id']
        write_result(case, folder)
        write_comparison(comparison, folder, config)
        summaries.append(dict(scenario_id=config['scenario_id'], **comparison['totals']))
        print(f'{config["scenario_id"]}: fulfilled {case["totals"]["fulfilled_units"]}/{case["totals"]["demand_units"]}; unmet {case["totals"]["unmet_units"]}; checks PASS')
    check(input_hashes()==before, 'Original inputs changed')
    (args.output/'scenario_summary.json').write_text(json.dumps(serializable(summaries), indent=2)+'\n')
    (args.output/'input_integrity.json').write_text(json.dumps(dict(status='PASS', before=before, after=input_hashes()), indent=2)+'\n')
    print(f'Original inputs unchanged. Results: {args.output}')


if __name__ == '__main__':
    main()
