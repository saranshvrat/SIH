"""
AI Analyst tool registry (master plan §7L).

🚨 THE CENTRAL RULE: the LLM never computes, estimates or recalls a number. Every
figure it states must come from a tool result, which comes from the database, which
came from a real computation. The LLM's only job is to turn retrieved facts into
readable prose.

Each tool is a plain Python function over the job's own data. They are also usable
directly from the API for the transparency panel.
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from app.db.models import Analysis, Job, ModelRun, Scene, UncertaintyMap
from app.ml.registry import describe
from app.services.metrics.compute import EXPLANATIONS

REGISTRY: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, params: dict | None = None):
    def deco(fn: Callable):
        REGISTRY[name] = {"name": name, "description": description,
                          "input_schema": {"type": "object",
                                           "properties": params or {}, "required": []},
                          "fn": fn}
        return fn
    return deco


# --------------------------------------------------------------------- tools
@tool("get_job_summary", "Overall status, mode, terrain classification and cloud cover.")
def get_job_summary(db: DbSession, job: Job, **_) -> dict:
    return {"job_id": job.id, "status": job.status, "mode": job.mode,
            "models_run": job.selected_models, "terrain_class": job.terrain_class,
            "terrain_confidence": job.classification_confidence,
            "terrain_method": job.classification_method,
            "terrain_distribution": job.terrain_distribution,
            "cloud_fraction_aoi": job.cloud_fraction_aoi,
            "partial_success": job.partial_success,
            "duration_s": job.duration_s}


@tool("list_model_runs", "Every model run with its output resolution and runtime.")
def list_model_runs(db: DbSession, job: Job, **_) -> dict:
    runs = db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
    return {"runs": [{"run_id": r.id, "model_key": r.model_key,
                      "display_name": describe(r.model_key)["display_name"],
                      "status": r.status, "is_baseline": r.is_baseline,
                      "output_gsd_m": r.output_gsd_m, "scale_factor": r.scale_factor,
                      "runtime_s": r.runtime_s, "device": r.device_name,
                      "error_code": r.error_code} for r in runs]}


@tool("get_metrics", "All computed metrics for a run, or every run when run_id is omitted.",
      {"run_id": {"type": "string"}})
def get_metrics(db: DbSession, job: Job, run_id: str | None = None, **_) -> dict:
    from app.api.v1.routes.results import _metrics_for
    runs = db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
    if run_id:
        runs = [r for r in runs if r.id == run_id]
    return {"metrics_by_run": {r.id: {"model_key": r.model_key, **_metrics_for(db, r.id)}
                               for r in runs}}


@tool("explain_metric", "Plain-language definition of a metric.",
      {"metric": {"type": "string"}})
def explain_metric(db: DbSession, job: Job, metric: str = "", **_) -> dict:
    key = (metric or "").lower().strip()
    return {"metric": key, "explanation": EXPLANATIONS.get(
        key, f"No definition is recorded for {metric!r}. Known metrics: "
             f"{', '.join(sorted(EXPLANATIONS))}.")}


@tool("compare_models", "Side-by-side comparison of every completed run in this job.")
def compare_models(db: DbSession, job: Job, **_) -> dict:
    from app.api.v1.routes.results import _metrics_for
    runs = [r for r in db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
            if r.status == "completed"]
    if len(runs) < 2:
        return {"comparable": False,
                "reason": "NEEDS_MULTIPLE_RUNS",
                "message": ("Only one model produced output for this job, so there is nothing "
                            "to compare. Re-run with at least two models selected."),
                "runs": [r.model_key for r in runs]}
    rows = []
    for r in runs:
        m = _metrics_for(db, r.id)
        rows.append({"run_id": r.id, "model_key": r.model_key,
                     "is_baseline": r.is_baseline, "output_gsd_m": r.output_gsd_m,
                     "runtime_s": r.runtime_s,
                     "no_reference": {k: v["value"] for k, v in m["no_reference"].items()},
                     "full_reference": ({k: v["value"] for k, v in m["full_reference"].items()
                                         if v} if m["full_reference"] else None)})
    return {"comparable": True, "runs": rows,
            "note": ("Where full-reference metrics are null there is no ground truth for this "
                     "area; compare using the no-reference metrics instead.")}


@tool("get_uncertainty", "Uncertainty map statistics, or the reason none exists.")
def get_uncertainty(db: DbSession, job: Job, **_) -> dict:
    u = db.query(UncertaintyMap).filter(UncertaintyMap.job_id == job.id).first()
    if not u:
        stage = next((s for s in (job.stages or []) if s["name"] == "UNCERTAINTY"), {})
        return {"available": False, "reason": stage.get("reason", "NOT_COMPUTED"),
                "message": stage.get("reason_text",
                                     "No uncertainty estimate was produced for this job.")}
    return {"available": True, "method": u.method, "calibrated": u.calibrated,
            "statistic": u.statistic, "stats": u.stats,
            "confidence_score": u.confidence_score,
            "confidence_definition": u.confidence_definition,
            "high_uncertainty_region_count": len(u.high_uncertainty_regions),
            "largest_regions_km2": [r.get("area_km2") for r in u.high_uncertainty_regions[:3]],
            "caveat": ("This is a relative indicator of model disagreement, NOT a calibrated "
                       "probability. It has not been validated against ground truth.")}


@tool("get_analyses", "Results of any geospatial analyses run on this job.")
def get_analyses(db: DbSession, job: Job, **_) -> dict:
    rows = db.query(Analysis).filter(Analysis.job_id == job.id).all()
    if not rows:
        return {"analyses": [], "message": "No geospatial analysis has been run for this job yet."}
    return {"analyses": [{"analysis_id": a.id, "type": a.type,
                          "sr_stats": a.stats.get("stats"), "sr_classes": a.stats.get("classes"),
                          "lr_stats": a.lr_stats.get("stats"),
                          "comparison_with_lr": a.comparison_with_lr} for a in rows]}


@tool("get_scene_metadata", "Acquisition metadata for the source imagery.")
def get_scene_metadata(db: DbSession, job: Job, **_) -> dict:
    s = db.get(Scene, job.scene_id)
    if not s:
        return {"available": False}
    return {"available": True, "source": s.source, "provider": s.provider,
            "external_id": s.external_id, "product_type": s.product_type,
            "sensed_at": s.sensed_at.isoformat() if s.sensed_at else None,
            "scene_cloud_cover": s.cloud_cover, "crs": s.native_crs, "gsd_m": s.gsd_m,
            "band_mapping": s.band_mapping, "original_filename": s.original_filename}


@tool("get_geospatial_integrity", "Verification that CRS, bounds and scale survived inference.")
def get_geospatial_integrity(db: DbSession, job: Job, **_) -> dict:
    runs = db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
    return {"by_run": {r.id: r.geospatial_integrity for r in runs if r.geospatial_integrity}}


@tool("list_available_models", "Every model in the catalogue and whether it can run.")
def list_available_models(db: DbSession, job: Job, **_) -> dict:
    from app.ml.registry import list_models
    return {"models": [{"key": m["key"], "display_name": m["display_name"],
                        "status": m["status"], "unavailable_reason": m["unavailable_reason"],
                        "is_baseline": m["is_baseline"]} for m in list_models()]}


@tool("run_analysis", "Run a geospatial index analysis (ndvi, ndwi, ndbi) on this job.",
      {"type": {"type": "string", "enum": ["ndvi", "ndwi", "ndbi"]}})
def run_analysis_tool(db: DbSession, job: Job, type: str = "ndvi", **_) -> dict:
    from app.api.v1.routes.analysis import AnalysisCreate, create_analysis
    try:
        return create_analysis(AnalysisCreate(type=type), job=job, db=db)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@tool("get_provenance", "Model versions, commit and the output disclaimer.")
def get_provenance(db: DbSession, job: Job, **_) -> dict:
    from app.core.config import settings
    runs = db.query(ModelRun).filter(ModelRun.job_id == job.id).all()
    return {"ml_commit": settings.ML_COMMIT, "contract_version": "0.1",
            "runs": [{"run_id": r.id, "model_key": r.model_key,
                      "model_version": r.model_version, "seed": r.seed} for r in runs],
            "disclaimer": ("Super-resolved imagery contains model-inferred detail not directly "
                           "observed by the satellite.")}


def call_tool(name: str, db: DbSession, job: Job, **kwargs) -> dict:
    spec = REGISTRY.get(name)
    if not spec:
        return {"error": f"unknown tool {name!r}", "available": sorted(REGISTRY)}
    return spec["fn"](db, job, **kwargs)


def anthropic_schema() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["input_schema"]} for t in REGISTRY.values()]
