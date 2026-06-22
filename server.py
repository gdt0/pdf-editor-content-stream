"""FastAPI server for PDF content stream editing."""
import os
import uuid
import shutil
import json
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pdf_editor import edit_pdf

app = FastAPI(title="PDF Content Stream Editor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/edit")
async def edit_pdf_endpoint(
    file: UploadFile = File(...),
    replacements: str = Form("{}"),
):
    """Upload PDF + JSON replacements → edited PDF."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    try:
        reps = json.loads(replacements)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON in replacements")

    if not reps:
        raise HTTPException(400, "No replacements provided")

    file_id = str(uuid.uuid4())
    pdf_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        out_id = uuid.uuid4().hex[:8]
        out_path = os.path.join(OUTPUT_DIR, f"edited_{out_id}.pdf")
        result = edit_pdf(pdf_path, out_path, reps)
        return FileResponse(
            out_path,
            media_type="application/pdf",
            filename="edited.pdf",
            headers={"X-Replacements-Made": str(result["replacements_made"])},
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Edit failed: {str(e)}")
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
