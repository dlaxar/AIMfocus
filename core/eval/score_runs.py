"""
eval/score_runs.py – scores `run_eval --v2 --extra` outputs (23 situations, 69 questions)
with one fixed criterion per question, applied to the text of the screen.

Usage:
    python -m eval.run_eval --v2 --extra > /tmp/run.txt 2> /tmp/run.err
    python eval/score_runs.py /tmp/run.txt [/tmp/other_run.txt ...]

Expected answers: cases/expected.json (1.x–5.x) and eval/expected_extra.json (6.x–23.x).
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
    return nd(L, "kidney", "renal")


def side_left(L):
    return any("stenosis" in l.lower() and "left" in l.lower() for l in L)


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
 "12.1": lambda t, L: side_left(L) and any("right" in l.lower() and has(l.lower(), "minimal", "plaque") for l in L),
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
}

SHORT = {"1": "Anaesthesia pre-op", "2": "Surgery planning", "3": "GP diabetes", "4": "Emergency at night", "5": "Open follow-ups",
         "6": "Oncologist", "7": "Ophthalmologist", "8": "Ward nurse", "9": "Pharmacist", "10": "Radiologist (contrast CT)",
         "11": "Cardiologist", "12": "Neurologist", "13": "Diabetologist", "14": "Rheumatologist", "15": "Gastroenterologist",
         "16": "Dentist", "17": "Radiation oncologist", "18": "Sleep physician", "19": "GP blood pressure",
         "20": "Discharge coordinator", "21": "Anaesthetist day of surgery", "22": "Clinical coder", "23": "GP cancer history"}


def score(path):
    txt = Path(path).read_text()
    parts = re.split(r"^### Situation (\d+):.*$", txt, flags=re.M)
    blocks = {parts[i]: items(parts[i + 1]) for i in range(1, len(parts), 2)}
    res = {}
    for qid, fn in C.items():
        L = blocks.get(qid.split(".")[0], [])
        res[qid] = bool(fn("\n".join(L).lower(), L))
    return res




if __name__ == "__main__":
    for path in sys.argv[1:]:
        res = score(path)
        err = Path(path).with_suffix(".err")
        times = [int(m) / 1000 for m in re.findall(r"complete (\d+) ms", err.read_text())] if err.exists() else []
        challenge = sum(v for q, v in res.items() if int(q.split(".")[0]) <= 5)
        timing = f"  mean {sum(times) / len(times):.1f} s  max {max(times):.1f} s" if times else ""
        print(f"{path}: {sum(res.values())}/{len(res)} (challenge {challenge}/15){timing}")
        print("  missed:", ", ".join(q for q, ok in res.items() if not ok) or "–")
