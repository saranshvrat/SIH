"""
The job orchestrator.

Deliberately a plain Python class, callable either inline (tests, eager mode, a
Celery-free deployment) or from a Celery task. The stage logic lives here exactly
once, so the code path exercised by tests is the code path that runs in production.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.ids import artifact_id as new_artifact_id
from app.core.ids import run_id as new_run_id
from app.core.logging import get_logger
from app.db.models import Artifact, Job, Metric, ModelRun, Scene, UncertaintyMap, utcnow
from app.domains.jobs_state import Stage, JobState, is_fatal
from app.ml.errors import MLError
from app.ml.registry import describe, get_spec, run_model
from app.services import keys
from app.services.geo import classify as classify_svc
from app.services.metrics import compute as metrics_svc
from app.services.preprocessing import pipeline as prep_svc
from app.services.raster import cog as cog_svc
from app.services.raster import preview as preview_svc
from app.services.storage import JOBS, SCENES, get_storage
from app.services.uncertainty import ensemble as unc_svc

log = get_logger(__name__)
WANTED_BANDS = ["B02", "B03", "B04", "B08"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobPipeline:
    def __init__(self, db: DbSession, job: Job, progress_sink: Callable | None = None):
        self.db = db
        self.job = job
        self.storage = get_storage()
        self.state = JobState.from_list(job.stages)
        self.progress_sink = progress_sink
        self.tmp = Path(tempfile.mkdtemp(prefix=f"pf_{job.id}_"))
        self.ctx: dict[str, Any] = {}

    # ---------------------------------------------------------------- plumbing
    def _persist(self) -> None:
        self.job.stages = self.state.to_list()
        self.job.progress = self.state.overall_progress()
        self.db.add(self.job)
        self.db.commit()
        if self.progress_sink:
            self.progress_sink(self.job.id, self.job.progress, self.job.current_stage)

    def _begin(self, stage: Stage) -> StageState:  # type: ignore[name-defined]
        st = self.state.get(stage)
        st.status, st.started_at, st.progress = "running", _iso(), 0.0
        self.job.current_stage = stage.value
        self.job.status = stage.value
        self._persist()
        return st

    def _end(self, stage: Stage, status: str = "done", reason: str | None = None,
             detail: str | None = None) -> None:
        st = self.state.get(stage)
        st.status, st.ended_at, st.reason, st.detail = status, _iso(), reason, detail
        st.progress = 1.0 if status in ("done", "skipped") else st.progress
        if st.started_at:
            st.duration_s = round(
                (datetime.fromisoformat(st.ended_at) - datetime.fromisoformat(st.started_at))
                .total_seconds(), 3)
        self._persist()

    def _skip(self, stage: Stage, reason: str) -> None:
        st = self.state.get(stage)
        st.status, st.reason, st.progress = "skipped", reason, 1.0
        self._persist()

    def _stage_progress(self, stage: Stage):
        def cb(p: float, detail: str = "") -> None:
            st = self.state.get(stage)
            st.progress, st.detail = max(0.0, min(1.0, p)), detail
            self.job.progress = self.state.overall_progress()
            self.db.add(self.job)
            self.db.commit()
            if self.progress_sink:
                self.progress_sink(self.job.id, self.job.progress, f"{stage.value}: {detail}")
        return cb

    def _artifact(self, owner_type: str, owner_id: str, kind: str, fmt: str,
                  bucket: str, key: str, local: Path, meta: dict | None = None) -> Artifact:
        size = self.storage.put_file(bucket, key, local)
        art = Artifact(id=new_artifact_id(), session_id=self.job.session_id,
                       owner_type=owner_type, owner_id=owner_id, kind=kind, format=fmt,
                       bucket=bucket, storage_key=key, bytes=size,
                       checksum_sha256=self.storage.checksum(local),
                       meta=meta or {}, expires_at=self.job.expires_at)
        if local.suffix.lower() in (".tif", ".tiff"):
            try:
                s = cog_svc.raster_summary(local)
                art.width, art.height, art.band_count = s["width"], s["height"], s["band_count"]
                art.crs, art.bounds_wgs84 = s["crs"], s["bounds_wgs84"]
            except Exception:
                pass
        self.db.add(art)
        self.db.commit()
        return art

    # ------------------------------------------------------------------- stages
    def run(self) -> Job:
        started = time.perf_counter()
        self.job.started_at = utcnow()
        try:
            self._end(Stage.QUEUED)
            self._download()
            self._preprocess()
            self._scene_analysis()
            self._model_selection()
            self._inference()
            self._quality()
            self._uncertainty()
            self._post_process()
            self._analysis()
            self.job.status = Stage.COMPLETED.value
            self.job.current_stage = Stage.COMPLETED.value
        except _FatalStageError as exc:
            self.job.status = Stage.FAILED.value
            self.job.failed_stage = exc.stage.value
            self.job.error_code = exc.code
            self.job.error_message = exc.message
            self._end(exc.stage, "failed", detail=exc.message)
            log.warning("job.failed", job_id=self.job.id, stage=exc.stage.value, code=exc.code)
        except Exception as exc:  # noqa: BLE001
            self.job.status = Stage.FAILED.value
            self.job.error_code = "INTERNAL_ERROR"
            self.job.error_message = str(exc)
            log.exception("job.crashed", job_id=self.job.id)
        finally:
            self.job.finished_at = utcnow()
            self.job.duration_s = round(time.perf_counter() - started, 3)
            self.job.progress = 1.0 if self.job.status == Stage.COMPLETED.value else self.job.progress
            self.db.add(self.job)
            self.db.commit()
            shutil.rmtree(self.tmp, ignore_errors=True)
        return self.job

    def _download(self) -> None:
        scene = self.db.get(Scene, self.job.scene_id)
        if scene.source == "upload":
            self._skip(Stage.DOWNLOADING, "UPLOADED_SOURCE")
            local = self.tmp / "source.tif"
            self.storage.get_to_path(SCENES, scene.storage_key, local)
            self.ctx["source_path"] = local
            self.ctx["scene"] = scene
            return
        self._begin(Stage.DOWNLOADING)
        try:
            from app.services.catalog.planetary_computer import download_aoi_window
            aoi_geom = self.ctx.get("aoi_geojson") or _aoi_geom(self.db, self.job)
            local = download_aoi_window(scene, aoi_geom, WANTED_BANDS, self.tmp,
                                        progress=self._stage_progress(Stage.DOWNLOADING))
            self.ctx["source_path"] = local
            self.ctx["scene"] = scene
            self._end(Stage.DOWNLOADING)
        except Exception as exc:  # noqa: BLE001
            raise _FatalStageError(Stage.DOWNLOADING, "SOURCE_UNAVAILABLE", str(exc)) from exc

    def _preprocess(self) -> None:
        self._begin(Stage.PREPROCESSING)
        scene = self.ctx["scene"]
        try:
            model_keys = self.job.requested_models or [settings.ML_DEFAULT_MODEL]
            max_px = min((get_spec(k).max_input_px for k in model_keys
                          if _spec_ok(k)), default=settings.MAX_INPUT_PX)
            result = prep_svc.run(
                source_path=self.ctx["source_path"], out_dir=self.tmp / "pp",
                band_mapping=scene.band_mapping or {}, wanted_bands=WANTED_BANDS,
                aoi_geojson=_aoi_geom(self.db, self.job),
                cloud_mask=bool(self.job.options.get("cloud_mask", True)),
                max_input_px=max_px, progress=self._stage_progress(Stage.PREPROCESSING))
        except Exception as exc:  # noqa: BLE001
            raise _FatalStageError(Stage.PREPROCESSING, "PREPROCESSING_FAILED", str(exc)) from exc

        self.ctx["prep"] = result
        self.job.cloud_fraction_aoi = result.cloud_fraction
        self._artifact("job", self.job.id, "lr_tif", "GTiff", JOBS,
                       keys.lr_tif(self.job.id), result.lr_path, {"band_order": result.band_order})
        self.storage.put_json(JOBS, keys.integrity_json(self.job.id), result.integrity)

        stretch = preview_svc.compute_stretch(result.lr_path, [1, 2, 3, 4])
        self.ctx["stretch"] = stretch
        for combo in ("rgb_true", "rgb_nir"):
            p = self.tmp / f"lr_{combo}.webp"
            meta = preview_svc.render_preview(result.lr_path, p, result.band_order, combo, stretch)
            self._artifact("job", self.job.id, f"preview_lr_{combo}", "WEBP", JOBS,
                           keys.lr_preview(self.job.id, combo), p, meta)
        self._end(Stage.PREPROCESSING, detail=" · ".join(result.warnings) or None)

    def _scene_analysis(self) -> None:
        self._begin(Stage.SCENE_ANALYSIS)
        try:
            res = classify_svc.classify(self.ctx["prep"].lr_path, self.ctx["prep"].band_order)
            if res.get("terrain_class") is None:
                self._skip(Stage.SCENE_ANALYSIS, res.get("reason", "CLASSIFICATION_FAILED"))
                return
            self.job.terrain_class = res["terrain_class"]
            self.job.terrain_distribution = res["distribution"]
            self.job.classification_method = res["method"]
            self.job.classification_confidence = res["confidence"]
            self.ctx["classification"] = res
            self._end(Stage.SCENE_ANALYSIS, detail=res["explanation"])
        except Exception as exc:  # noqa: BLE001  — MUST NOT fail the job
            log.warning("classification.failed", job_id=self.job.id, error=str(exc))
            self._skip(Stage.SCENE_ANALYSIS, "CLASSIFICATION_FAILED")

    def _model_selection(self) -> None:
        self._begin(Stage.MODEL_SELECTION)
        available = [m["key"] for m in (describe(k) for k in _catalog_keys()) if m["status"] == "available"]
        if not self.job.auto_select_model and self.job.requested_models:
            chosen = [m for m in self.job.requested_models if m in available]
            unavailable = [m for m in self.job.requested_models if m not in available]
            if not chosen:
                raise _FatalStageError(
                    Stage.INFERENCE, "NO_MODEL_AVAILABLE",
                    f"None of the requested models are available: {', '.join(unavailable) or 'none'}.")
            self.job.selected_models = chosen
            self.ctx["routing"] = {"selected": chosen, "reason": "USER_SELECTED_MODELS",
                                   "unavailable_requested": unavailable}
            self._skip(Stage.MODEL_SELECTION, "USER_SELECTED_MODELS")
            return
        routing = classify_svc.route(self.job.terrain_class, available, settings.ML_DEFAULT_MODEL)
        if not routing["selected"]:
            raise _FatalStageError(Stage.INFERENCE, "NO_MODEL_AVAILABLE", routing["explanation"])
        self.job.selected_models = routing["selected"]
        self.ctx["routing"] = routing
        self._end(Stage.MODEL_SELECTION, detail=routing["explanation"])

    def _inference(self) -> None:
        self._begin(Stage.INFERENCE)
        prep = self.ctx["prep"]
        models = self.job.selected_models or [settings.ML_DEFAULT_MODEL]
        succeeded, failed = [], []

        for i, key in enumerate(models):
            spec = get_spec(key)
            run = ModelRun(id=new_run_id(), job_id=self.job.id, model_key=key,
                           status="running", model_version=getattr(spec, "version", None),
                           ml_commit=settings.ML_COMMIT, is_baseline=spec.is_baseline,
                           input_key=keys.lr_tif(self.job.id), seed=42)
            self.db.add(run)
            self.db.commit()

            def sub(p: float, detail: str, i=i, key=key) -> None:
                self._stage_progress(Stage.INFERENCE)((i + p) / len(models), f"{key} · {detail}")

            try:
                out_dir = self.tmp / "runs" / run.id
                out_dir.mkdir(parents=True, exist_ok=True)
                result, integrity = run_model(key, prep.lr_path, out_dir / "sr.tif",
                                              {"run_id": run.id, "seed": 42, "progress_cb": sub})
                if not result.ok:
                    raise MLError(result.error_message or "inference failed",
                                  {"code": result.error_code})

                cog_path = out_dir / "sr_cog.tif"
                cog_svc.to_cog(result.sr_path, cog_path, band_names=result.output_bands,
                               tags={"RUN_ID": run.id, "JOB_ID": self.job.id})
                self._artifact("run", run.id, "sr_cog", "COG", JOBS,
                               keys.sr_cog(self.job.id, run.id), cog_path,
                               {"band_order": result.output_bands})

                for combo in ("rgb_true", "rgb_nir"):
                    p = out_dir / f"{combo}.webp"
                    meta = preview_svc.render_preview(cog_path, p, result.output_bands, combo,
                                                      self.ctx.get("stretch"))
                    self._artifact("run", run.id, f"preview_{combo}", "WEBP", JOBS,
                                   keys.run_preview(self.job.id, run.id, combo), p, meta)

                summary = cog_svc.raster_summary(cog_path)
                run.status = "completed"
                run.output_gsd_m = result.output_gsd_m
                run.scale_factor = result.scale_factor
                run.output_bands = result.output_bands
                run.output_crs = summary["crs"]
                run.output_bounds_wgs84 = summary["bounds_wgs84"]
                run.output_width, run.output_height = summary["width"], summary["height"]
                run.output_dtype = summary["dtype"]
                run.geospatial_integrity = integrity
                run.runtime_s = result.runtime_s
                run.peak_vram_mb = result.peak_vram_mb
                run.tiles_processed = result.tiles_processed
                run.device_name = result.device_name
                run.warnings = result.warnings
                self.db.add(run)
                self.db.commit()
                self.ctx.setdefault("run_paths", {})[run.id] = cog_path
                self.ctx.setdefault("run_models", {})[run.id] = key
                succeeded.append(run.id)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "details", {}).get("code") or getattr(exc, "code", "INFERENCE_FAILED")
                run.status = "failed"
                run.error_code = code
                run.error_message = str(exc)
                self.db.add(run)
                self.db.commit()
                failed.append((key, code, str(exc)))
                log.warning("run.failed", job_id=self.job.id, model=key, code=code)

        if not succeeded:
            first = failed[0] if failed else ("", "INFERENCE_FAILED", "no models ran")
            raise _FatalStageError(Stage.INFERENCE, first[1], first[2])

        self.job.partial_success = bool(failed)
        detail = f"{len(succeeded)} of {len(models)} model(s) completed"
        self._end(Stage.INFERENCE, detail=detail)

    def _quality(self) -> None:
        if not self.job.options.get("compute_metrics", True):
            self._skip(Stage.QUALITY_ASSESSMENT, "METRICS_DISABLED")
            return
        self._begin(Stage.QUALITY_ASSESSMENT)
        try:
            lr = self.ctx["prep"].lr_path
            ref_path = self.ctx.get("reference_path")
            ref_type = self.job.options.get("reference", {}).get("type") if self.job.options.get("reference") else None
            for run_id, sr_path in self.ctx.get("run_paths", {}).items():
                nr = metrics_svc.compute_no_reference(lr, sr_path)
                for name, m in nr.items():
                    self._metric(run_id, name, m, reference_type="lr_self")
                if ref_path:
                    fr = metrics_svc.compute_full_reference(sr_path, ref_path)
                    for name, m in fr.items():
                        if name in ("per_band", "implementation"):
                            continue
                        self._metric(run_id, name, m, reference_type=ref_type or "user_supplied")
            self._end(Stage.QUALITY_ASSESSMENT,
                      reason=None if ref_path else "NO_REFERENCE")
        except Exception as exc:  # noqa: BLE001 — must not fail the job
            log.warning("metrics.failed", job_id=self.job.id, error=str(exc))
            self._end(Stage.QUALITY_ASSESSMENT, "done", detail=f"partial: {exc}")

    def _metric(self, run_id: str, name: str, m: dict, reference_type: str) -> None:
        val = m.get("value")
        if val is not None and isinstance(val, float) and val == float("inf"):
            val = None
        self.db.merge(Metric(run_id=run_id, name=name, value=val, unit=m.get("unit"),
                             scope="overall", band=None, tile_index=None,
                             reference_type=reference_type,
                             implementation=metrics_svc.IMPLEMENTATION))
        self.db.commit()

    def _uncertainty(self) -> None:
        if not self.job.options.get("compute_uncertainty", True):
            self._skip(Stage.UNCERTAINTY, "NOT_REQUESTED")
            return
        run_paths = self.ctx.get("run_paths", {})
        if len(run_paths) < 2:
            # 🚨 Honest skip. We do NOT invent a map from a single output.
            self._skip(Stage.UNCERTAINTY, "NEEDS_MULTIPLE_RUNS")
            return
        self._begin(Stage.UNCERTAINTY)
        try:
            baselines = {rid for rid, k in self.ctx.get("run_models", {}).items()
                         if get_spec(k).is_baseline}
            out = self.tmp / "uncertainty.tif"
            res = unc_svc.compute_ensemble_uncertainty(run_paths, out, baseline_keys=baselines)
            if not res:
                self._skip(Stage.UNCERTAINTY, "NEEDS_MULTIPLE_RUNS")
                return
            self._artifact("job", self.job.id, "uncertainty_cog", "COG", JOBS,
                           keys.ensemble_uncertainty(self.job.id), out,
                           {"method": res["method"], "calibrated": False})
            png = self.tmp / "uncertainty.png"
            pmeta = preview_svc.render_colormap(out, png, vmin=res["range"]["min"],
                                                vmax=res["range"]["p95"], colormap="magma")
            self._artifact("job", self.job.id, "uncertainty_preview", "PNG", JOBS,
                           keys.ensemble_uncertainty_preview(self.job.id), png,
                           {**pmeta, "legend": [
                               {"value": res["range"]["min"], "label": "Models agree"},
                               {"value": res["range"]["p95"], "label": "Models disagree strongly"}]})
            self.db.add(UncertaintyMap(
                job_id=self.job.id, method=res["method"], calibrated=False,
                statistic=res["statistic"], unit=res["unit"], stats=res["range"],
                confidence_score=res["confidence_score"],
                confidence_definition=res["confidence_definition"],
                source_run_ids=res["source_run_ids"],
                high_uncertainty_regions=res["high_uncertainty_regions"]))
            self.db.commit()
            self._end(Stage.UNCERTAINTY, detail=res["interpretation_note"])
        except Exception as exc:  # noqa: BLE001 — must not fail the job
            log.warning("uncertainty.failed", job_id=self.job.id, error=str(exc))
            self._skip(Stage.UNCERTAINTY, "CLASSIFICATION_FAILED")

    def _post_process(self) -> None:
        if not self.job.options.get("post_process", False):
            self._skip(Stage.POST_PROCESSING, "NOT_REQUESTED")
            return
        self._begin(Stage.POST_PROCESSING)
        self._end(Stage.POST_PROCESSING, detail="Outputs written as COGs with provenance tags.")

    def _analysis(self) -> None:
        requested = self.job.options.get("analyses") or []
        if not requested:
            self._skip(Stage.ANALYSIS, "NO_ANALYSES_REQUESTED")
            return
        self._begin(Stage.ANALYSIS)
        from app.domains.analysis import run_analysis
        done = []
        for run_id in self.ctx.get("run_paths", {}):
            for kind in requested:
                try:
                    run_analysis(self.db, self.job, run_id, kind,
                                 sr_path=self.ctx["run_paths"][run_id],
                                 lr_path=self.ctx["prep"].lr_path,
                                 band_names=self.ctx["prep"].band_order, tmp=self.tmp)
                    done.append(f"{kind}:{run_id}")
                except Exception as exc:  # noqa: BLE001
                    log.warning("analysis.failed", kind=kind, error=str(exc))
        self._end(Stage.ANALYSIS, detail=f"{len(done)} analysis result(s)")


class _FatalStageError(Exception):
    def __init__(self, stage: Stage, code: str, message: str):
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


def _aoi_geom(db: DbSession, job: Job) -> dict | None:
    from app.db.models import Aoi
    if not job.aoi_id:
        return None
    aoi = db.get(Aoi, job.aoi_id)
    return aoi.geometry_geojson if aoi else None


def _catalog_keys() -> list[str]:
    from app.ml.registry import load_catalog
    return list(load_catalog())


def _spec_ok(key: str) -> bool:
    try:
        get_spec(key)
        return True
    except Exception:
        return False
