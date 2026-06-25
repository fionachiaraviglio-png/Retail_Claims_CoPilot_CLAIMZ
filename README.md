# ClearProcess — AI-Powered Auto Insurance Claims Management

> End-to-end automation platform for auto insurance claims processing, built with a multi-agent AI system on the Anthropic Claude API.  
> Built for **Zurich Hyper Challenge 2026**.

---

## What is this?

ClearProcess automates the full lifecycle of an auto insurance claim — from the first notice of loss (FNOL) through workshop repair to final invoice verification and claim closure.

**Key features:**
- 🤖 **4 AI agents** powered by Claude: triage, work order generation, budget reconciliation, and invoice verification
- 🔄 **10-stage structured workflow** with entry validation, KPI tracking and SLA alerts at every transition
- 👤 **Dual portal** — handler (adjuster) view and customer (policyholder) view in a single app
- 📄 **Document intelligence** — PDF extraction, multi-image inspection analysis, fuzzy budget matching
- 📊 **Full audit trail** — every AI call, timestamp and decision recorded on the claim

---

## Architecture overview

```
Streamlit UI (handler + customer portal)
        │
        ▼
Workflow Engine ──► Stage Handler ──► AI Agent (Claude)
        │
        ▼
SQLite Database + Document Store (uploads/)
```

**AI Agents:**
| Agent | Trigger | What it does |
|---|---|---|
| `FNOLTriageAgent` | Claim creation | Reads FNOL + policy PDF → coverage decision, confidence score, risk flags |
| `WorkOrderDraftAgent` | Workshop inspection | Analyses photos → generates Work Order line items with repair classification |
| `WorkOrderReconciliationAgent` | Budget upload | Fuzzy-matches workshop budget vs. approved WO → fills costs, flags extras |
| `BillComparisonAgent` | Final invoice upload | Compares invoice vs. approved WO line by line → flags discrepancies |

---

## Requirements

- Python 3.11+
- An Anthropic API key ([get one here](https://console.anthropic.com))

---

## Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/fionachiaraviglio-png/Retail_Claims_CoPilot_CLAIMZ.git
cd Retail_Claims_CoPilot_CLAIMZ
```

### 2. Create a virtual environment

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac / Linux
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your API key

Create a `.env` file in the project root:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

> ⚠️ Never commit this file. It is already in `.gitignore`.

Optional settings you can add to `.env`:

```bash
ANTHROPIC_MODEL=claude-opus-4-7          # primary model (default)
CLAUDE_FAST_MODEL=claude-haiku-4-5-20251001  # fast model for lightweight tasks
NOTIFICATIONS_ENABLED=false              # set to true to enable notification agent (adds latency)
```

### 5. Run the app

```bash
streamlit run streamlit_app.py
```

The app will open at **http://localhost:8501**

---

## How to use the app

### Handler (Adjuster) flow

1. **Dashboard** — see all claims, click "Ver →" to open a claim, or "+ Nuevo Siniestro" to create one
2. **New Claim** — upload the claim PDF and policy PDF (or paste JSON) → AI processes the first stage automatically
3. **Claim detail → Triage tab** — run the FNOL triage agent to get a coverage recommendation
4. **Claim detail → Decisión Ajustador tab** — approve, reject, or request additional documents
5. **Claim detail → Taller tab** — mark vehicle received → upload inspection photos → generate Work Order with AI
6. **Claim detail → Orden de Trabajo tab** — review WO line items → upload workshop budget → AI reconciles costs → approve and start repair → when ready, mark vehicle for pickup → upload final invoice → AI compares → approve and close

### Customer flow

1. Toggle **"Vista del cliente"** at the top of any page to switch to the customer view
2. **Search** for a claim by its 8-character ID
3. **Estado tab** — see a visual timeline of the claim progress with timestamps
4. **Documentación tab** — view and download all documents; upload documents requested by the adjuster
5. **Póliza tab** — view policy details

---

## Project structure

```
├── streamlit_app.py          # Main UI — all pages and routing
├── database.py               # SQLite persistence layer
├── storage.py                # File upload storage (uploads/<claim_id>/)
│
├── agents/
│   ├── base_agent.py         # Abstract base class for all AI agents
│   ├── fnol_triage_agent.py
│   ├── work_order_draft_agent.py
│   ├── work_order_reconciliation_agent.py
│   ├── bill_comparison_agent.py
│   ├── notification_agent.py
│   ├── damage_assessment_agent.py
│   ├── coverage_analysis_agent.py
│   └── kpi_monitor_agent.py
│
├── core/
│   ├── workflow.py           # ClaimWorkflow orchestrator
│   └── stages/               # One file per workflow stage
│       ├── base_stage.py
│       ├── claim_report.py
│       ├── vehicle_intake.py
│       ├── damage_inspection.py
│       ├── coverage_analysis.py
│       ├── work_order_creation.py
│       ├── spare_parts_purchase.py
│       ├── repair_process.py
│       ├── work_order_closure.py
│       ├── vehicle_delivery.py
│       └── customer_billing.py
│
├── models/
│   ├── claim.py              # Pydantic models (Claim, WorkOrder, TriageResult, ...)
│   └── enums.py              # WorkflowStage, ClaimStatus, etc.
│
├── config/
│   └── settings.py           # Environment config (API keys, model names)
│
├── api/
│   └── routes.py             # FastAPI routes (optional REST API)
│
├── flow_diagram.html         # Visual process flow diagram (open in browser → print as PDF)
├── TECHNICAL_SUMMARY.md      # Hyper Challenge submission document
├── PROCESS_DESIGN.md         # Detailed architecture and stage-by-stage design
├── requirements.txt
├── .env.example              # Template for environment variables
└── .gitignore
```

---

## Workflow stages

| # | Stage | Triggered by | AI agent |
|---|---|---|---|
| 1 | CLAIM_REPORT | Claim creation form | FNOLTriageAgent |
| 2 | VEHICLE_INTAKE | Adjuster clicks "Approve" | — |
| 3 | DAMAGE_INSPECTION | Handler marks vehicle received | — |
| 4 | WORK_ORDER_CREATION | Handler uploads photos + generates WO | WorkOrderDraftAgent |
| 5 | SPARE_PARTS_PURCHASE | Handler uploads workshop budget | WorkOrderReconciliationAgent |
| 6 | REPAIR_PROCESS | Handler approves WO + starts repair | — |
| 7 | WORK_ORDER_CLOSURE | Handler marks vehicle ready for pickup (auto) | — |
| 8 | VEHICLE_DELIVERY | Handler uploads conformity receipt | — |
| 9 | CUSTOMER_APPROVAL_BILLING | Handler confirms vehicle collected | — |
| 10 | COMPLETED | Handler approves final invoice | BillComparisonAgent |

---

## Known limitations

- **No authentication** — handler/customer are distinguished by a UI toggle only. Not suitable for production without adding auth.
- **SQLite** — single-file database, not suitable for concurrent users. Replace with PostgreSQL for production.
- **PDF extraction** — uses regex-based extraction. Works well on structured documents; may miss fields on scanned PDFs.
- **Notifications disabled by default** — the notification agent adds ~2–5s per stage. Enable with `NOTIFICATIONS_ENABLED=true` in `.env`.

---

## Cost estimate

| Claim complexity | Approx. tokens | Approx. cost |
|---|---|---|
| Simple (1 photo, short policy) | ~50,000 | ~$0.75 |
| Standard (5 photos, full policy) | ~120,000 | ~$1.80 |
| Complex (10+ photos, disputes) | ~250,000 | ~$3.75 |

Primary cost driver: `WorkOrderDraftAgent` (multi-modal photo analysis).

---

## Tech stack

| Layer | Technology |
|---|---|
| AI | Anthropic Claude API (`claude-opus-4-7`) |
| UI | Streamlit |
| Data modelling | Pydantic v2 |
| Database | SQLite |
| PDF processing | PyPDF |
| Language | Python 3.11+ |

---

## Team

Built by Team CLAIMZ · Zurich Hyper Challenge 2026
