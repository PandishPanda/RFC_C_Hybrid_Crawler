"""StudyStream university crawler substrate.

Content-addressed snapshot store, artifact/renderer resolution, and the
mechanical provenance gate (ADR-0002) — architecture-neutral pieces shared
across extraction engines. The DEC-1 deterministic cascade + gated-Haiku-tail
engine that used to sit on top of this is archived on
archive/crawler-v2-dec1; RFC_C_Hybrid is testing a different engine here.
"""
