"""
eval/score_runs.py – scores `run_eval --v2 --extra` outputs (31 situations, 93 questions)
with one fixed criterion per question, applied to the text of the screen.

Usage:
    python -m eval.run_eval --v2 --extra > /tmp/run.txt 2> /tmp/run.err
    python eval/score_runs.py /tmp/run.txt [/tmp/other_run.txt ...]

Expected answers: cases/expected.json (1.x–5.x) and eval/expected_extra.json (6.x–31.x).
A criterion checks that the expected facts are on the screen; for "is X documented?"
questions it accepts an explicit "not documented" item or an equivalent statement.
"""
import json, re, sys
from pathlib import Path


def items(block):
    return [l for l in block.splitlines() if re.match(r"\s+\[\d+\]", l)]


def has(t, *alts):
    return any(a in t for a in alts)


def nd(lines, *words):
    return any(any(w in l.lower() for w in words) and "[not_documented]" in l for l in lines)


def kid(t, L):
    """Kidney function not documented: the state, or an equivalent statement from the text search."""
    return nd(L, "kidney", "renal") or any(has(l.lower(), "kidney", "renal", "creatinine", "egfr") and has(l.lower(), *NOREC, "no kidney", "no renal", "cannot be determined", " not ") for l in L)


def side_left(L):
    return any("stenosis" in l.lower() and "left" in l.lower() for l in L)


def carotid_right(L):
    """Minimal right carotid plaques, checked per statement: "Hollenhorst plaques ... left > right" (eye card)
    and the side-swapped copy "right internal carotid artery narrowing of about 60%" do not count."""
    return any(has(s, "right") and has(s, "carotid", "ica") and has(s, "minimal", "without hemodynamic", "without haemodynamic")
               for l in L for s in l.lower().split(";"))


DRUGS = [("aspirin", "thrombo ass"), ("metformin", "glucophage"), ("simvastatin", "zocor"), ("candesartan", "blopress"),
         ("oleovit", "colecalciferol", "vitamin d"), ("alendron",), ("pioglitazone", "actos"), ("exenatide", "byetta")]
NOREC = ("no later", "not documented", "no record", "no subsequent", "no follow", "no further", "not recorded", "no documented", "no completion", "no evidence")

C = {
 "1.1": lambda t, L: has(t, "100 mg") and has(t, "1-0-0", "morning") and has(t, "aspirin", "ass"),
 "1.2": lambda t, L: has(t, "contrast") and has(t, "nausea") and has(t, "dizziness") and has(t, "vomiting"),
 "1.3": lambda t, L: has(t, "60%") and has(t, "lvef", "ejection") and has(t, "2015", "june", "jun "),
 "2.1": lambda t, L: has(t, "ductal") and has(t, "t2 n0 m0") and has(t, "left"),
 "2.2": lambda t, L: all(has(t, x) for x in ("100%", "allred 8", "70%", "allred 7", "her2", "ki-67", "20%")),
 "2.3": lambda t, L: has(t, "quadrantectomy") and has(t, "16 may 2016", "2016-05-16"),
 "3.1": lambda t, L: all(has(t, x) for x in ("metformin", "1000 mg", "1-1-1", "pioglitazone", "30 mg", "0-1-0", "exenatide", "1-0-1")),
 "3.2": lambda t, L: has(t, "6.4%") and has(t, "2015"),
 "3.3": lambda t, L: has(t, "78 kg") and has(t, "67 kg") and has(t, "66 kg"),
 "4.1": lambda t, L: has(t, "amaurosis", "vision loss", "visual loss") and has(t, "60%") and side_left(L),
 "4.2": lambda t, L: has(t, "contrast") and has(t, "without contrast", "native") and has(t, "metformin") and kid(t, L),
 "4.3": lambda t, L: has(t, "candesartan") and has(t, "16 mg") and has(t, "1-0-1"),
 "5.1": lambda t, L: has(t, "duplex") and has(t, "2 months") and has(t, "eye", "ophthalm") and has(t, "blood pressure", "bp "),
 "5.2": lambda t, L: has(t, "duplex") and has(t, *NOREC),
 "5.3": lambda t, L: side_left(L),
 "6.1": lambda t, L: all(has(t, x) for x in ("100%", "allred 8", "70%", "allred 7", "her2")),
 "6.2": lambda t, L: has(t, "57"),
 "6.3": lambda t, L: has(t, "osteoporosis") and has(t, "alendron"),
 "7.1": lambda t, L: has(t, "vision", "visual", "amaurosis") and has(t, "left") and has(t, "2015", "june", "jun "),
 "7.2": lambda t, L: has(t, "hollenhorst") and has(t, "fundus", "papill"),
 "7.3": lambda t, L: any(has(l.lower(), "eye", "ophthalm") and has(l.lower(), *NOREC) for l in L) or nd(L, "eye", "ophthalm"),
 "8.1": lambda t, L: has(t, "contrast") and has(t, "nausea") and has(t, "vomiting"),
 "8.2": lambda t, L: has(t, "170 cm") and has(t, "66 kg") and has(t, "22.8"),
 "8.3": lambda t, L: all(has(t, *d) for d in DRUGS),
 "9.1": lambda t, L: all(has(t, *d) for d in DRUGS),
 "9.2": lambda t, L: has(t, "candesartan", "blopress") and has(t, "1-0-1/2", "1-0-½", "1-0-0.5", "changed", "increased", "adjusted"),
 "9.3": lambda t, L: kid(t, L),
 "10.1": lambda t, L: has(t, "contrast") and has(t, "nausea") and has(t, "vomiting"),
 "10.2": lambda t, L: has(t, "metformin") and has(t, "1000 mg"),
 "10.3": lambda t, L: kid(t, L),
 "11.1": lambda t, L: has(t, "60%") and has(t, "lvef", "ejection") and has(t, "thicken", "hypertroph", "septum"),
 "11.2": lambda t, L: has(t, "sinus") and has(t, "47"),
 "11.3": lambda t, L: has(t, "135/84"),
 "12.1": lambda t, L: side_left(L) and carotid_right(L),
 "12.2": lambda t, L: has(t, "ct") and has(t, "without contrast", "native") and has(t, "bleed", "haemorrhage", "hemorrhage"),
 "12.3": lambda t, L: has(t, "focal"),
 "13.1": lambda t, L: has(t, "6.4%"),
 "13.2": lambda t, L: has(t, "exenatide", "byetta") and has(t, "5 µg", "5 mcg", "5 ug", "5µg") and has(t, "subcut", "s.c.") and has(t, "1-0-1"),
 "13.3": lambda t, L: has(t, "retinopathy", "fundus"),
 "14.1": lambda t, L: has(t, "lupus", "sle"),
 "14.2": lambda t, L: has(t, "inflammat", "autoimmun"),
 "14.3": lambda t, L: has(t, "osteoporosis"),
 "15.1": lambda t, L: has(t, "gerd", "reflux") and has(t, "grade ii", "grade 2", "ii°"),
 "15.2": lambda t, L: nd(L, "acid", "ppi", "proton") or any(has(l.lower(), "acid", "ppi", "proton") and has(l.lower(), "no ", "not ") for l in L),
 "15.3": lambda t, L: has(t, "alendron") and has(t, "aspirin", "thrombo ass"),
 "16.1": lambda t, L: has(t, "alendron") and has(t, "70 mg"),
 "16.2": lambda t, L: has(t, "aspirin", "ass") and has(t, "100 mg"),
 "16.3": lambda t, L: has(t, "contrast"),
 "17.1": lambda t, L: has(t, "left") and has(t, "3 x 1.5", "3 cm"),
 "17.2": lambda t, L: has(t, "quadrantectomy") and has(t, "16 may 2016", "2016-05-16"),
 "17.3": lambda t, L: has(t, "lymph") and has(t, "no enlarged", "n0"),
 "18.1": lambda t, L: has(t, "apnoea", "apnea"),
 "18.2": lambda t, L: has(t, "cpap"),
 "18.3": lambda t, L: has(t, "29.4") and has(t, "22.8"),
 "19.1": lambda t, L: has(t, "145/88"),
 "19.2": lambda t, L: has(t, "candesartan") and has(t, "16 mg") and has(t, "1-0-1"),
 "19.3": lambda t, L: has(t, "hypertensi") and has(t, "diabet") and has(t, "lipid", "cholesterol"),
 "20.1": lambda t, L: has(t, "discharg") and has(t, "15 apr", "2016-04-15", "15.04"),
 "20.2": lambda t, L: has(t, "quadrantectomy") and has(t, "16 may", "2016-05-16"),
 "20.3": lambda t, L: has(t, "duplex") and has(t, *NOREC, "open", "not done"),
 "21.1": lambda t, L: has(t, "gerd", "reflux") and has(t, "hiatus"),
 "21.2": lambda t, L: has(t, "apnoea", "apnea"),
 "21.3": lambda t, L: has(t, "ecg", "ekg", "heart rate", "hr ") and has(t, "90"),
 "22.1": lambda t, L: has(t, "ductal") and has(t, "t2 n0"),
 "22.2": lambda t, L: sum(has(t, *x) for x in [("gerd", "reflux"), ("lupus", "sle"), ("diabet",), ("hypertension",), ("hyperlipid",)]) >= 3,
 "22.3": lambda t, L: has(t, "biopsy") and has(t, "ultrasound"),
 "23.1": lambda t, L: has(t, "mammograph") and has(t, "april 2016", "2016-04", "3 apr", "03.04"),
 "23.2": lambda t, L: has(t, "family"),
 "23.3": lambda t, L: has(t, "13 apr", "2016-04-13", "13.04"),
 "24.1": lambda t, L: has(t, "60%") and side_left(L),
 "24.2": lambda t, L: has(t, "amaurosis", "vision loss", "visual loss", "loss of vision"),
 "24.3": lambda t, L: has(t, "duplex") and has(t, *NOREC, "open"),
 "25.1": lambda t, L: has(t, "185/110"),
 "25.2": lambda t, L: has(t, "nitro"),
 "25.3": lambda t, L: has(t, "candesartan", "blopress") and has(t, "16 mg"),
 "26.1": lambda t, L: has(t, "mmse") and has(t, "30/30"),
 "26.2": lambda t, L: has(t, "78 kg") and has(t, "66 kg"),
 "26.3": lambda t, L: has(t, "alendron") and has(t, "colecalciferol", "vitamin d", "oleovit"),
 "27.1": lambda t, L: has(t, "g2") and has(t, "t2 n0 m0"),
 "27.2": lambda t, L: has(t, "ki-67", "ki67") and has(t, "20%"),
 "27.3": lambda t, L: has(t, "core") and has(t, "biops"),
 "28.1": lambda t, L: has(t, "cholesterol", "lipid"),
 "28.2": lambda t, L: has(t, "simvastatin", "zocor") and has(t, "20 mg"),
 "28.3": lambda t, L: nd(L, "ldl", "cholesterol", "lipid") or any(has(l.lower(), "ldl", "lipid value", "cholesterol value") and has(l.lower(), *NOREC, "no ", "not ") for l in L),
 "29.1": lambda t, L: has(t, "60%") and has(t, "36 mm"),
 "29.2": lambda t, L: has(t, "tapse") and has(t, "25 mm"),
 "29.3": lambda t, L: has(t, "22 mmhg", "pulmonary hypertension"),
 "30.1": lambda t, L: has(t, "cholecystectomy"),
 "30.2": lambda t, L: has(t, "hiatus hernia"),
 "30.3": lambda t, L: has(t, "aspirin", "acetylsalicylic", "thrombo ass") and not has(t, "apixaban", "rivaroxaban", "edoxaban", "dabigatran", "warfarin", "phenprocoumon", "clopidogrel"),
 "31.1": lambda t, L: has(t, "2015-06-10", "10 jun", "10.06") and has(t, "2015-06-14", "14 jun", "14.06"),
 "31.2": lambda t, L: has(t, "hypertensive crisis"),
 "31.3": lambda t, L: has(t, "2016-04-10", "10 apr", "10.04"),
}

SHORT = {"1": "Anaesthesia pre-op", "2": "Surgery planning", "3": "GP diabetes", "4": "Emergency at night", "5": "Open follow-ups",
         "6": "Oncologist", "7": "Ophthalmologist", "8": "Ward nurse", "9": "Pharmacist", "10": "Radiologist (contrast CT)",
         "11": "Cardiologist", "12": "Neurologist", "13": "Diabetologist", "14": "Rheumatologist", "15": "Gastroenterologist",
         "16": "Dentist", "17": "Radiation oncologist", "18": "Sleep physician", "19": "GP blood pressure",
         "20": "Discharge coordinator", "21": "Anaesthetist day of surgery", "22": "Clinical coder", "23": "GP cancer history",
         "24": "Vascular surgeon", "25": "Emergency high BP", "26": "Geriatrician", "27": "Pathologist", "28": "Lipid clinic", "29": "Echo comparison", "30": "Surgical history", "31": "Admissions office"}


def score(path):
    txt = Path(path).read_text()
    parts = re.split(r"^### Situation (\d+):.*$", txt, flags=re.M)
    blocks = {parts[i]: items(parts[i + 1]) for i in range(1, len(parts), 2)}
    res = {}
    for qid, fn in C.items():
        L = [_normalize(line) for line in blocks.get(qid.split(".")[0], [])]
        res[qid] = bool(fn("\n".join(L).lower(), L))
    return res


def _normalize(line):
    """Spelling only: "100mg" = "100 mg", "1-0-0-0" (four-slot schedule) = "1-0-0",
    "Allred score 8" = "Allred 8", US = UK spelling."""
    line = re.sub(r"(\d)\s*(mg|mcg|µg|ug|kg|ml|mmhg)\b", r"\1 \2", line, flags=re.I)
    line = re.sub(r"allred score:?\s*", "Allred ", line, flags=re.I)
    line = re.sub(r"\bnon-?contrast\b", "without contrast", line, flags=re.I)
    for us, uk in (("estrogen", "oestrogen"), ("tumor", "tumour"), ("apnea", "apnoea"), ("esophag", "oesophag"),
                   ("hiatal hernia", "hiatus hernia"), ("hemorrh", "haemorrh")):
        line = re.sub(rf"\b{us}", uk, line, flags=re.I)
    return re.sub(r"\b(\d(?:/\d)?-\d(?:/\d)?-\d(?:/\d)?)-0\b", r"\1", line)




if __name__ == "__main__":
    for path in sys.argv[1:]:
        res = score(path)
        err = Path(path).with_suffix(".err")
        times = [int(m) / 1000 for m in re.findall(r"complete (\d+) ms", err.read_text())] if err.exists() else []
        challenge = sum(v for q, v in res.items() if int(q.split(".")[0]) <= 5)
        timing = f"  mean {sum(times) / len(times):.1f} s  max {max(times):.1f} s" if times else ""
        print(f"{path}: {sum(res.values())}/{len(res)} (challenge {challenge}/15){timing}")
        print("  missed:", ", ".join(q for q, ok in res.items() if not ok) or "–")
