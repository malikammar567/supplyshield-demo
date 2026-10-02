"""Guided, keyboard-accessible public surface, isolated from private local tools."""
import streamlit as st
import pandas as pd
from phase2.engine import serializable
from phase6 import services as api
from phase9.provenance import safe_error,execution
from phase10 import demo_service as demo

@st.cache_data(max_entries=64,ttl=3600,show_spinner=False)
def _cached(name,dataset_id,engine_hash,cfg,target):return demo.calculate(name,cfg,target)

def cached(name,cfg=None,target=.95):
    return _cached(name,demo.company(name)['id'],execution('cache')['engine_source_hash'],cfg,target)

def provenance(result):
    origin='Saved verified example' if result.get('execution_origin')=='saved_verified_example' else 'Calculated by the real engine · may be reused from the one-hour demo cache'
    st.caption(f"{origin}. Execution {result['run_id']} · dataset {result['dataset_id'][:12]} · {result['created_at']}")

def totals(result):
    t=result['run']['totals'];v=result['valuation'];cols=st.columns(3)
    cols[0].metric('Units delivered',f"{t['fulfilled_units']:,}")
    cols[1].metric('Unmet demand',f"{t['unmet_units']:,}")
    cols[2].metric('Demand fulfilled',f"{100*t['unit_fill_rate']:.2f}%")
    st.write(f"**${float(v['gross_committed_resource_cost']):,.2f} committed modeled cost.** Includes purchase commitments, conversion, transportation and holding; this is not profit or realized savings.")
    provenance(result)

def downloads(result):
    a,b=st.columns(2)
    a.download_button('Download executive report',api.report(result),'SupplyShield-report.md','text/markdown',key='public_report')
    b.download_button('Download detailed results',api.export_zip(result),'SupplyShield-results.zip','application/zip',key='public_results')
    if result.get('plan'):st.download_button('Download verified plan',api.json_bytes(result['plan']),'SupplyShield-plan.json','application/json')

def render():
    st.set_page_config(page_title='SupplyShield · Recruiter demo',page_icon='📦',layout='wide')
    st.markdown('''<style>.stApp{background:#f8fafc;color:#183247}section[data-testid="stSidebar"]{background:#eef3f7}div[data-testid="stMetric"]{background:#fff;border:1px solid #cedae4;border-radius:12px;padding:16px}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #147d92!important;outline-offset:3px}h1,h2,h3{color:#183247}</style>''',unsafe_allow_html=True)
    st.title('SupplyShield')
    st.write('Explore how a supplier disruption reaches customers—and compare feasible responses.')
    st.caption('Public recruiter demo · fictional data only · Free Demo Mode · no language model, uploads or external AI services')
    with st.sidebar:
        st.header('Your demo')
        name=st.selectbox('Choose a fictional company',list(demo.COMPANIES),key='demo_company')
        step=st.radio('Explore in order',['1 · Baseline','2 · Disruption','3 · Responses','4 · Verified optimizer','5 · Free Analyst'],key='demo_step')
        st.caption('About 3 minutes. Calculations run on the hosting computer. No private company data is requested.')
        st.info('All companies, demand and demonstration assumptions are fictional.')
    ds=demo.company(name);weeks=[r['week_start'] for r in ds['company']['tables']['calendar']]
    if st.session_state.get('active_demo')!=name:
        st.session_state.active_demo=name;st.session_state.public_cfg=api.default_scenario(ds)
        st.session_state.public_latest=None;st.session_state.pop('public_trace',None)
    cfg=st.session_state.public_cfg
    st.caption(f"{name} · {len(weeks)} planning weeks · {weeks[0]} to {weeks[-1]} · USD")
    try:
        if step=='1 · Baseline':
            st.header('Start with normal operations')
            result=cached(name);st.session_state.public_latest=result;totals(result)
            st.write('This plan follows the existing purchasing, production and shipment rules. Component availability and factory labor limit what can be made.')
            weekly=result['run']['weekly']
            st.bar_chart(pd.DataFrame(serializable(weekly)).set_index('week_start')[['fulfilled_units','unmet_units']],x_label='Planning week',y_label='Customer units')
            st.dataframe(pd.DataFrame(serializable(weekly))[['week_start','demand_units','fulfilled_units','unmet_units']],hide_index=True,width='stretch')
            st.info('Next: choose “2 · Disruption” in the navigation. Orders already in transit keep their scheduled receipt dates.')
            downloads(result)
        elif step=='2 · Disruption':
            st.header('Test a supplier interruption')
            with st.form('public_scenario'):
                suppliers=[r['supplier_id'] for r in ds['company']['tables']['suppliers']]
                supplier=st.selectbox('Supplier',suppliers,index=suppliers.index(cfg['supplier_id']))
                start=st.selectbox('First affected week',weeks,index=weeks.index(cfg['start_week']))
                duration=st.selectbox('Duration in weeks',list(range(1,len(weeks)+1)),index=cfg['duration_weeks']-1)
                loss=st.slider('Supplier capacity lost (%)',0,100,cfg['capacity_loss_percentage'],10)
                submitted=st.form_submit_button('Calculate disruption',type='primary')
            if submitted:
                candidate=demo.scenario_config(ds,supplier,start,duration,loss)
                with st.spinner('Calculating orders, receipts, production and customer deliveries…'):result=cached(name,candidate,None)
                st.session_state.public_cfg=candidate;cfg=candidate
                st.session_state.pop('public_trace',None)
            else:result=cached(name,cfg,None)
            st.session_state.public_latest=result;totals(result)
            delta=result['comparison']['totals']['delta_unmet_units']
            st.write(f"Compared with normal operations: **{delta:+,} additional unmet units**. A supplier capacity reduction restricts new orders first; receipts are affected after supplier lead time.")
            st.caption(f"Displayed scenario: {cfg['supplier_id']} · {cfg['capacity_loss_percentage']}% loss · {cfg['duration_weeks']} weeks from {cfg['start_week']}. Press Calculate to apply edited controls.")
            with st.expander('Follow the disruption through the chain',expanded=True):
                st.write('New orders → dated component receipts → component stock → constrained production → dated warehouse arrivals → customer fulfillment.')
                ledger=st.selectbox('Inspect the calculated flow',['supplier_capacity','orders','receipts','component_inventory','production','shipments','warehouse_inventory'])
                st.dataframe(pd.DataFrame(serializable(result['run'][ledger])),hide_index=True,width='stretch')
            with st.expander('Which products and warehouses are affected?'):
                st.dataframe(pd.DataFrame(api.group_service(result['run'])),hide_index=True,width='stretch')
            st.info('No extra shortage within this calendar does not mean no disruption risk. Orders and impacts may extend beyond it.')
            downloads(result)
        elif step=='3 · Responses':
            st.header('Compare feasible responses')
            target=st.selectbox('Target demand fulfillment',[95,90,99])
            result=cached(name,cfg,target/100);st.session_state.public_latest=result
            rows=result['comparison']['rows'];display=[]
            for r in rows:
                display.append({'Strategy':r['strategy_id'].replace('_',' ').title(),'Status':r.get('status','calculated'),'Delivered':r.get('fulfilled_units'),'Unmet':r.get('unmet_units'),'Fill rate (%)':None if r.get('unit_fill_rate') is None else round(float(r['unit_fill_rate'])*100,2),'Committed cost (USD)':None if r.get('gross_committed_resource_cost') is None else round(float(r['gross_committed_resource_cost']),2)})
            st.dataframe(pd.DataFrame(display),hide_index=True,width='stretch')
            st.info(result['comparison']['ranking']['target_statement'])
            st.write('This is the lowest-cost tested strategy, not a global optimum. Alternate suppliers have finite capacity and must be qualified. Expediting cannot create missing supply. Prepared inventory is purchased ahead of the disruption and includes preparation costs.')
            st.caption('ClearDesk expedited lanes, annual 20% holding and preparedness settings are synthetic assumptions. Beacon has qualified-alternate responses only; no expedited lanes or free preparedness are invented.')
            with st.expander('Response applicability and infeasible configurations'):
                st.write('Responses use the saved company configurations. ClearDesk alternate-source and prepared-inventory rules are designed for S01; choosing another disrupted supplier does not automatically redesign those rules. All results still follow actual qualification, capacity and timing constraints.')
                st.dataframe(pd.DataFrame(serializable(rows)),hide_index=True,width='stretch')
            provenance(result);downloads(result)
        elif step=='4 · Verified optimizer':
            st.header('Inspect an independently verified 95% plan')
            result=demo.saved_optimization(name);st.session_state.public_latest=result
            st.info('Saved example: first supplier loses 60% capacity for four weeks from week 1. This example is separate from edited disruption settings. No public solver is started.')
            totals(result);s=result['solver']
            st.write('**'+('Optimal within the configured model' if s['proven_optimal'] else 'Feasible, not proven optimal')+'** · Independent replay: '+result['verification']['status'])
            if s['relative_gap'] is not None:st.write(f"Reported solver gap: **{100*float(s['relative_gap']):.3f}%**. The incumbent is a feasible plan; the bound limits how much better the modeled objective might be.")
            st.caption('A 95% aggregate target can allow concentrated product or warehouse shortages. Full custom solves remain available in the local application.')
            downloads(result)
        else:
            st.header('Ask about the verified results')
            st.success('Free Demo Mode · deterministic, engine-connected explanations · no language model or API key')
            example=st.selectbox('Example question',['Explain the result','Why does cost change?','What are the bottlenecks?','What assumptions are used?','Compare mitigation strategies at 95% fill rate'])
            with st.form('public_question'):
                q=st.text_input('Your question',value=example,max_chars=500)
                go=st.form_submit_button('Explain with verified numbers',type='primary')
            if go:
                latest=st.session_state.public_latest or cached(name,cfg,None)
                result,trace=demo.answer(name,q,cfg,latest)
                st.session_state.public_latest=result;st.session_state.public_trace=trace
            if st.session_state.get('public_trace'):
                trace=st.session_state.public_trace;st.markdown(trace['explanation'].replace('$',r'\$'))
                with st.expander('See the structured request and cited result fields'):st.json(serializable(trace))
                provenance(st.session_state.public_latest)
            else:st.write('Start with a result explanation, then ask about a product, warehouse, cost or assumptions. Open the verified optimizer step before asking about its plan.')
        with st.expander('How it works, accessibility and limitations'):
            st.write('The Python engine calculates inventory balances, BOM consumption, supplier capacity, labor-constrained production and dated deliveries. The Analyst explains those calculations using deterministic templates.')
            st.write('Use Tab and Shift+Tab to move between controls. Every control has a visible label. Charts have accompanying tables; color is not the only way to identify a result.')
            st.write(api.LIMITATIONS)
            st.caption('This is a portfolio MVP, not a production-certified decision system. No accounts, uploads or external AI connections are provided here.')
    except Exception as error:
        st.error(safe_error(error))
        st.info('Adjust the demo settings and retry. For a four-week example, choose the first planning week. No failed calculation is presented as a successful plan.')
