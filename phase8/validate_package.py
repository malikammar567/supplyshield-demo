"""Read-only upload validator: python3 phase8/validate_package.py COMPANY.zip."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from phase6.datasets import inspect_upload
from phase2.engine import serializable

if __name__=='__main__':
    outcome=inspect_upload(Path(sys.argv[1]).read_bytes())
    print(json.dumps(serializable(outcome['report']),indent=2))
    sys.exit(outcome['candidate'] is None)
