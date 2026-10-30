"""
app/images.py – DICOM images of a patient as quick-to-show PNGs.

At import time each DICOM gets a preview (axial, coronal and sagittal middle slice side by
side) in cache/<patient>/images/<image_id>.png. Single slices are rendered on request for
scrolling. Grey levels are windowed to the 1st–99.5th percentile of the volume.
"""
from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

AXES = ("axial", "coronal", "sagittal")


@lru_cache(maxsize=4)
def _volume(dicom_path: str):
    """(volume as uint8 [frames, rows, cols], header info) – a few volumes stay cached for scrolling."""
    import numpy as np
    import pydicom

    ds = pydicom.dcmread(dicom_path)
    arr = ds.pixel_array.astype("float32")
    if arr.ndim == 2:
        arr = arr[None, ...]
    lo, hi = np.percentile(arr, [1, 99.5])
    vol = ((np.clip(arr, lo, hi) - lo) / max(hi - lo, 1e-6) * 255).astype("uint8")
    if getattr(ds, "PhotometricInterpretation", "") == "MONOCHROME1":
        vol = 255 - vol
    info = {"frames": int(vol.shape[0]), "rows": int(vol.shape[1]), "columns": int(vol.shape[2]),
            "modality": str(getattr(ds, "Modality", "")), "study_date": str(getattr(ds, "StudyDate", ""))}
    return vol, info


def _slice(vol, axis: str, index: Optional[int]):
    n = {"axial": vol.shape[0], "coronal": vol.shape[1], "sagittal": vol.shape[2]}[axis]
    i = n // 2 if index is None else max(0, min(int(index), n - 1))
    img = vol[i] if axis == "axial" else vol[:, i, :] if axis == "coronal" else vol[:, :, i]
    # Coronal and sagittal slices of a frame stack come upside down
    return (img[::-1] if axis != "axial" else img), n


def _png(array) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_preview(dicom: Path, out_dir: Path, image_id: str) -> dict:
    """Writes <image_id>.png (three middle slices side by side); returns its file name and the volume's size."""
    import numpy as np

    vol, info = _volume(str(dicom))
    views = [_slice(vol, axis, None)[0] for axis in AXES]
    height = max(v.shape[0] for v in views)
    padded = [np.pad(v, ((0, height - v.shape[0]), (0, 4)), constant_values=0) for v in views]
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{safe_id(image_id)}.png"
    (out_dir / name).write_bytes(_png(np.hstack(padded)))
    return {"preview": name, **info}


def render_slice(dicom: Path, axis: str, index: Optional[int]) -> tuple[bytes, int]:
    """One slice as PNG, and how many slices the axis has."""
    vol, _info = _volume(str(dicom))
    img, n = _slice(vol, axis, index)
    return _png(img), n


def safe_id(image_id: str) -> str:
    return "".join(c if c.isalnum() or c in ".-" else "-" for c in image_id)


def catalogue(cache_dir: Path, patient_id: str) -> dict[str, dict]:
    """image_id → {dicom, preview, modality, body_site, date, frames, …} from cache/<patient>/images.json."""
    path = cache_dir / patient_id / "images.json"
    return {i["image_id"]: i for i in json.loads(path.read_text(encoding="utf-8"))} if path.exists() else {}


def image_ref(fact, store):
    """ImageRef for a fact that names an image the patient's store has, else None."""
    from app.schemas import ImageRef
    image_id = (getattr(fact, "fields", None) or {}).get("image_id") if fact is not None else None
    info = getattr(store, "images", {}).get(image_id or "")
    if not info:
        return None
    base = f"/api/v2/patients/{store.patient_id}/images/{image_id}"
    return ImageRef(image_id=image_id, modality=info.get("modality"), body_site=info.get("body_site"),
                    date=info.get("date"), frames=info.get("frames"), preview_url=f"{base}/preview.png",
                    slice_url=f"{base}/slice.png", dicom_url=f"{base}/dicom")
