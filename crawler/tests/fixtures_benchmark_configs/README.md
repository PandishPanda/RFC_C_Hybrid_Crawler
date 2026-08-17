The FROZEN benchmark-four configs — the exact shapes spike A's acceptance
replay was audited against (captured 2026-08-17, from the commit before
VUM gained its 11 Bulgarian-page programs).

The acceptance is a frozen TUPLE: these configs + the spike-A cache +
the frozen answer key in test_replay.py. The living configs in
crawler/configs/ keep evolving (new programs, new pages); this copy does
not. Discovered the hard way: promoting 11 VUM programs made replay
fail loudly on cache misses, because the replay was implicitly assuming
the living configs never change.
