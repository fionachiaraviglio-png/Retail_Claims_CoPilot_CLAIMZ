# Hyper Challenge 2026 – Technical Summary

> **How to fill in this document**
> - Replace the `<placeholder>` text with your own content.
> - The bullet points under each section are *guiding questions* – you don't have to answer all of them, and you can answer more broadly.
> - Keep code blocks (` ```...``` `) for any snippets, model names, or config you want to highlight.
> - For the Process Design Map, either embed an image link/path or describe your architecture in text/ASCII.
> - Save the final file as `<UseCase>_<TeamName>.md` (matching the GitHub repo naming convention – e.g. `CI_Customer_Scouting_TeamAlpha.md`) and submit via the dedicated submission folder.

---

## Team

- **Team name:** `<placeholder>`
- **Use case:** Auto Insurance Claims Processing
- **Platform used:** `claude_api`
- **Team members:**
  - `<Name, email>`
  - `<Name, email>`

---

## Where to find your submission

| Artifact | Filename or URL |
|---|---|
| GitHub repo | `<placeholder>` |
| Exported workflow / solution | `clearprocess.zip` |
| Copilot Studio agent name | N/A |
| Demo video | `<placeholder>` |
| Video transcript | `<placeholder>` |
| Pitch deck | `<placeholder>` |
| Technical summary | `TECHNICAL_SUMMARY.md` |
| Process design map | `flow_diagram.html` / `PROCESS_DESIGN.md` |

---

## Models & tools summary

| Stage | Model / Tool | Purpose |
|---|---|---|
| FNOL Triage | `claude-opus-4-7` | Coverage decision, confidence score, matched policy clauses, risk flags |
| Work Order Creation | `claude-opus-4-7` | Analyse inspection photos → generate WO line items with repair type classification |
| Budget Reconciliation | `claude-opus-4-7` | Fuzzy-match workshop budget against approved WO, fill monetary amounts, flag unapproved items |
| Invoice Verification | `claude-opus-4-7` | Line-by-line comparison of final invoice vs. approved WO, discrepancy detection |
| Notifications (disabled) | `claude-haiku-4-5-20251001` | Stage-transition notifications to customer / workshop (disabled for latency) |
| PDF extraction | PyPDF + regex | Extract structured fields from claim and policy PDFs |
| UI framework | Streamlit | Dual-portal web app (handler + customer), session-state routing |
| Data layer | SQLite + Pydantic v2 | Claim persistence, document storage, data contract enforcement |

---

*To better understand and evaluate your AI solution, we ask you to provide a short overview and a system design process map. This will help us assess the architecture, logic, and control mechanisms behind your prototype.*

*The goal is to clearly explain **what** you built, **how** it works, and **how** you ensure it operates effectively and responsibly.*

---

## 1. What did you build?

**Goal:** ClearProcess automates the end-to-end lifecycle of an auto insurance claim — from the first notice of loss (FNOL) through workshop repair to final invoice verification and claim closure — using a multi-agent AI system built on the Anthropic Claude API.

**Key components:**

- **4 specialised AI agents**, each responsible for a distinct decision point:
  - `FNOLTriageAgent` — reads FNOL data + policy PDF and returns a structured coverage decision with confidence score, matched clauses, exclusions and risk flags
  - `WorkOrderDraftAgent` — analyses workshop inspection photos using Claude vision to generate a preliminary Work Order with per-item repair type classification
  - `WorkOrderReconciliationAgent` — fuzzy-matches the workshop's budget PDF against the adjuster-approved WO, fills in monetary amounts per line item and flags unapproved additions
  - `BillComparisonAgent` — compares the final workshop invoice against the approved WO line by line, returning discrepancy amounts and an alert reason per line

- **10-stage workflow engine** with entry validation at every transition: FNOL → Triage → Adjuster Decision → Workshop Intake → Damage Inspection → Work Order Creation → Budget Reconciliation → Repair → Vehicle Pickup → Invoice Verification → Completed

- **Dual-portal Streamlit UI**: a handler (adjuster) view and a customer (policyholder) view in a single app, switchable via a toggle. The customer portal shows real-time claim status, document upload slots keyed to the adjuster's document requests, and downloadable documents.

- **KPI & SLA tracking**: automated time tracking at each stage transition with defined SLA targets (e.g. 48h for damage inspection, 120h for WO creation, 240h for repair).

---

## 2. How did you build it?

**Agent architecture:**
All agents inherit from a `BaseAgent` abstract class that handles async Claude API calls, tool orchestration and retries. Each subclass defines a domain-specific system prompt and tool set. Agents are invoked synchronously from the Streamlit UI via `asyncio.run()`.

```
BaseAgent
├── FNOLTriageAgent       — single-pass batch mode, no tool loop
├── WorkOrderDraftAgent   — tool loop: identify → classify → recommend → build WO
├── WorkOrderReconcilAgent — tool loop: extract budget → fuzzy match → flag → build final WO
└── BillComparisonAgent   — tool loop: extract invoice → compare → flag discrepancies
```

**Orchestration:**
A `ClaimWorkflow` class dispatches to stage handlers via `process_stage(claim, target_stage, data)`. Stages can be targeted directly (jumping intermediate stages) but cannot go backwards. Each handler validates required fields before running, preventing invalid state transitions.

**Data & knowledge:**
- Policy documents and claim forms are uploaded as PDFs and extracted with PyPDF + regex
- Inspection photos are passed directly as base64-encoded bytes to Claude's vision API
- No external knowledge base or vector store — all reasoning is done in-context from the uploaded documents

**Technologies:**

| Layer | Technology |
|---|---|
| AI | Anthropic Claude API (`claude-opus-4-7`, `claude-haiku-4-5-20251001`) |
| UI | Streamlit (session-state page routing) |
| Data modelling | Pydantic v2 (strict field contracts at every stage boundary) |
| Persistence | SQLite + file-based document store (`uploads/<claim_id>/`) |
| PDF processing | PyPDF + regex extraction |
| Language | Python 3.13, async/await throughout |

---

## 3. How do you control and evaluate it?

**Human-in-the-loop gates:**
Every AI output requires an explicit adjuster action before the workflow advances. No stage auto-progresses based solely on AI output, with one exception: triage auto-approval fires only when confidence ≥ 90% AND zero risk flags are present.

**Per-output quality controls:**

| Agent | Control mechanism |
|---|---|
| FNOLTriageAgent | Confidence score (0–1) shown to adjuster; risk flags and exclusions displayed prominently |
| WorkOrderDraftAgent | Handler approves/rejects each line item individually via a slider before requesting a workshop budget |
| WorkOrderReconciliationAgent | Unapproved items (added by workshop, not in original WO) flagged with ⚠️ and highlighted in the review table |
| BillComparisonAgent | Per-line discrepancy % and alert reason shown before adjuster approves payment |

**KPI monitoring:**
Each stage records elapsed hours. SLA breaches are flagged in the `kpi_status` field on the claim. Stage timestamps (`fecha_aviso`, `fecha_ingreso_taller`, `fecha_inspeccion`, `fecha_creacion_ot`, `fecha_cierre_ot`, `fecha_salida_taller`, `fecha_cierre`) provide a full audit trail.

**Known limitations and risks:**

| Risk | Mitigation |
|---|---|
| Hallucination in WO line items | Handler reviews and approves every item individually |
| PDF extraction quality (regex-based) | Fields fall back to empty / "Unknown"; handler can correct before proceeding |
| Model latency on complex agents | Notification agent disabled by default; Haiku for lightweight tasks |
| No authentication | Current prototype has no login — handler/customer distinguished by UI toggle only |

---

## 4. How do you scale it?

**Path to production:**

- **Replace SQLite** with PostgreSQL to support concurrent users, proper relational queries and transactional safety
- **Add authentication** (e.g. Auth0 or Azure AD) to separate handler and customer sessions properly
- **Deploy on cloud** (Azure App Service / AWS ECS) with environment-based config management
- **Move notifications to async background tasks** (Celery / Azure Service Bus) to eliminate blocking latency
- **Replace regex PDF extraction** with a Claude-based document parsing agent for higher accuracy on real-world documents
- **Add a vector store** (e.g. Azure AI Search) for policy knowledge retrieval to support more nuanced coverage decisions
- **Implement rate limiting and cost controls** per claim / per team to manage API spend at scale

**Scalability factors:**
- The agent architecture is stateless — each agent call is independent and horizontally scalable
- The stage engine is claim-scoped — multiple claims can be processed in parallel without interference
- Token usage per claim is predictable and bounded (see cost section), making budget forecasting straightforward

---

## 5. Costs considerations

**Prototype build cost:** Primarily engineer time. API costs during development were minimal (~$20–50 in testing).

**Cost to run at prototype level (per claim, end-to-end):**

| Scenario | Approx. tokens | Approx. cost (`claude-opus-4-7`) |
|---|---|---|
| Simple claim (1 photo, short policy) | ~50,000 tokens | ~$0.75 |
| Standard claim (5 photos, full policy) | ~120,000 tokens | ~$1.80 |
| Complex claim (10+ photos, disputes) | ~250,000 tokens | ~$3.75 |

**Models used per phase:**

| Phase | Model | Reason |
|---|---|---|
| FNOL triage, WO generation, reconciliation, invoice check | `claude-opus-4-7` | High accuracy required; multi-modal vision for photos |
| Notifications (disabled by default) | `claude-haiku-4-5-20251001` | Speed and cost — ~20× cheaper than Opus |

**Token profile:** Moderately token-heavy due to multi-modal inputs (photos) and long-context policy PDFs. The primary cost driver is the WorkOrderDraftAgent (up to 60K tokens with many photos).

**Forecasted cost at scale (1,000 claims/month):**
- Average claim ~$2.00 in API costs → ~$2,000/month in model costs
- Significant reduction possible by: caching policy documents, batching WO line-item analysis, using Haiku for classification steps

---

## 6. Learnings

**What we learned:**

- **Multi-agent orchestration requires careful state management.** Having a strict workflow engine with entry validation at each stage caught bugs early and made the system far easier to debug than a more free-form approach.

- **Streamlit's rerender model requires deliberate patterns.** The two-phase session-state pattern (set flag → rerun → disabled button → process) was essential for good UX on long-running AI calls. Without it, users could accidentally double-submit.

- **Blocking notifications were the biggest latency driver.** Every stage was calling a notification agent synchronously — fixing the model ID (`eu.anthropic.claude-opus-4-8` → `claude-haiku-4-5-20251001`) and disabling the agent by default cut stage transition time dramatically.

- **Pydantic v2 class-identity issues in hot-reload scenarios** required a `field_validator` workaround to safely round-trip claim objects through the stage result model.

- **`workflow.advance()` does not advance the stage** — it processes the current stage and leaves `current_stage` unchanged. All stage progressions must use `workflow.process_stage(claim, target_stage)` explicitly. Discovering this late caused several hard-to-diagnose bugs.

- **Real-world PDF quality is highly variable.** Regex extraction works well on structured documents but fails on scanned PDFs. An AI-based extraction agent would be a priority upgrade for production.

**What we would do differently:**

- Use a proper database (PostgreSQL) and authentication from day one
- Build PDF extraction as an AI agent, not regex, from the start
- Design the notification system as async background tasks from the beginning
- Use Streamlit multi-page apps instead of session-state routing for cleaner code organisation
- Invest more time in testing with real insurance documents early in the process

---

*Submitted by: `<your name>` · `<email>` · `<date>`*
