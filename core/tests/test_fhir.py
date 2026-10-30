"""
tests/test_fhir.py – the cache as a FHIR R4 Bundle: lossless round trip, valid resources,
standard codes from IBM-style files, and the store reading fhir.json.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import fhir
from app.fhir import mapping
from app.fhir.codes import ibm_codes
from app.fhir.extensions import ATC, ICD10, LOINC, SNOMED
from tests.test_smoke import CACHE_DIR as FIXTURE_CACHE, _build_fixture_cache

PREF = {"reference": "Patient/P"}


@pytest.fixture(scope="module")
def fixture_cache():
    _build_fixture_cache()
    return fhir.from_json_dir(FIXTURE_CACHE, "TEST")


def _refs(x, out):
    if isinstance(x, dict):
        if isinstance(x.get("reference"), str):
            out.append(x["reference"])
        for v in x.values():
            _refs(v, out)
    elif isinstance(x, list):
        for v in x:
            _refs(v, out)
    return out


class TestBundle:
    def test_round_trip_is_lossless(self, fixture_cache, tmp_path):
        path = tmp_path / "fhir.json"
        fhir.save(fixture_cache, path)
        assert fhir.load(path).data == fixture_cache.data

    def test_every_resource_valid_and_every_reference_resolves(self, fixture_cache):
        bundle = fhir.cache.to_bundle(fixture_cache)
        fhir.cache.validate(bundle)                      # raises on an invalid R4 resource
        ids = {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]}
        assert set(_refs(bundle, [])) <= ids

    def test_held_back_finding_without_id_round_trips(self, fixture_cache, tmp_path):
        cache = fhir.Cache(patient_id="TEST", data={**fixture_cache.data, "contradictions": [], "contradictions_report": [
            {"shown": False, "key": "lv_wall:D1", "kind": "value", "summary_en": "thick wall, normal size", "passage_ids": []}]})
        path = tmp_path / "fhir.json"
        bundle = fhir.save(cache, path)
        issues = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "DetectedIssue"]
        assert [i["status"] for i in issues] == ["preliminary"]
        assert fhir.load(path).data == cache.data

    def test_drop_from_keeps_earlier_stages(self, fixture_cache):
        cache = fhir.Cache(patient_id="TEST", data=dict(fixture_cache.data))
        cache.drop_from("s2")
        assert {"documents", "passages"} <= set(cache.data)
        assert not {"facts", "concepts", "derived", "contradictions", "manifest"} & set(cache.data)

    def test_store_reads_fhir_json(self, tmp_path, monkeypatch):
        from app.config import settings
        from app.store import FactStore

        _build_fixture_cache()
        shutil.copytree(FIXTURE_CACHE, tmp_path / "TEST")
        fhir.save(fhir.from_json_dir(tmp_path / "TEST", "TEST"), tmp_path / "TEST" / "fhir.json")
        for name in fhir.ARTIFACTS:                       # only fhir.json is left
            (tmp_path / "TEST" / f"{name}.json").unlink(missing_ok=True)
        monkeypatch.setattr(settings, "CACHE_FORMAT", "fhir")
        store = FactStore()
        store.load(tmp_path, "TEST")
        assert store.source == "fhir"
        assert "F:TEST001:all:01" in store.facts and store.documents and store.passages
        assert store.fact_sheet.startswith("# Fact sheet")


class TestMapping:
    def _fact(self, **kw):
        base = {"fact_id": "F:D1:dia:01", "type": "diagnosis", "passage_id": "D1:p1", "span": "x", "fields": {},
                "status": "positive", "laterality": None, "date": "2016-04-15", "concept_id": "C:dx:x", "en": "X", "de": "x"}
        return {**base, **kw}

    def test_negative_diagnosis_is_refuted(self):
        r = mapping.fact_resource(self._fact(status="negative"), PREF, "X", [])
        assert r["resourceType"] == "Condition"
        assert r["verificationStatus"]["coding"][0]["code"] == "refuted" and "clinicalStatus" not in r

    def test_blood_pressure_has_loinc_and_two_components(self):
        r = mapping.fact_resource(self._fact(type="measurement", concept_id="C:meas:blood_pressure",
                                             fields={"value": "145/88", "unit": "mmHg"}), PREF, "Blood pressure", [])
        assert {"system": LOINC, "code": "85354-9", "display": "Blood pressure panel with all children optional"} in r["code"]["coding"]
        assert [c["valueQuantity"]["value"] for c in r["component"]] == [145.0, 88.0]

    def test_laterality_is_a_snomed_body_site(self):
        r = mapping.fact_resource(self._fact(type="finding", laterality="left"), PREF, None, [])
        assert r["bodySite"]["coding"][0] == {"system": SNOMED, "code": "7771000", "display": "Left"}

    def test_current_medication_is_active_with_dosage(self):
        r = mapping.fact_resource(self._fact(type="medication", status="current",
                                             fields={"strength": "100 mg", "schedule": "1-0-0", "route": "oral"}), PREF, None, [])
        assert r["status"] == "active" and r["dosage"][0]["text"] == "100 mg 1-0-0"


class TestIbmCodes:
    def test_codes_go_to_the_matching_fact_and_its_concept(self, tmp_path):
        folder = tmp_path / "patient-P"
        folder.mkdir()
        tree = lambda catalog, code, text: {"values": [{"name": "group", "values": [
            {"name": "coded", "code": {"catalog": catalog, "value": code}, "values": [
                {"name": text, "text": f"<SPAN>{text}</SPAN>", "documentIds": ["D1"]}]}]}]}
        (folder / "medication.json").write_text(json.dumps(tree("ATC", "B01AC06", "Thrombo ASS 100mg")), encoding="utf-8")
        (folder / "diagnoses.json").write_text(json.dumps(tree("ICD10", "I10", "Hypertonie")), encoding="utf-8")
        passages = [{"passage_id": "D1:p1", "doc_id": "D1", "text": "Thrombo ASS 100mg 1-0-0"},
                    {"passage_id": "D1:p2", "doc_id": "D1", "text": "Diagnosen: Hypertonie"}]
        facts = [
            {"fact_id": "F:D1:med:01", "type": "medication", "passage_id": "D1:p1", "span": "Thrombo ASS 100mg", "concept_id": "C:med:asa"},
            {"fact_id": "F:D2:med:01", "type": "medication", "passage_id": "D2:p9", "span": "ASS", "concept_id": "C:med:asa"},
            {"fact_id": "F:D1:dia:01", "type": "diagnosis", "passage_id": "D1:p2", "span": "Hypertonie", "concept_id": "C:dx:htn"},
        ]
        codes, stats = ibm_codes(tmp_path, "P", facts, passages)
        assert codes["F:D1:med:01"] == [{"system": ATC, "code": "B01AC06"}]
        assert codes["F:D2:med:01"] == [{"system": ATC, "code": "B01AC06"}]     # spread over the concept
        assert codes["F:D1:dia:01"] == [{"system": ICD10, "code": "I10"}]
        assert stats["mentions"] == 2 and stats["fact_matched"] == 2

    def test_no_ibm_files_means_no_codes(self, tmp_path):
        assert ibm_codes(tmp_path, "P", [], [])[0] == {}


class TestOfficialValidator:
    """Runs the HL7 FHIR Validator when it is installed (FHIR_VALIDATOR_JAR or ~/tools/validator_cli.jar)."""

    def test_fixture_bundle_has_no_errors(self, fixture_cache, tmp_path):
        import os
        from preprocess.validate_fhir import _jar, validate
        if not os.path.exists(_jar()):
            pytest.skip("HL7 FHIR Validator not installed")
        path = tmp_path / "fhir.json"
        fhir.save(fixture_cache, path)
        result = validate(path)
        assert result["errors"] == []
