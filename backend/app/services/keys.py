"""
Storage key builders (master plan §9.2).

🔒 SECURITY INVARIANT: every storage key in PixelForge is produced HERE, from
validated IDs only. User input never contributes to a path. This makes path
traversal structurally impossible rather than defended-against.
"""
from __future__ import annotations

import hashlib
import re

_SAFE_ID = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_SAFE_SUFFIX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _id(value: str, what: str) -> str:
    if not _SAFE_ID.match(value or ""):
        raise ValueError(f"unsafe {what} for storage key: {value!r}")
    return value


def _suffix(value: str) -> str:
    if not _SAFE_SUFFIX.match(value or "") or ".." in value:
        raise ValueError(f"unsafe filename component: {value!r}")
    return value


def aoi_hash(geojson: dict, bands: list[str]) -> str:
    payload = repr(sorted(bands)) + repr(geojson)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --- scenes bucket ---------------------------------------------------------
def scene_source(scene_id: str, filename: str) -> str:
    return f"scenes/{_id(scene_id,'scene_id')}/source/{_suffix(filename)}"


def scene_quicklook(scene_id: str) -> str:
    return f"scenes/{_id(scene_id,'scene_id')}/quicklook.webp"


def cdse_window_cache(external_id: str, aoi_h: str, band: str) -> str:
    safe_ext = hashlib.sha256(external_id.encode()).hexdigest()[:24]
    return f"cache/cdse/{safe_ext}/{_id(aoi_h,'aoi_hash')}/{_suffix(band)}.tif"


# --- jobs bucket -----------------------------------------------------------
def job_prefix(job_id: str) -> str:
    return f"jobs/{_id(job_id,'job_id')}"


def lr_tif(job_id: str) -> str: return f"{job_prefix(job_id)}/input/lr.tif"
def lr_mask(job_id: str) -> str: return f"{job_prefix(job_id)}/input/lr_mask.tif"
def lr_preview(job_id: str, combo: str = "rgb_true") -> str:
    return f"{job_prefix(job_id)}/input/preview_{_suffix(combo)}.webp"
def integrity_json(job_id: str) -> str: return f"{job_prefix(job_id)}/input/integrity.json"


def run_prefix(job_id: str, run_id: str) -> str:
    return f"{job_prefix(job_id)}/runs/{_id(run_id,'run_id')}"


def sr_raw(job_id: str, run_id: str) -> str: return f"{run_prefix(job_id, run_id)}/sr.tif"
def sr_cog(job_id: str, run_id: str) -> str: return f"{run_prefix(job_id, run_id)}/sr_cog.tif"
def sr_postprocessed(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/sr_postprocessed_cog.tif"
def uncertainty_cog(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/uncertainty_cog.tif"
def sample(job_id: str, run_id: str, k: int) -> str:
    return f"{run_prefix(job_id, run_id)}/samples/sample_{int(k):03d}.tif"
def run_preview(job_id: str, run_id: str, combo: str) -> str:
    return f"{run_prefix(job_id, run_id)}/preview/{_suffix(combo)}.webp"
def uncertainty_preview(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/preview/uncertainty_magma.png"
def metrics_json(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/metrics.json"
def run_metadata(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/run_metadata.json"
def inference_log(job_id: str, run_id: str) -> str:
    return f"{run_prefix(job_id, run_id)}/logs/inference.log"


def ensemble_uncertainty(job_id: str) -> str:
    return f"{job_prefix(job_id)}/uncertainty/ensemble_std_cog.tif"
def ensemble_uncertainty_preview(job_id: str) -> str:
    return f"{job_prefix(job_id)}/uncertainty/ensemble_std.png"


def analysis_prefix(job_id: str, analysis_id: str) -> str:
    return f"{job_prefix(job_id)}/analyses/{_id(analysis_id,'analysis_id')}"
def analysis_cog(job_id: str, analysis_id: str, which: str = "sr") -> str:
    return f"{analysis_prefix(job_id, analysis_id)}/result_{_suffix(which)}.tif"
def analysis_preview(job_id: str, analysis_id: str, which: str = "sr") -> str:
    return f"{analysis_prefix(job_id, analysis_id)}/preview_{_suffix(which)}.png"
def analysis_geojson(job_id: str, analysis_id: str) -> str:
    return f"{analysis_prefix(job_id, analysis_id)}/result.geojson"
def analysis_stats(job_id: str, analysis_id: str) -> str:
    return f"{analysis_prefix(job_id, analysis_id)}/stats.json"


def bundle_zip(job_id: str) -> str: return f"{job_prefix(job_id)}/bundle.zip"


# --- reports bucket --------------------------------------------------------
def report_pdf(report_id: str) -> str:
    return f"reports/{_id(report_id,'report_id')}/report.pdf"
def report_json(report_id: str) -> str:
    return f"reports/{_id(report_id,'report_id')}/report.json"
