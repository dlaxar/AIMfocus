"""
tests/test_import_fhir.py – a FHIR patient with a DICOM image: import, several patients side
by side, image endpoints, image cards and image references in sources.
"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from tests.test_smoke import FIXTURE_DIR, _build_fixture_cache

PID = "FHIRTEST"
UID = "1.2.840.99999999.1.2"


def _dicom(path):
    import numpy as np
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality, ds.StudyDate = "OT", "20200102"
    vol = (np.arange(8 * 16 * 12) % 200).astype("uint16").reshape(8, 16, 12)
    ds.NumberOfFrames, ds.Rows, ds.Columns = 8, 16, 12
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.PixelData = vol.tobytes()
    ds.save_as(path, enforce_file_format=True)


def _bundle() -> dict:
    snomed = "http://snomed.info/sct"
    resources = [
        {"resourceType": "Patient", "id": "abcdef12-0000", "gender": "male", "birthDate": "1950-05-06",
         "name": [{"given": ["Max123"], "family": "Test456"}]},
        {"resourceType": "Encounter", "id": "e1", "period": {"start": "2020-01-02T10:00:00Z"}},
        {"resourceType": "Condition", "id": "c1", "onsetDateTime": "2019-03-04T00:00:00Z",
         "clinicalStatus": {"coding": [{"code": "active"}]},
         "code": {"coding": [{"system": snomed, "code": "38341003", "display": "Hypertension"}], "text": "Hypertension"}},
        {"resourceType": "Observation", "id": "o1", "status": "final", "effectiveDateTime": "2020-01-02T10:00:00Z",
         "category": [{"coding": [{"code": "vital-signs"}]}],
         "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body Weight"}], "text": "Body Weight"},
         "valueQuantity": {"value": 81.5, "unit": "kg"}},
        {"resourceType": "ImagingStudy", "id": "i1", "status": "available", "started": "2020-01-02T10:00:00Z",
         "identifier": [{"value": f"urn:oid:{UID}"}], "numberOfInstances": 8,
         "series": [{"modality": {"code": "MR"}, "bodySite": {"display": "Head structure (body structure)"}}]},
    ]
    return {"resourceType": "Bundle", "type": "transaction", "entry": [{"resource": r} for r in resources]}


@pytest.fixture(scope="module", autouse=True)
def imported(tmp_path_factory):
    from app.config import settings
    from preprocess import import_fhir

    _build_fixture_cache()
    folder = tmp_path_factory.mktemp("coherent") / "Max123_Test456"
    (folder / "dicom").mkdir(parents=True)
    (folder / "fhir.json").write_text(json.dumps(_bundle()), encoding="utf-8")
    _dicom(folder / "dicom" / f"Max123_{UID}.dcm")
    data_dir = settings.DATA_DIR
    settings.DATA_DIR = tmp_path_factory.mktemp("data")
    try:
        import_fhir.run(folder, PID)
        yield folder
    finally:
        settings.DATA_DIR = data_dir
        shutil.rmtree(settings.CACHE_DIR / PID, ignore_errors=True)


@pytest.fixture()
def client():
    import app.store as store_mod

    store_mod._store = None
    store_mod._stores.clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


class TestImport:
    def test_cache_and_image_preview_are_written(self):
        from app.config import settings
        out = settings.CACHE_DIR / PID
        assert (out / "manifest.json").exists() and (out / "fhir.json").exists()
        [image] = json.loads((out / "images.json").read_text(encoding="utf-8"))
        assert image["image_id"] == UID and image["modality"] == "MR" and image["body_site"] == "Head"
        assert image["frames"] == 8 and (out / "images" / image["preview"]).read_bytes()[:4] == b"\x89PNG"

    def test_coded_resources_become_facts_with_their_codes(self, client):
        from app.store import get_store
        store = get_store(PID)
        by_text = {f.en: f for f in store.facts.values()}
        assert "Hypertension" in by_text and "MRI of the head – image available" in by_text
        weight = next(f for f in store.facts.values() if f.concept_id == "C:meas:body_weight")
        assert weight.fields.get("value") in ("81.5", 81.5)
        codes = store.codes[by_text["Hypertension"].fact_id]
        assert {"system": "http://snomed.info/sct", "code": "38341003"}.items() <= codes[0].items()
        assert store.derived["D:age"].en.startswith("Age 69")   # born 1950-05-06, record ends 2020-01-02


class TestPatients:
    def test_both_patients_are_listed_and_kept_apart(self, client):
        listed = {p["patient_id"]: p for p in client.get("/api/v2/patients").json()}
        assert listed["TEST"]["default"] and listed["TEST"]["images"] == 0
        assert listed[PID]["source"] == "fhir" and listed[PID]["images"] == 1 and listed[PID]["name"] == "Max Test"
        profile = client.get(f"/api/v2/patients/{PID}").json()
        assert profile["patient_id"] == PID and profile["sex"] == "male" and profile["images"] == 1
        assert client.get("/api/v2/patients/TEST").json()["patient_id"] == "TEST"

    def test_unknown_patient_is_404(self, client):
        assert client.get("/api/v2/patients/NOPE").status_code == 404
        assert client.get("/api/documents?patient_id=NOPE").status_code == 404
        assert client.post("/api/v2/screens", json={"situation": "x", "patient_id": "NOPE"}).status_code == 404

    def test_documents_are_read_per_patient(self, client):
        docs = client.get(f"/api/documents?patient_id={PID}").json()
        assert docs and all(d["doc_id"].startswith(PID) for d in docs)
        assert not any(d["doc_id"].startswith(PID) for d in client.get("/api/documents").json())

    def test_screen_and_chat_follow_the_patient(self, client):
        screen = client.post("/api/v2/screens", json={"situation": "Neurologist, headache", "patient_id": PID}).json()
        assert screen["patient_id"] == PID
        other = client.post("/api/v2/screens", json={"situation": "Neurologist, headache"}).json()
        assert other["patient_id"] == "TEST"
        bad = client.post("/api/v2/chat", json={"question": "MRI?", "screen_id": screen["screen_id"], "patient_id": "TEST"})
        assert bad.status_code == 400   # the screen is another patient's
        chat = client.post("/api/v2/chat", json={"question": "MRI?", "screen_id": screen["screen_id"]}).json()
        assert client.get(f"/api/v2/chat/{chat['chat_id']}").json()["patient_id"] == PID


class TestImages:
    def test_image_list_carries_the_links(self, client):
        [img] = client.get(f"/api/v2/patients/{PID}/images").json()
        assert img["image_id"] == UID and img["frames"] == 8 and img["title"].startswith("MRI of the head")
        assert img["preview_url"] == f"/api/v2/patients/{PID}/images/{UID}/preview.png"

    def test_preview_slice_and_dicom(self, client):
        base = f"/api/v2/patients/{PID}/images/{UID}"
        r = client.get(f"{base}/preview.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        r = client.get(f"{base}/slice.png?axis=coronal&index=3")
        assert r.status_code == 200 and r.headers["x-slice-count"] == "16" and r.content[:4] == b"\x89PNG"
        assert client.get(f"{base}/slice.png?axis=oblique").status_code == 400
        r = client.get(f"{base}/dicom")
        assert r.status_code == 200 and r.content[128:132] == b"DICM"
        assert client.get(f"/api/v2/patients/{PID}/images/1.2.3/preview.png").status_code == 404

    def test_imaging_fact_source_carries_the_image(self, client):
        from app.fastpath.service import _sources_for_facts
        from app.store import get_store
        store = get_store(PID)
        fact = next(f for f in store.facts.values() if f.fields.get("image_id"))
        [src] = [s for s in _sources_for_facts([fact.fact_id], store) if s.image]
        assert src.image.image_id == UID and src.image.slice_url.endswith("/slice.png")

    def test_image_card_shows_the_studies_or_says_none(self, client):
        from app.fastpath.models import SelectedVariable
        from app.fastpath.service import _image_variable
        from app.store import get_store
        sel = SelectedVariable(name="brain_mri", label="Brain MRI", output="image", concepts=[], facts=[])
        var = _image_variable(sel, get_store(PID), "2020-01-02", 0.0)
        assert var.state == "documented" and var.output == "image"
        assert var.items[0].source.image.image_id == UID
        assert _image_variable(sel, get_store("TEST"), "2020-01-02", 0.0) is None   # letters only → not_available


class TestPortablePaths:
    def test_paths_are_stored_relative_to_data_dir(self, monkeypatch, tmp_path):
        from app import images
        from app.config import settings
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        f = tmp_path / "coherent" / "P1" / "dicom" / "x.dcm"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
        assert images.relative_to_data(f) == "coherent/P1/dicom/x.dcm"
        assert images.dicom_path("coherent/P1/dicom/x.dcm") == f

    def test_absolute_path_from_another_machine_is_rebased(self, monkeypatch, tmp_path):
        from app import images
        from app.config import settings
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        f = tmp_path / "coherent" / "P1" / "dicom" / "x.dcm"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
        assert images.dicom_path("/home/someone/repo/data/coherent/P1/dicom/x.dcm") == f

    def test_missing_dicom_is_404_but_preview_works(self, client):
        from app.store import get_store
        info = get_store(PID).images[UID]
        real = info["dicom"]
        info["dicom"] = "/nowhere/data/missing.dcm"
        try:
            base = f"/api/v2/patients/{PID}/images/{UID}"
            assert client.get(f"{base}/dicom").status_code == 404
            assert client.get(f"{base}/slice.png").status_code == 404
            assert client.get(f"{base}/preview.png").status_code == 200
        finally:
            info["dicom"] = real


class TestPatientPickedLater:
    """The situation is entered first (screen for the default patient), the patient picked later."""

    def test_screen_is_rebuilt_for_the_picked_patient_under_the_same_id(self, client):
        screen = client.post("/api/v2/screens", json={"situation": "Neurologist, headache"}).json()
        first = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true").json()
        assert first["patient_id"] == "TEST"
        out = client.put(f"/api/v2/screens/{screen['screen_id']}/patient", json={"patient_id": PID})
        assert out.status_code == 200
        switched = out.json()
        assert switched["screen_id"] == screen["screen_id"] and switched["patient_id"] == PID
        assert switched["version"] > first["version"]
        done = client.get(f"/api/v2/screens/{screen['screen_id']}?wait=true").json()
        assert done["patient_id"] == PID and done["status"] == "complete"
        # the same patient again: nothing is rebuilt
        again = client.put(f"/api/v2/screens/{screen['screen_id']}/patient", json={"patient_id": PID}).json()
        assert again["version"] == done["version"]

    def test_chat_follows_the_switched_screen(self, client):
        screen = client.post("/api/v2/screens", json={"situation": "Neurologist, headache"}).json()
        sid = screen["screen_id"]
        before = client.post("/api/v2/chat", json={"question": "MRI?", "screen_id": sid}).json()
        assert client.get(f"/api/v2/chat/{before['chat_id']}").json()["patient_id"] == "TEST"
        client.put(f"/api/v2/screens/{sid}/patient", json={"patient_id": PID})
        after = client.post("/api/v2/chat", json={"question": "MRI?", "screen_id": sid, "chat_id": before["chat_id"]})
        assert after.status_code == 200
        chat = client.get(f"/api/v2/chat/{after.json()['chat_id']}").json()
        assert chat["patient_id"] == PID and after.json()["chat_id"] != before["chat_id"]   # a new conversation

    def test_unknown_screen_or_patient_is_404(self, client):
        assert client.put("/api/v2/screens/nope/patient", json={"patient_id": PID}).status_code == 404
        screen = client.post("/api/v2/screens", json={"situation": "x y"}).json()
        assert client.put(f"/api/v2/screens/{screen['screen_id']}/patient", json={"patient_id": "NOPE"}).status_code == 404

    def test_upgrade_for_the_earlier_patient_never_replaces_the_new_screen(self, client, monkeypatch):
        import app.llm.client as client_mod
        from app.config import settings
        from tests.test_upgrade import _ModelClient
        monkeypatch.setattr(settings, "RUNTIME_MODEL", "fast-model")
        monkeypatch.setattr(settings, "UPGRADE_MODEL", "slow-model")
        previous = client_mod._client_instance
        client_mod.use_client(_ModelClient({"slow-model": 0.3}))
        try:
            screen = client.post("/api/v2/screens", json={"situation": "Neurologist, headache"}).json()
            sid = screen["screen_id"]
            client.get(f"/api/v2/screens/{sid}?wait=true")            # fast screen done, upgrade still running
            client.put(f"/api/v2/screens/{sid}/patient", json={"patient_id": PID})
            final = client.get(f"/api/v2/screens/{sid}?wait=true&final=true").json()
            assert final["patient_id"] == PID and final["model"] == "slow-model" and final["upgrade"] == "done"
        finally:
            client_mod.use_client(previous)

    def test_picking_the_patient_needs_no_llm_call(self, client, monkeypatch):
        import app.llm.client as client_mod
        from app.config import settings
        from tests.test_upgrade import _ModelClient
        monkeypatch.setattr(settings, "RUNTIME_MODEL", "fast-model")
        monkeypatch.setattr(settings, "UPGRADE_MODEL", "slow-model")
        previous = client_mod._client_instance
        llm = _ModelClient({})
        client_mod.use_client(llm)
        try:
            sid = client.post("/api/v2/screens", json={"situation": "Neurologist, headache"}).json()["screen_id"]
            client.get(f"/api/v2/screens/{sid}?wait=true&final=true")   # select (default patient) and plans, both models
            before = [p for p, _ in llm.models if p in ("fp_plan", "fp_select")]
            assert sorted(before) == ["fp_plan", "fp_plan", "fp_select", "fp_select"]
            client.put(f"/api/v2/screens/{sid}/patient", json={"patient_id": PID})
            done = client.get(f"/api/v2/screens/{sid}?wait=true&final=true").json()
            assert done["patient_id"] == PID and done["upgrade"] == "done"
            assert [p for p, _ in llm.models if p in ("fp_plan", "fp_select")] == before   # no new plan or select
        finally:
            client_mod.use_client(previous)


class TestPlanMatch:
    """A plan made without the patient, applied to the patient's facts in code."""

    def _match(self, **kw):
        from app.fastpath.plan import PlannedVariable, match
        from app.store import get_store
        return match(PlannedVariable(label="x", **kw), get_store(PID), "2020-01-02")

    def test_terms_find_concepts(self, client):
        concepts, _ = self._match(types=["diagnosis"], terms=["hypertension", "high blood pressure"])
        assert any("hypertension" in c for c in concepts)

    def test_measurement_keys(self, client):
        concepts, _ = self._match(types=["measurement"], measures=["body_weight"])
        assert concepts == ["C:meas:body_weight"]

    def test_types_keep_unrelated_facts_out(self, client):
        concepts, facts = self._match(types=["medication"], terms=["hypertension"])
        assert not concepts and not facts

    def test_derived_age_and_word_prefix(self, client):
        _, facts = self._match(types=["demographic"], derived=["age"])
        assert facts == ["D:age"]
        concepts, _ = self._match(types=["diagnosis"], terms=["hypertens"])   # start of a longer word
        assert any("hypertension" in c for c in concepts)
