"""
FastAPI backend for the GST purchase reconciliation tool.

Endpoints
---------
GET  /api/health              - liveness probe
POST /api/reconcile           - upload portal + purchase .xlsx, get the report
GET  /api/sample/{which}      - download a ready-made sample file to try the tool
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .parser import parse_workbook
from .reconcile import DEFAULT_TOLERANCE, reconcile

app = FastAPI(title="GST Reconciliation API", version="1.0.0")

# Dev CORS: the Next.js app runs on a different port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
MAX_BYTES = 15 * 1024 * 1024  # 15 MB per file


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _read_xlsx(upload: UploadFile, label: str) -> bytes:
    name = (upload.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail=f"The {label} file must be an Excel .xlsx file (got "
                   f"'{upload.filename}'). If it is .xls, re-save it as .xlsx.",
        )
    content = await upload.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=400,
                            detail=f"The {label} file is too large (max 15 MB).")
    if not content:
        raise HTTPException(status_code=400,
                            detail=f"The {label} file is empty.")
    return content


@app.post("/api/reconcile")
async def reconcile_endpoint(
    portal_file: UploadFile = File(..., description="GST portal export (GSTR-2A/2B)"),
    purchase_file: UploadFile = File(..., description="Your purchase register"),
    tolerance: float = Form(DEFAULT_TOLERANCE),
):
    portal_bytes = await _read_xlsx(portal_file, "GST portal")
    purchase_bytes = await _read_xlsx(purchase_file, "purchase register")

    try:
        portal_parsed = parse_workbook(portal_bytes, "portal")
        purchase_parsed = parse_workbook(purchase_bytes, "purchase")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = reconcile(
        portal_parsed["records"],
        purchase_parsed["records"],
        tolerance=max(0.0, float(tolerance)),
    )

    result["meta"] = {
        "portal_file": portal_file.filename,
        "purchase_file": purchase_file.filename,
        "tolerance": tolerance,
        "warnings": portal_parsed.get("warnings", []) + purchase_parsed.get("warnings", []),
        "portal": {
            "sheet": portal_parsed["sheet"],
            "rows": portal_parsed["row_count"],
            "detected_columns": portal_parsed["detected_columns"],
        },
        "purchase": {
            "sheet": purchase_parsed["sheet"],
            "rows": purchase_parsed["row_count"],
            "detected_columns": purchase_parsed["detected_columns"],
        },
    }
    return result


@app.get("/api/sample/{which}")
def sample(which: str):
    files = {
        "portal": "sample_gst_portal.xlsx",
        "purchase": "sample_purchase_register.xlsx",
    }
    if which not in files:
        raise HTTPException(status_code=404, detail="Unknown sample.")
    path = SAMPLE_DIR / files[which]
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Sample files not generated yet. Run "
                   "`python -m scripts.generate_samples` in the backend folder.",
        )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=files[which],
    )
