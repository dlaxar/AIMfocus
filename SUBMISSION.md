# AIM Healthcare Hackathon 2026 — Team Submission

Draft answers for the
[submission form](https://docs.google.com/forms/d/e/1FAIpQLScZXc_XL3Nwd4crtbbtTG-ZS4p7QuvmVhXo_5mXsWG2ytUcRw/viewform).

## Team name

Oachkatzlschwoaf

## Member names

Daniel Laxar, Lorenz Kapral, Henry Forman, Mohammad Azarbeik

## Track

1 — Clinical Documentation

## Demo video URL

TODO

## Repository URL

https://github.com/dlaxar/AIMfocus

## Problem (1–2 lines)

A patient's history is spread across many letters from different departments that
contradict each other, and each clinician needs a different handful of facts from them right
now. Reading everything takes too long, and AI summaries that might invent a dose can't be
trusted.

## What they built

AIMfocus: the clinician types or says who they are and what they're about to do
("Emergency department, blurry vision"), picks the patient, and in about 5 seconds gets one screen
with only the facts that role needs, in three tiers: **Important** (check before acting),
**Additional** (for this task) and **Background** (context, collapsed). Every value links to
the sentence it came from, shown highlighted in its letter. Facts the letters don't contain
show as "not documented", and contradictions between letters are flagged on the values they
affect. The record is read once, offline, by an LLM that must quote its source word for
word. At request time the model only *chooses* from facts already checked, and code verifies
every number, drug, side and date before it's shown. A chat bar answers follow-up questions
with sources. Results on the 5 challenge situations: 15/15 correct answers, 100% traceable
sources (278/278), 0 invented facts, 8 real contradictions found and no false alarms, first
response 1.8 s at worst.

## Tech used

Python 3.12, FastAPI, Pydantic; Vue 3, Vite, Chart.js; Claude via the Anthropic API (Claude
Haiku 5.5 for the fast screen and chat, Claude Opus 5.5 for the more thorough screen); Google
Gemini for offline fact extraction; FHIR R4 (fhir.resources, HL7 validator, ICD-10-GM / ATC /
LOINC codes); pydicom for DICOM images; Web Speech API for voice input; Synthea / MITRE
Coherent synthetic patients; pytest. Built with IBM Bob and Claude Code.

## Team contact email

TODO

## Where judges should look first

`README.md` (problem, solution, results), then `docs/evaluation.md` (metrics and the
contradictions found) and `docs/architecture.md` (pipeline and validation).