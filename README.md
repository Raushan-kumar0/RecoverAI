# RecoverAI — Autonomous Revenue Recovery Agent

RecoverAI is an AI-powered revenue recovery system that detects failed or at-risk payments, diagnoses why they failed, recommends a recovery action, applies deterministic safety guardrails, executes approved actions, verifies actual payment recovery, and measures the outcome.

## 🚀 Pipeline

```text
DETECT
   ↓
DIAGNOSE
   ↓
DECIDE
   ↓
GUARDRAIL
   ↓
EXECUTE
   ↓
RECOVER
   ↓
MEASURE
```

The system is designed to make recovery decisions autonomously while keeping financial actions bounded, auditable, and safe.

## ✨ Key Features

- **ML-based payment diagnosis** using scikit-learn
- **Recovery decision engine** for selecting the next best action
- **Deterministic guardrails** for retry limits, monetary limits, and human approval
- **Razorpay Test Mode REST API integration**
- **Payment Link creation and independent payment-status verification**
- **Recovery verification** based on confirmed payment status
- **Mandate retry sequencer** with controlled retry attempts
- **Payment routing optimizer** for selecting the next payment route
- **Customer recovery script generator** with Hinglish support
- **Append-only audit trail**
- **Failure handling and fallback workflows**
- **Promise tracking** for customer payment commitments
- **Streamlit dashboard** for end-to-end visibility
- **Automated test suite**

## 🧠 ML Diagnosis

RecoverAI uses a Logistic Regression classifier to estimate the likelihood that a failed payment can be recovered.

The diagnosis layer combines the model prediction with rule-based explanations so that recovery decisions remain understandable and auditable.

## 🛡️ Safety First

Financial actions are protected by deterministic guardrails.

The system can enforce:

- Maximum retry limits
- Monetary ceilings
- Human-approval requirements
- Dry-run execution
- Live-mode credential rejection
- API failure handling
- Audit logging

The system does not treat an attempted action as a recovered payment.

A recovery is counted only after payment status is independently verified.

## 💳 Razorpay Integration

RecoverAI integrates with Razorpay Test Mode through REST APIs.

Test Mode is used so that the project can demonstrate the complete execution and verification workflow without moving real money.

The project does not claim Test Mode transactions as real business revenue.

## 📊 Dashboard

The Streamlit dashboard provides visibility into the complete recovery lifecycle.

### Dashboard Sections

- **Overview** — Overall results and verified recoveries
- **Diagnosis** — Failure reasons and recovery likelihood
- **Decision + Guardrail** — Recommended action and approval result
- **Execution** — Actions actually executed or simulated
- **Recovery** — Independently verified payment results
- **Measurement** — Model, execution, and recovery metrics
- **Audit Trail** — Complete case history
- **Promises** — Customer payment commitments

## 🗂️ Project Structure

```text
RecoverAI/
├── actions/
├── audit/
├── dashboard/
├── data/
├── decision_engine/
├── diagnosis/
├── evaluation/
├── failure_handling/
├── guardrails/
├── hinglish_recovery/
├── integrations/
│   └── razorpay/
├── mandate_sequencer/
├── measurement/
├── promise_tracker/
├── recovery/
├── routing_optimizer/
├── .streamlit/
├── requirements.txt
└── run_real_demo.py
```

## ⚙️ Tech Stack

- Python
- scikit-learn
- SQLite
- Razorpay REST API
- Streamlit

## ▶️ Running Locally

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate the virtual environment

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the dashboard

```bash
streamlit run dashboard/app.py
```

## 🔐 Configuration

Razorpay credentials should be provided through environment variables.

**Do not commit real credentials to GitHub.**

The repository includes:

```text
integrations/razorpay/.env.example
```

as a configuration template.

RecoverAI is designed to reject live-mode credentials and use Razorpay Test Mode for demonstration.

## 🧪 Testing

The project contains automated tests across the major modules, including:

- Diagnosis
- Decision engine
- Guardrails
- Actions
- Razorpay integration
- Recovery verification
- Measurement
- Failure handling
- Audit trail
- Promise tracking
- Routing optimization
- Hinglish recovery

## 🔄 Recovery Verification

RecoverAI separates execution from actual recovery.

```text
Action Executed
       ≠
Payment Recovered
```

Creating a Payment Link or executing a recovery action does not automatically mean revenue was recovered.

A payment is considered recovered only when the recovery verification layer independently confirms the payment status.

This prevents the system from falsely reporting attempted actions as successful revenue recovery.

## 🧩 Automation Modules

### Mandate Retry Sequencer

Automatically manages controlled recovery attempts for eligible failed subscription and payment cases.

The sequencer supports bounded retries and fallback behavior.

### Payment Routing Optimizer

Suggests the next payment route based on the diagnosed failure reason.

A Payment Link can be executed through Razorpay Test Mode, while other routing options are represented as simulations.

### Promise Tracker

Tracks customer payment commitments and allows payment status to be rechecked against available payment evidence.

### Hinglish Recovery

Generates customer-facing Hinglish recovery messages or voice-script previews.

These scripts are content simulations and do not automatically send messages or make calls.

## 📋 Audit Trail

RecoverAI maintains an append-only audit trail of important agent activity.

The audit layer records information such as:

- Case information
- Diagnosis
- Recommended action
- Guardrail decision
- Execution result
- Recovery verification
- Failure handling
- Measurement information

This makes the recovery process easier to inspect and debug.

## 🛡️ Failure Handling

The system includes explicit failure handling for execution and API problems.

Possible execution outcomes include:

- Executed
- Dry run
- Simulated
- API error
- Error
- Not executed

This keeps failed or simulated actions separate from successful execution.

## ⚠️ Important Note

This project uses **Razorpay Test Mode** for safe demonstration.

Test Mode transactions do not represent real customer revenue.

RecoverAI does not claim revenue recovery merely because an action was executed.

The system follows:

```text
Detect
  ↓
Diagnose
  ↓
Decide
  ↓
Guardrail
  ↓
Execute
  ↓
Verify
  ↓
Measure
```

Only independently verified payment results are treated as confirmed recoveries.

## 🎯 Project Goal

RecoverAI demonstrates how an autonomous revenue recovery agent can combine machine learning, deterministic financial guardrails, payment gateway APIs, payment verification, failure handling, and observability into one controlled workflow.

The goal is to recover more revenue while keeping financial actions bounded, explainable, and auditable.

---

**RecoverAI — Detect. Diagnose. Decide. Recover. Measure.**
