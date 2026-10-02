[# SupplyShield public recruiter demo

This separate interface contains fictional ClearDesk and Beacon data only. Visitors follow Baseline → Disruption → Responses → Verified optimizer → Free Analyst. It has no company upload controls, provider selector, credentials, account system or arbitrary JSON editor. The complete local workspace remains available through `python3 launch.py`.

## Try it locally

From the project folder, after the normal README setup:

```bash
python3 launch_demo.py
```

The first available local port is 8510–8520. Read the address printed on launch. For a standalone copy of this hosting package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run recruiter_app.py
```

On Windows, activate with `.venv\Scripts\activate` instead. No API key, Ollama, credit card or paid AI service is required.

## Publish for free with Streamlit Community Cloud

**Publishing has not happened yet.** A GitHub repository and Streamlit Community Cloud account are required. Official guidance: [free hosting](https://docs.streamlit.io/deploy), [account and GitHub setup](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started), [deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [resource limits](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app). Verified against official documentation on October 1, 2026; free service terms and limits may change.

1. Create a GitHub account if needed, then create an empty public repository named `supplyshield-demo`. Do not upload the full Desktop project.
2. Extract `SupplyShield-public-demo.zip`. Upload its **contents** to the repository root, preserving folders. Include the `.streamlit` folder if your file picker displays hidden files. No secrets settings are needed; its theme/telemetry settings are already included.
3. Create/sign into Streamlit Community Cloud and connect that repository. Complete account agreements and GitHub permissions yourself.
4. Select **Create app**, choose the repository and its default branch, and set the main file to `recruiter_app.py`.
5. In Advanced settings choose Python 3.12, then deploy. Python 3.12 is a deployment recommendation; the recorded local acceptance environment is Python 3.14.6 on macOS, so verify the cloud run before sharing.
6. Open the generated `*.streamlit.app` link, try both companies, and follow the acceptance checks below. Put that link on your resume only after it works.

GitHub and Streamlit access is the remaining publication prerequisite. The demo package includes source code and synthetic datasets intended for public display, not private uploaded data, local environments, API keys or historical personal attachments. The source owner retains existing rights; no new license grant is implied.

## What is live and what is saved?

Baseline, bounded supplier capacity loss, and mitigation comparisons call the existing real Python engines. Scenario requests must stay within the company's calendar. Comparison targets are 90%, 95% or 99%. Calculations may be reused from a one-hour, 64-entry cache keyed by company content, engine source and settings; displayed execution IDs identify the calculation rather than claiming each page refresh is a new run.

The optimizer page displays the saved, independently replayed Phase 9 **95%** example for the first supplier at **60% loss for four weeks**. It explicitly does not solve visitors' arbitrary requests. This prevents anonymous visitors from starting repeated long-running solver jobs on free shared hosting. The saved plan must match current dataset, request and engine hashes and have passing engine/replay checks. Custom optimization remains in the local app. Regenerate saved examples locally with `python3 demo.py`, then rebuild the package.

The Free Analyst uses deterministic intent parsing and templates. It supports explanations, product/warehouse questions, bounded supplier disruptions, comparison targets and the saved optimizer example. It runs no language model and has no network provider adapter. The active mode and calculation evidence are visible. Hosting still receives browser requests; “no external AI” does not mean that a hosted service is local to a visitor's computer.

## Three-minute walkthrough

1. Choose **ClearDesk Manufacturing** and Baseline: 2,334 delivered, 18 unmet, 99.23% fill.
2. Open Disruption. Keep S01, week 1, four weeks, 60% loss and calculate: 1,912 delivered, 440 unmet. Inspect capacity, orders, receipts, component inventory, production and warehouse flows.
3. Open Responses at 95%. Expedited transport fulfills 2,252 units with 100 unmet and is the lowest-cost tested strategy meeting this target; do not call it a global optimum or realized savings.
4. Open Verified optimizer: the stored example fulfills 2,235 units with 117 unmet (95.03%). Show its feasible-not-proven status, solver gap and passing independent replay.
5. Open Free Analyst and ask “Explain the result”, followed by “Why does cost change?”. Download the executive report, detailed results or verified plan from the relevant step.
6. Switch to **Beacon Instruments**: baseline 76 delivered / 2 unmet (97.44%). Its IDs, six-week horizon and qualifications differ. No new engine code or fabricated expedited lanes are used.

Numbers above identify stored/default fictional examples. Changed controls must produce newly calculated results, not fixed demo numbers. Neither company represents real performance.

## Accessibility and checks before sharing

Use Tab/Shift+Tab and keyboard activation to navigate labeled controls. Charts have data tables and text summaries. Result meaning does not rely on color. The layout uses responsive Streamlit controls. Automated UI acceptance does not constitute an accessibility certification; check screen readers and touch/mobile behavior on the deployed service.

Before sharing the URL: switch both companies; verify default numbers above; test zero loss; submit an invalid late-start/four-week duration and expect an actionable error; compare at 95%; open the saved optimizer; explain its current result; download reports; confirm there is no upload, API key, local-model or hosted-provider control. Never put private company data or keys in this repository.

Free hosting may sleep, restart or slow under traffic. Cache/session state can disappear and must not be treated as persistent storage. Anonymous visitors cannot start solvers, but all other calculations still use server resources. This is a public portfolio demo with no uptime guarantee, audited multi-user production security or globally optimal claim.
](https://github.com/malikammar567/supplyshield-demo.git)
