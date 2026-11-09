"""FastAPI server for PDF content stream editing."""
import logging
import os
import time
import uuid
import shutil
import json
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pdf_editor import edit_pdf
from verify import VerificationError

log = logging.getLogger("pdf-editor.server")

app = FastAPI(title="PDF Content Stream Editor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_REPLACEMENTS_BYTES = 1024 * 1024   # the JSON form field is unbounded otherwise
OUTPUT_TTL_SECONDS = 24 * 3600         # outputs/ is pruned to this age

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)


def _prune_outputs():
    """Delete stale edited_*.pdf files so outputs/ cannot grow unbounded."""
    try:
        now = time.time()
        for name in os.listdir(OUTPUT_DIR):
            if not name.startswith("edited_"):
                continue
            path = os.path.join(OUTPUT_DIR, name)
            try:
                if now - os.path.getmtime(path) > OUTPUT_TTL_SECONDS:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/edit")
async def edit_pdf_endpoint(
    request: Request,
    file: UploadFile = File(...),
    replacements: str = Form("{}"),
):
    """Upload PDF + JSON replacements -> edited PDF.

    Query params:
        verify=0     skip verification
        verify=1     verify, report in X-Verification header
        verify=json  verify, return the JSON report instead of the PDF
    """
    verify_mode = request.query_params.get("verify", "json")
    if verify_mode not in ("0", "1", "json"):
        raise HTTPException(400, "verify must be 0, 1, or json")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    try:
        reps = json.loads(replacements)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON in replacements")

    if not isinstance(reps, dict) or not reps:
        raise HTTPException(400, "No replacements provided")
    if len(replacements) > MAX_REPLACEMENTS_BYTES:
        raise HTTPException(413, "replacements field exceeds 1 MB limit")

    _prune_outputs()

    file_id = str(uuid.uuid4())
    pdf_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    size = 0
    with open(pdf_path, "wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                os.remove(pdf_path)
                raise HTTPException(413, "PDF exceeds 25 MB upload limit")
            f.write(chunk)

    try:
        out_id = uuid.uuid4().hex[:8]
        out_path = os.path.join(OUTPUT_DIR, f"edited_{out_id}.pdf")
        result = edit_pdf(
            pdf_path, out_path, reps, verify=(verify_mode != "0"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except VerificationError as e:
        if os.path.exists(out_path):
            os.remove(out_path)  # never serve output the harness rejected
        raise HTTPException(422, detail={
            "error": "verification failed",
            "report": e.report.to_dict(),
        })
    except Exception as e:
        if os.path.exists(out_path):
            os.remove(out_path)
        log.exception("edit failed for upload %s", file_id)
        # generic message: raw exception text leaks paths/library internals
        raise HTTPException(500, "Edit failed (internal error); "
                                 "see server logs for details")
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

    headers = {
        "X-Replacements-Made": str(result["replacements_made"]),
        "X-Verification": "skipped" if verify_mode == "0" else "pass",
        "X-Missing-Glyphs": str(len(result.get("missing_glyphs", []))),
    }
    if verify_mode == "json":
        return JSONResponse(result["verification"], headers=headers)
    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename="edited.pdf",
        headers=headers,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
