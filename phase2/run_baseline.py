"""Run the verified baseline: python3 phase2/run_baseline.py."""
import argparse
import json
from pathlib import Path
from engine import ROOT, input_hashes, load_company, simulate, write_result, serializable, check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension-demand', type=Path, help='Explicit extra demand rows, same headers as demand.csv')
    parser.add_argument('--output', type=Path, default=ROOT / 'phase2/results/baseline')
    args = parser.parse_args()
    before = input_hashes()
    result = simulate(load_company(extension=args.extension_demand))
    check(before == input_hashes(), 'Baseline inputs were modified')
    result['metadata']['input_sha256'] = before
    write_result(result, args.output)
    print(json.dumps(serializable(result['totals']), indent=2))
    print(f'Checks: {result["checks"]["status"]}. Results: {args.output}')


if __name__ == '__main__':
    main()
