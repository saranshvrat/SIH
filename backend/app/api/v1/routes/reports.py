"""
Report generation (master plan §7M).

JSON and a ZIP bundle are ALWAYS available because they need no extra dependency.
PDF is produced when WeasyPrint is installed and degrades gracefully when it is not —
the report is never silently skipped.
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app.api.v1.deps import current_session, db_session, owned_job
from app.api.v1.routes.results import get_results
from app.core.config import settings
from app.core.errors import ConflictError
from app.core.ids import report_id as new_report_id
from app.db.models import Artifact, Report
from app.services import keys
from app.services.storage import JOBS, REPORTS, get_storage

router = APIRouter(tags=["reports"])


class ReportCreate(BaseModel):
    format: str = "json"          # json | zip | pdf
    include_rasters: bool = True


def _pdf_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


@router.post("/jobs/{job_id}/report", status_code=201)
def create_report(payload: ReportCreate, job=Depends(owned_job),
                  db: DbSession = Depends(db_session)) -> dict[str, Any]:
    if job.status != "COMPLETED":
        raise ConflictError(f"This job is {job.status}; a report needs a completed job.")

    storage = get_storage()
    data = get_results(job=job, db=db)
    rid = new_report_id()
    tmp = Path(tempfile.mkdtemp(prefix="pf_rep_"))
    notes: list[str] = []

    report = Report(id=rid, job_id=job.id, status="running",
                    format=payload.format, options=payload.model_dump())
    db.add(report)
    db.commit()

    fmt = payload.format
    if fmt == "pdf" and not _pdf_available():
        fmt = "json"
        notes.append("PDF generation is unavailable in this deployment (WeasyPrint is not "
                     "installed). A JSON report was produced instead.")

    if fmt == "json":
        local = tmp / "report.json"
        local.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        key = keys.report_json(rid)
        bucket, media = REPORTS, "JSON"
    elif fmt == "zip":
        local = tmp / "bundle.zip"
        with zipfile.ZipFile(local, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.json", json.dumps(data, indent=2, default=str))
            z.writestr("README.txt", _bundle_readme(job.id))
            if payload.include_rasters:
                for art in db.query(Artifact).filter(Artifact.session_id == job.session_id).all():
                    if art.owner_id != job.id and art.owner_id not in [
                            r["run_id"] for r in data["runs"]]:
                        continue
                    try:
                        f = storage.get_to_path(art.bucket, art.storage_key,
                                                tmp / "files" / Path(art.storage_key).name)
                        z.write(f, f"files/{art.kind}_{Path(art.storage_key).name}")
                    except Exception:
                        notes.append(f"File {art.kind} could not be included.")
        key = keys.bundle_zip(job.id)
        bucket, media = JOBS, "ZIP"
    else:
        local = tmp / "report.pdf"
        _render_pdf(data, local)
        key = keys.report_pdf(rid)
        bucket, media = REPORTS, "PDF"

    size = storage.put_file(bucket, key, local)
    art = Artifact(session_id=job.session_id, owner_type="report", owner_id=rid,
                   kind="report", format=media, bucket=bucket, storage_key=key,
                   bytes=size, checksum_sha256=storage.checksum(local),
                   expires_at=job.expires_at)
    db.add(art)
    report.status = "completed"
    report.format = fmt
    report.artifact_id_ref = art.id
    db.add(report)
    db.commit()

    return {"report_id": rid, "format": fmt, "status": "completed", "bytes": size,
            "artifact_id": art.id,
            "download_url": f"{settings.API_V1_PREFIX}/artifacts/{art.id}/download",
            "notes": notes}


def _bundle_readme(job_id: str) -> str:
    return (
        f"PixelForge export — job {job_id}\n"
        f"{'=' * 60}\n\n"
        "report.json   Full result record: models, metrics, provenance, analyses.\n"
        "files/        GeoTIFF (COG) rasters and web previews.\n\n"
        "IMPORTANT\n"
        "Super-resolved rasters contain model-inferred detail that was NOT directly\n"
        "observed by the satellite. Do not use them for cadastral, legal, navigational\n"
        "or safety-critical measurement. The original 10 m observation is included so\n"
        "you can always compare against what the sensor actually recorded.\n\n"
        "Each GeoTIFF carries its provenance in its metadata tags; inspect with:\n"
        "    gdalinfo <file>.tif\n"
    )


def _render_pdf(data: dict, out: Path) -> None:
    from weasyprint import HTML
    runs = "".join(
        f"<tr><td>{r['model_display_name']}</td><td>{r['output']['gsd_m']} m</td>"
        f"<td>{r['performance']['runtime_s']} s</td>"
        f"<td>{'baseline' if r['is_baseline'] else r['status']}</td></tr>"
        for r in data["runs"])
    unavailable = data["runs"][0]["metrics"].get("full_reference_unavailable_reason") if data["runs"] else None
    html = f"""<html><head><style>
      body {{ font-family: -apple-system, system-ui, sans-serif; margin: 36px; color:#111; }}
      h1 {{ font-size: 22px; }} h2 {{ font-size: 15px; margin-top: 24px; }}
      table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
      td, th {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
      .warn {{ background:#fff7ed; border-left:3px solid #ea580c; padding:10px; font-size:12px; }}
    </style></head><body>
      <h1>PixelForge analysis report</h1>
      <p style="font-size:12px;color:#555">Job {data['job_id']} · mode {data['mode']}</p>
      <h2>Model runs</h2>
      <table><tr><th>Model</th><th>Output GSD</th><th>Runtime</th><th>Status</th></tr>{runs}</table>
      {f'<h2>Quality metrics</h2><div class="warn">{unavailable}</div>' if unavailable else ''}
      <h2>Provenance and limitations</h2>
      <div class="warn">{data['provenance']['disclaimer']}</div>
    </body></html>"""
    HTML(string=html).write_pdf(str(out))
