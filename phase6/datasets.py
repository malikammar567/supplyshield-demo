"""Bounded ZIP ingestion; uploaded schemas cannot change the trusted CSV contract."""
import csv,hashlib,io,json,stat,tempfile,zipfile
from pathlib import Path,PurePosixPath
from phase2.engine import ROOT,load_company,check,serializable

MAX_UPLOAD=20*1024*1024
MAX_EXPANDED=50*1024*1024
MAX_ROWS=200000
SCHEMA=json.loads((ROOT/'schema.json').read_text())
REQUIRED={f'data/{name}.csv' for name in SCHEMA['tables']}|{'schema.json','model_config.json','phase2/planning_policy.json'}
ALLOWED=REQUIRED|{'README.md','DATA_DICTIONARY.md'}


def digest(value):
    return hashlib.sha256(json.dumps(serializable(value),sort_keys=True,allow_nan=False).encode()).hexdigest()


def dataset(company,source):
    return dict(company=company,source=source,name=company['config']['company'],id=digest(company),schema_version='1.0',configuration_hash=digest(dict(model=company['config'],policy=company['policy'])))


def demo():return dataset(load_company(),'Synthetic demo')


def template_zip(blank=False):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(REQUIRED):
            content=(ROOT/name).read_bytes()
            if blank and name.startswith('data/'):
                table=Path(name).stem
                content=(','.join(SCHEMA['tables'][table]['columns'])+'\n').encode()
            z.writestr(name,content)
        z.writestr('README.md','CSV headers are fixed; row counts and IDs are not. Supply all 12 tables, schema.json, model_config.json and phase2/planning_policy.json. Update horizon, snapshot and production_priority. One factory, EA units, USD costs, complete weekly demand including zero rows. Blank templates must be populated before activation. No code is accepted.')
    return stream.getvalue()


def load_upload(raw):
    check(len(raw)<=MAX_UPLOAD,'ZIP exceeds the 20 MiB compressed upload limit.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members=z.infolist()
            check(len(members)<=64,'ZIP has too many entries (maximum 64).')
            total=0;files={}
            for entry in members:
                name=entry.filename;p=PurePosixPath(name)
                check(not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name and '\x00' not in name,'Unsafe ZIP path: '+name)
                mode=entry.external_attr>>16
                check(not stat.S_ISLNK(mode) and (stat.S_IFMT(mode) in (0,stat.S_IFREG,stat.S_IFDIR)),'ZIP links/special files are not accepted: '+name)
                check(not entry.flag_bits&1,'Encrypted ZIP files are not supported.')
                if entry.is_dir():continue
                check(name not in files,'Duplicate ZIP entry: '+name)
                total+=entry.file_size
                check(total<=MAX_EXPANDED,'ZIP exceeds the 50 MiB expanded size limit.')
                check(entry.file_size<=10*1024*1024,'File exceeds 10 MiB: '+name)
                check(entry.file_size/max(entry.compress_size,1)<=500,'Excessive compression ratio: '+name)
                files[name]=entry
            # Accept the documented root layout or one enclosing directory, never arbitrary flattening.
            check(bool(files),'ZIP contains no files.')
            if 'schema.json' not in files:
                roots={PurePosixPath(n).parts[0] for n in files}
                if len(roots)==1 and all(len(PurePosixPath(n).parts)>1 for n in files):
                    prefix=next(iter(roots))+'/'
                    files={n[len(prefix):]:entry for n,entry in files.items()}
            check(bool(files),'ZIP contains no files.')
            check(set(files)<=ALLOWED,'Unsupported ZIP files: '+', '.join(sorted(set(files)-ALLOWED)))
            if not REQUIRED<=set(files):
                from phase8.contract import PackageError
                raise PackageError(dict(status='FAIL',schema_version=None,profile=None,mapping=[],errors=[dict(file=n,row=None,column=None,code='missing_file',message='Missing required file: '+n) for n in sorted(REQUIRED-set(files))]))
            with tempfile.TemporaryDirectory(prefix='resilience-upload-') as directory:
                root=Path(directory);count=0
                for name,entry in files.items():
                    content=z.read(entry)
                    try:text=content.decode('utf-8-sig')
                    except UnicodeError:raise ValueError(name+': expected UTF-8 text') from None
                    if name.endswith('.json'):
                        try:value=json.loads(text,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON number')))
                        except (ValueError,RecursionError) as e:raise ValueError(name+': invalid JSON: '+str(e)) from None
                        check(isinstance(value,dict),name+': expected a JSON object')
                    if name.endswith('.csv'):
                        reader=csv.DictReader(io.StringIO(text));expected=SCHEMA['tables'][Path(name).stem]['columns']
                        for row in reader:
                            count+=1
                            check(count<=MAX_ROWS,'Dataset exceeds 200,000 CSV rows.')
                    destination=root/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(text)
                from phase8.contract import validate_package,PackageError,profile
                report=validate_package(root)
                if report['errors']:raise PackageError(report)
                try:company=load_company(root)
                except (KeyError,TypeError,ValueError,IndexError,OverflowError) as e:
                    raise ValueError('Dataset validation failed: '+str(e)) from None
                check(bool(company['tables']['suppliers']),'suppliers.csv: at least one supplier is required')
                check(any(r['facility_type']=='warehouse' for r in company['tables']['facilities']),'facilities.csv: at least one warehouse is required')
                check(any(r['item_type']=='finished_good' for r in company['tables']['items']),'items.csv: at least one finished product is required')
                ds=dataset(company,'Uploaded dataset');ds['schema_version']=report['schema_version']
                ds['profile']=profile(ds);report['profile']=ds['profile'];ds['validation_report']=report
                return ds
    except (zipfile.BadZipFile,NotImplementedError,RuntimeError,EOFError) as e:
        raise ValueError('Cannot read ZIP: '+str(e)) from None


def inspect_upload(raw):
    """Validate for preview only; callers explicitly activate a returned candidate."""
    from phase8.contract import PackageError
    try:
        ds=load_upload(raw)
        return dict(candidate=ds,report=ds['validation_report'],upload_hash=hashlib.sha256(raw).hexdigest())
    except PackageError as e:return dict(candidate=None,report=e.report,upload_hash=hashlib.sha256(raw).hexdigest())
    except (ValueError,TypeError,KeyError,IndexError,OverflowError,RecursionError) as e:
        return dict(candidate=None,report=dict(status='FAIL',schema_version=None,profile=None,mapping=[],errors=[dict(file='package',row=None,column=None,code='package_error',message=str(e))]),upload_hash=hashlib.sha256(raw).hexdigest())
