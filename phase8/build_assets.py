"""Create versioned templates and a deliberately different synthetic company."""
import csv,io,json,re,sys,zipfile
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from phase8.contract import schema

def company_files():
    weeks=[(date(2027,1,4)+timedelta(weeks=i)).isoformat() for i in range(6)]
    tables={
        'suppliers':[['NORTH','North Metals (fictional)','US'],['DELTA','Delta Works (fictional)','US']],
        'items':[['STEEL','Steel blank','component','EA',0],['CELL','Battery cell','component','EA',0],['LAMP','Inspection lamp','finished_good','EA',90],['GAUGE','Electronic gauge','finished_good','EA',140]],
        'facilities':[['WORKS','Beacon assembly','factory','US',20],['EAST','East depot','warehouse','US',0],['WEST','West depot','warehouse','US',0],['CENTRAL','Central depot','warehouse','US',0]],
        'bom':[['LAMP','CELL',1],['GAUGE','STEEL',1]],
        'production':[['WORKS','LAMP',1,4],['WORKS','GAUGE',1.5,6]],
        'supplier_offers':[['NORTH','STEEL',14,9,2,2],['NORTH','CELL',14,7,2,2],['DELTA','STEEL',8,12,1,2],['DELTA','CELL',8,10,1,2]],
        'sourcing_policy':[['NORTH','STEEL',1],['NORTH','CELL',1],['DELTA','STEEL',0],['DELTA','CELL',0]],
        'inventory':[['WORKS','STEEL',12,2],['WORKS','CELL',10,2]]+[[wh,p,4,1] for wh in ['EAST','WEST','CENTRAL'] for p in ['LAMP','GAUGE']],
        'transportation':[['IN-N','NORTH','supplier','WORKS','facility','truck',1,.8],['IN-D','DELTA','supplier','WORKS','facility','truck',1,1.1]]+[[f'OUT-{wh}','WORKS','facility',wh,'facility','truck',2 if wh=='WEST' else 1,.6] for wh in ['EAST','WEST','CENTRAL']],
        'calendar':[[w] for w in weeks],
        'demand':[[w,wh,p,3 if wh=='WEST' and p=='LAMP' else 2] for w in weeks for wh in ['EAST','WEST','CENTRAL'] for p in ['LAMP','GAUGE']],
        'open_orders':[['BOOKED-STEEL','NORTH','STEEL','WORKS',6,weeks[0]],['BOOKED-CELL','NORTH','CELL','WORKS',6,weeks[0]]]
    }
    config=json.loads((ROOT/'model_config.json').read_text());config.update(company='Beacon Instruments (fictional)',snapshot_date=weeks[0],horizon_weeks=6,model_config_version='1.0')
    policy=json.loads((ROOT/'phase2/planning_policy.json').read_text());policy['production_priority']=['GAUGE','LAMP']
    files={'schema.json':json.dumps(schema(),indent=2),'model_config.json':json.dumps(config,indent=2),'phase2/planning_policy.json':json.dumps(policy,indent=2)}
    for name,rows in tables.items():
        stream=io.StringIO();writer=csv.writer(stream);writer.writerow(schema()['tables'][name]['columns']);writer.writerows(rows);files['data/'+name+'.csv']=stream.getvalue()
    return files

def package(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,content in files.items():z.writestr(name,content)
    return stream.getvalue()

def main():
    directory=ROOT/'phase8/assets';directory.mkdir(parents=True,exist_ok=True)
    files=company_files();(directory/'beacon_company.zip').write_bytes(package(files))
    for name,content in files.items():
        target=directory/'beacon_company'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
    blank=dict(files)
    for name in schema()['tables']:blank['data/'+name+'.csv']=','.join(schema()['tables'][name]['columns'])+'\n'
    (directory/'csv_templates.zip').write_bytes(package(blank));(directory/'schema.json').write_text(json.dumps(schema(),indent=2))
    dictionary=(ROOT/'DATA_DICTIONARY.md').read_text().replace('# Phase 1 data dictionary','# Reusable company data dictionary — schema 1.1').replace('All values are synthetic.','The downloadable Beacon company is synthetic; user packages may contain their own data.').replace('Fictional supplier display name','Supplier display name (untrusted text)').replace('Destination facility key in this sample','Reference to the single factory for orders; facility key for lanes').replace('supplier or facility endpoint type; facility in this sample','facility (supplier→factory or factory→warehouse only)')
    dictionary=re.sub(r' Sample rows: \d+\.', '',dictionary)
    dictionary+='\n\n## Keys, versions and model boundaries\nPrimary keys are explicitly listed in schema.json. Other ID columns are foreign keys to the supplier, item or facility master; week columns reference the calendar. Every sourcing policy and firm order must have a matching qualified supplier offer. Demand covers every week/product/warehouse, including zeros. Shares are fractions, not percentages: 0.6 + 0.4 = 1.\n\nSchema version 1.1 requires model_config_version 1.0 and policy_version 1.0. Headers are fixed; company row counts and IDs are configurable. Legacy schema 1.0 remains supported; its absent model_config_version means 1.0. IDs use 1–64 letters, digits, dots, underscores or hyphens and start with a letter or digit. Supplier, item and facility ID namespaces are disjoint. Text is never treated as instructions. See ../README.md for upload limits and supported single-factory, one-level BOM relationships.\n'
    (directory/'DATA_DICTIONARY.md').write_text(dictionary)

if __name__=='__main__':main()
