"""
app/fhir – the patient cache as one FHIR R4 Bundle (cache/<patient>/fhir.json).

    from app.fhir import load, save, Cache
    cache = load(path)          # → Cache (the same JSON artifacts the pipeline writes)
    save(cache, path)           # → validated R4 Bundle

See docs/fhir-plan.md for the mapping.
"""
from app.fhir.cache import ARTIFACTS, Cache, from_json_dir, load, save, to_json_dir

__all__ = ["ARTIFACTS", "Cache", "from_json_dir", "load", "save", "to_json_dir"]
