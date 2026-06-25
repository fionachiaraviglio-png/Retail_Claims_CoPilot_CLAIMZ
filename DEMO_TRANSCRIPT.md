# ClearProcess — Demo Video Transcript
**Zurich Hyper Challenge 2026 · Team CLAIMZ**

---

## [INTRO — 0:00]

*[Screen shows the ClearProcess dashboard with the Zurich logo]*

"Hi everyone. Today we're going to walk you through ClearProcess — our AI-powered auto insurance claims platform.

The problem we set out to solve is this: today, processing an auto insurance claim is slow, manual, and opaque. The adjuster reads policy documents by hand, builds repair work orders line by line, compares workshop budgets manually, and the customer has no visibility into what's happening with their car.

ClearProcess changes all of that. We've built a multi-agent AI system that automates every major decision point in the claims lifecycle — from the moment a claim comes in to the moment it's closed.

Let me show you how it works."

---

## [THE DASHBOARD — 0:30]

*[Screen shows the claims dashboard with a list of claims, metrics at the top]*

"This is the handler dashboard — what an adjuster sees when they log in.

At the top, you can see our summary metrics: total claims, how many are active, how many are waiting for documents from the customer, and how many are completed.

Below that, each claim is listed as a row — you can see the policy number, the vehicle, the current stage in the workflow, and the status. Each row has a 'View' button to jump directly into that claim.

We also have a toggle at the top — this lets us switch between the handler view and the customer view in the same app. I'll show you the customer portal a bit later.

Let's start by creating a brand new claim."

---

## [CREATING A NEW CLAIM — 1:00]

*[Screen shows the "New Claim" form with two file upload areas]*

"We click 'New Claim' and we land on the intake form.

Here, the adjuster — or in some cases the customer themselves — uploads two documents: the claim form, which contains the incident details, and the policy document, which tells us what's covered.

*[Drags and drops two PDF files into the upload areas]*

I'm going to upload a real claim PDF and a policy PDF here. You can see the app immediately parses both documents — it extracts key fields like the policyholder's name, the vehicle registration, the incident date, and the incident description from the claim form, and coverage details, validity dates and deductibles from the policy.

*[Shows extracted JSON preview]*

In a production scenario, this data would come directly from Zurich's core systems. For the prototype, we're parsing it from the uploaded PDFs.

Now I'll click 'Create and process initial stage'. Watch what happens."

---

## [FNOL TRIAGE — AI AGENT #1 — 1:45]

*[Loading spinner appears: "Processing claim with AI, please wait..."]*

"While we wait, let me explain what's happening in the background.

Our first AI agent — the FNOLTriageAgent — is reading the FNOL data alongside the full policy document. It's identifying which policy clauses apply to this incident, checking for any exclusions, looking for risk flags or inconsistencies in the claim, and producing a coverage recommendation with a confidence score.

*[Spinner disappears, screen transitions to claim detail — Triage tab]*

And there it is. Let's look at what the agent produced."

---

## [TRIAGE RESULTS — 2:10]

*[Screen shows the Triage tab with a green "COVERED" badge at 94% confidence]*

"The agent has returned a decision: **COVERED**, with 94% confidence. You can see that immediately in the large badge at the top.

Below that, the agent's recommendation: the incident is a rear collision, and it falls within the comprehensive coverage section of this policy. No exclusions apply.

If I expand the 'Clauses matched' section — *[clicks expander]* — I can see exactly which clauses the agent identified as relevant. This is the work that an adjuster would previously do manually by reading through the full policy document.

There are no risk flags and no missing information — this claim is clean.

Because the confidence is above 90% and there are no risk flags, the system has flagged this as eligible for **automatic approval**. You can see the green banner here suggesting the adjuster can approve directly.

Let's go to the Decision tab and do exactly that."

---

## [ADJUSTER DECISION — 2:55]

*[Screen shows the Decision Ajustador tab with three buttons: Approve, Reject, Request Documents]*

"In the Decisión Ajustador tab, the adjuster has three options: Approve, Reject, or Request additional documents from the customer.

Given the triage result, we're going to approve this claim.

*[Clicks 'Approve' button]*

The system records the decision, timestamps it, and automatically advances the workflow to the **Vehicle Intake** stage — the car is now expected to arrive at the workshop.

Notice how the tab now shows the decision in read-only mode — a green checkmark, 'Approved'. No one can accidentally change it. The audit trail is locked in."

---

## [WORKSHOP — VEHICLE ARRIVES — 3:25]

*[Screen shows the Taller tab with a single primary button: "Mark vehicle at workshop"]*

"We're now in the Taller tab — the workshop view.

Right now, only one thing can happen: the workshop marks the vehicle as received. This is intentional — we designed the interface to be sequential, showing only the relevant action at each moment.

*[Clicks 'Mark vehicle at workshop']*

The vehicle is now in. The stage advances to **Damage Inspection**, and the customer — as we'll see later — immediately sees this reflected in their portal."

---

## [WORK ORDER GENERATION — AI AGENT #2 — 3:50]

*[Screen now shows inspection text area and image uploader]*

"Now we're in the inspection phase. The workshop technician writes up their inspection notes and uploads photos of the damage.

*[Types in the text area: "Front bumper impact, cracked headlight unit, hood deformation, paint damage on front panel"]*

*[Uploads 4 photos of a damaged car front]*

And now we click **Generate Work Order with AI**.

*[Loading spinner: "Generating work order with AI, please wait..."]*

This is our second AI agent — the WorkOrderDraftAgent. It's doing something remarkable: it's looking at these photos using Claude's vision capabilities and the inspection text, and generating a structured repair work order — line by line — with the correct repair classifications.

*[Spinner disappears, screen transitions to Work Order tab]*

Let's go to the Work Order tab to see what it produced."

---

## [WORK ORDER REVIEW — 4:35]

*[Screen shows the Work Order tab with a table of line items, all amounts showing "—"]*

"Excellent. The AI has generated 8 line items. You can see each one classified correctly:

- The headlight unit is marked as 'Sustituir / Cambiar' — a full replacement
- The hood repair is 'Reparar / Grave' — severe damage requiring major work  
- The bumper gets 'Reparar / Mediano' — medium repair
- Front panel painting is 'Pintar'

Each item also has an AI recommendation — the agent has flagged which items it believes are covered under the policy based on the triage analysis.

The adjuster now reviews each line item and approves or rejects individually using these sliders. In this case, all items look correct.

*[Clicks 'Save decisions' — all items approved]*

The work order is now in 'handler reviewed' state. The workshop can now submit their budget."

---

## [BUDGET RECONCILIATION — AI AGENT #3 — 5:20]

*[Screen shows Phase B: "Upload workshop budget to continue"]*

"The next step is for the workshop to submit their budget — a PDF with the actual cost breakdown for each repair item.

*[Uploads a budget PDF]*

I'll click **Send budget and reconcile**.

*[Loading spinner: "Analysing budget with AI, please wait..."]*

Our third agent — the WorkOrderReconciliationAgent — is now doing a fuzzy match between the workshop's budget and our approved work order. It handles variations in naming, partial descriptions, and different ordering of items.

*[Results appear — table with costs filled in per line item]*

Look at this. Every line item now has its costs filled in: labour, parts, painting costs. The total comes to $3,847.

One item has been flagged with a warning — *[points to ⚠️ row]* — 'BROCHES/clips (qty 10)' was in the workshop's budget but was **not** in our original approved work order. This is exactly the kind of thing that would get missed in a manual review.

The damage severity is automatically computed as **Mediano** — medium — based on the repair types present."

---

## [STARTING REPAIR — 5:55]

*[Screen shows final review form with per-item sliders and "Initiate Repair" button]*

"The adjuster does a final review of all line items with their costs. They can approve or reject each one individually here — including that flagged item.

*[Adjusts the unapproved item slider to NO]*

We'll reject the clips — they weren't in scope. Everything else gets approved.

*[Clicks 'Initiate Repair']*

The workflow advances to **Repair in Progress**. Let me switch to the customer portal to show you what the policyholder sees right now."

---

## [CUSTOMER PORTAL — 6:25]

*[Toggles to Customer view — shows the customer dashboard]*

"I'm switching to the customer view using the toggle at the top. This is what the policyholder would see on their phone or computer.

They can search for their claim by ID — the 8-character reference code they received when they filed.

*[Types in the claim ID, presses Search]*

*[Claim detail opens — Estado tab shows a visual timeline]*

Here's the customer's view of their claim. Three main stages: Filing, Workshop, and Completed.

The Workshop stage is currently active — highlighted in orange. If I expand it, I can see the sub-stages:

- **Intake** — green, done ✅  
- **Damage Inspection** — green, done ✅  
- **Repair in progress** — orange, current 🟠 — with today's date  
- **Vehicle pickup** — grey, pending  

The customer knows exactly where their car is and what's happening to it. No phone calls needed.

They can also go to the **Documentation tab** to see all their documents and upload anything the adjuster has requested."

---

## [VEHICLE READY — CUSTOMER NOTIFICATION — 7:10]

*[Switches back to handler view — Taller tab]*

"Back to the handler view. The workshop has finished the repair. The adjuster clicks **Mark vehicle ready for pickup**.

*[Clicks button — spinner briefly shows]*

The workflow has now automatically advanced through Work Order Closure to the **Vehicle Delivery** stage.

If we switch back to the customer portal —

*[Toggles to customer view]*

— the customer's timeline now shows 'Vehicle pickup' in orange, and a green banner has appeared: **'Your vehicle is ready for collection. Head to the workshop to pick it up.'**

Real-time. Automatic. No manual communication needed."

---

## [VEHICLE HANDOVER — 7:40]

*[Switches back to handler view — Taller tab showing the delivery section]*

"Back on the handler side. Once the customer collects the vehicle, the workshop uploads the signed customer satisfaction receipt — the conformity document.

*[Uploads a PDF receipt file]*

*[Clicks 'Vehicle collected']*

This advances the claim to the final stage: **Customer Approval and Billing**. The workshop stage is complete — the Taller tab now shows a green confirmation box directing the adjuster to the Work Order tab to process the final invoice."

---

## [FINAL INVOICE — AI AGENT #4 — 8:10]

*[Screen shows Work Order tab — Phase C — with bill upload section visible]*

"This is the moment of truth for the insurer: the workshop submits their final invoice — what Zurich actually pays them.

*[Uploads final invoice PDF]*

*[Clicks 'Analyse invoice with AI']*

Our fourth and final agent — the BillComparisonAgent — compares the final invoice against the approved work order line by line.

*[Results appear — a comparison table with ✅ and ⚠️ icons per row]*

Most lines match. But look at this: one item on the invoice is $120 higher than what was approved. The agent has flagged it, shown the exact discrepancy amount and percentage, and written a clear alert reason.

The adjuster reviews this, adds a note, and decides whether to approve or query it.

*[Adjuster types a note, clicks 'Approve invoice and close claim']*

The claim is now **Completed**. All timestamps are recorded. The full audit trail — every AI decision, every human approval, every document — is stored on the claim."

---

## [SUMMARY — 8:55]

*[Screen returns to the dashboard — claim shows as Completed in green]*

"So what did we just see?

We processed a full auto insurance claim — from filing to closure — with four AI agents handling the most time-consuming parts:

1. **Coverage triage** — seconds, not hours of policy reading
2. **Work order generation** — from photos, not manual line-by-line entry
3. **Budget reconciliation** — automated fuzzy matching against the approved scope
4. **Invoice verification** — line-by-line discrepancy detection before payment

Every AI decision had a human approval gate. The customer had real-time visibility throughout. Every stage has a defined SLA. And the entire workflow is fully auditable.

This is ClearProcess. Thank you."

---

## [OUTRO — 9:20]

*[Title card: ClearProcess · Team CLAIMZ · Zurich Hyper Challenge 2026]*

*[Shows: github.com/fionachiaraviglio-png/Retail_Claims_CoPilot_CLAIMZ]*

---

*Total estimated runtime: ~9 minutes 30 seconds*
