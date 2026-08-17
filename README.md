# RFC_C_Hybrid_Crawler

Extraction pipeline for Bulgarian university degree-program data
(tuition, admission, degree, duration, language), sourced from the RSVU
registry and university websites, with verbatim provenance on every
shipped value.

Design: a deterministic extraction cascade carries the structured share;
a gated LLM tail absorbs the prose share; one mechanical provenance gate
decides what ships. See CONTEXT.md and docs/adr/.

```bash
python3 -m unittest discover -s crawler/tests -p "test_*.py"
python3 -m crawler validate
```
