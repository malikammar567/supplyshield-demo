"""Execution identity and secret-safe presentation, independent of calculations."""
import hashlib,os,uuid
from pathlib import Path

PROJECT_VERSION='0.9.0'
ENGINE_VERSION='weekly-planning-1.0'
ROOT=Path(__file__).resolve().parents[1]
ENGINE_FILES=['phase2/engine.py','phase3/scenarios.py','phase4/strategies.py','phase4/preparation.py','phase4/valuation.py','phase5/data.py','phase5/formulation.py','phase5/exact_formulation.py','phase5/execution.py','phase5/reconcile.py','phase5/solver.py']

def execution(kind):
    source={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ENGINE_FILES}
    fingerprint=hashlib.sha256(str(sorted(source.items())).encode()).hexdigest()
    return dict(run_id='ss-'+kind+'-'+uuid.uuid4().hex[:16],project_version=PROJECT_VERSION,engine_version=ENGINE_VERSION,engine_source_hash=fingerprint,engine_source_files=source,execution_origin='fresh_calculation')

def redact(text):
    for name in ('RESILIENCE_HOSTED_API_KEY','OPENAI_API_KEY'):
        value=os.environ.get(name)
        if value:text=text.replace(value,'[REDACTED]')
    return text

def safe_error(error):
    # Preserve useful validation messages without exposing stack locals or keys.
    return redact(str(error))[:1000]

def sanitize(value):
    if isinstance(value,str):return redact(value)
    if isinstance(value,dict):return {redact(k) if isinstance(k,str) else k:sanitize(v) for k,v in value.items()}
    if isinstance(value,list):return [sanitize(v) for v in value]
    return value
