# GST Purchase Matcher — GSTR-2A/2B ⇄ Purchase Register Reconciliation

A simple, local **Excel comparator built for Indian GST reconciliation**. Upload
the invoices your vendors uploaded to the GST portal (the **GSTR-2A / 2B** export)
and your own **purchase register**, and the tool matches them invoice-by-invoice
and **clearly shows every mismatch** — wrong tax amounts, wrong taxable value,
date differences, GSTIN typos, bills missing on either side, and **Input Tax
Credit (ITC) at risk**.

- **Backend:** Python + FastAPI (Excel parsing + reconciliation engine)
- **Frontend:** Next.js (React) + TypeScript
- Runs **entirely on your machine** — your files never leave it.

---

## What it detects

| Category | Meaning | Why it matters |
| --- | --- | --- |
| ✅ **Reconciled** | Same invoice on both sides, all values agree (within a ₹ tolerance) | Nothing to do |
| ⚠️ **Mismatch** | Same invoice, but taxable value / IGST / CGST / SGST / date / GSTIN differ | Fix the entry or raise with vendor |
| 🔵 **Only in portal** | Vendor uploaded it, but it's not in your books | Unclaimed ITC — record the bill |
| 🔴 **ITC at risk** | In your books, but the vendor has **not** uploaded it | Your ITC may be reversed — follow up with the supplier |

It auto-maps columns, so the two files **don't need matching headers**. The
portal file can say `Integrated Tax(₹)` and your register can say `IGST` — both
are understood. Matching is done on **GSTIN + invoice number** with a fuzzy
fallback that catches `INV-001` vs `INV001` and one-character GSTIN typos. It
also flags **double-entered bills** as duplicates and keeps **credit notes**
(negative amounts) from masking your real ITC exposure.

---

## Quick start

You need **Python 3.10+** and **Node 18+**.

### 1. Backend (port 8011)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_samples.py   # creates the two sample Excel files
uvicorn app.main:app --reload --port 8011
```

The API is now at <http://localhost:8011> (health check: `/api/health`).

> Port 8011 is used because 8000 was already taken on this machine. To use a
> different port, change `--port` above **and** `NEXT_PUBLIC_API_BASE` in
> `frontend/.env.local`.

**CORS (production):** the backend allows any origin by default (handy locally).
In production, restrict it to your frontend via the `CORS_ALLOW_ORIGINS` env var
(comma-separated, no trailing slash), e.g.:

```bash
CORS_ALLOW_ORIGINS="https://main.d1234abcd.amplifyapp.com,https://gst.example.com" \
  uvicorn app.main:app --host 0.0.0.0 --port 8011
```

For wildcard hosts (e.g. Amplify branch URLs) use `CORS_ALLOW_ORIGIN_REGEX`,
such as `https://.*\.amplifyapp\.com`. See `backend/.env.example`.

### 2. Frontend (port 3000)

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>.

### 3. Try it

Click **“↧ Load sample files”** then **Reconcile** — or drop in your own two
`.xlsx` files. Click any mismatch row to see a **side-by-side comparison** of the
portal vs. your books, and use **Export full report (CSV)** to download
everything.

### One-command start (optional)

From the project root, `./start.sh` boots both servers together (it sets up the
venv and installs deps on first run).

---

## Using your real files

- **GST portal file:** download the **GSTR-2B** (or 2A) Excel from the GST
  portal. The tool reads **every recognised sheet** — B2B, plus CDNR (credit/
  debit notes) and B2BA (amendments) — skips the title rows automatically, and
  forward-fills merged GSTIN/supplier cells.
- **Purchase register:** any `.xlsx` with one row per invoice. Recognised column
  names (case-insensitive, any order) include:
  - GSTIN — `GSTIN of supplier`, `Supplier GSTIN`, `GSTIN` …
  - Invoice no. — `Invoice number`, `Bill No`, `Invoice No.` …
  - Date — `Invoice Date`, `Bill Date`, `Date` …
  - Taxable — `Taxable Value`, `Taxable Amount`, `Assessable Value` …
  - Taxes — `Integrated Tax`/`IGST`, `Central Tax`/`CGST`, `State/UT Tax`/`SGST`, `Cess`
  - Total — `Invoice Value`, `Total Invoice Value` (optional; derived if absent)

Only `.xlsx` / `.xlsm` are supported. If you have a `.xls` or CSV, open it in
Excel/LibreOffice and **Save As → .xlsx**.

The **amount tolerance** (default ₹1) absorbs rounding differences between the
portal and your books — increase it if your data rounds to the rupee.

---

## Project structure

```
gst/
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI app + endpoints
│   │   ├── parser.py      # Excel reading + column auto-mapping
│   │   └── reconcile.py   # the matching / reconciliation engine
│   ├── scripts/generate_samples.py
│   ├── sample_data/       # generated sample .xlsx files
│   └── requirements.txt
├── frontend/
│   ├── app/               # Next.js App Router (layout, page, styles, icon)
│   ├── components/        # FileDrop, SummaryCards, ResultsTable
│   └── lib/               # api client, types, formatting
└── start.sh               # boots both servers
```

## Deploying

The frontend and backend are **two separate services** — deploy them apart.

### Frontend → AWS Amplify Hosting
This repo ships an [`amplify.yml`](./amplify.yml) (monorepo build spec, app root
`frontend/`) and a `frontend/.nvmrc` (Node 20). In the Amplify console:

1. Connect this GitHub repo; Amplify reads `amplify.yml` automatically.
2. Under **Hosting → Environment variables**, set
   `NEXT_PUBLIC_API_BASE = https://<your-backend-url>` (baked in at build time).
3. Deploy. Amplify builds the Next.js app and serves it over HTTPS.

### Backend → anywhere that runs Python (not Amplify)
Amplify Hosting cannot run FastAPI. Host `backend/` on **AWS App Runner**, EC2,
Render, Fly.io, etc. — it must be reachable over **HTTPS**. Then:

- Point the frontend's `NEXT_PUBLIC_API_BASE` at it.
- Lock down CORS with `CORS_ALLOW_ORIGINS` (see above / `backend/.env.example`).
- Note: file uploads are up to 15 MB, so avoid Lambda + API Gateway (≤10 MB body).

## API

`POST /api/reconcile` — multipart form: `portal_file`, `purchase_file`,
`tolerance` (optional). Returns JSON: `summary`, `matched`, `mismatched`,
`only_in_portal`, `only_in_purchase`, `meta` (including which columns were
detected in each file).

---

*This tool assists with reconciliation; always review results before filing.
It does not give tax advice.*
