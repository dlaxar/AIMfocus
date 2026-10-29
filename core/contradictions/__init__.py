"""
contradictions – contradiction finder over the S0 passages (see README.md).

Rules first: deterministic detectors that need neither S1 facts nor an API key.
An optional LLM sweep adds candidates the rules cannot see; a judge must confirm them.
Output is the shared app.schemas.Contradiction list in cache/<patient>/contradictions.json.
"""
