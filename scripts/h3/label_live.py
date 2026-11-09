"""Run the labelling CLI live. The CBORG listing endpoint (2026-11-06) returns bare `cborg-*` and provider-prefixed
ids only (no `lbl/` aliases), while the chat API still accepts the legacy `lbl/` aliases (probe_ids.py, 3 calls,
all accepted). The owner's hard rule is to send only the three `lbl/` ids, so the live listing is passed to the CLI
with exactly those three aliases appended when the listing is live. Everything else is the CLI's own code."""
import dataclasses, sys, time
from app.modules.model_catalog import list_cborg_models_live
from evals.binary.labeling import cli

ALIASES = ("lbl/cborg-chat", "lbl/cborg-deepthought", "lbl/gpt-oss-120b")

def lister():
    live = list_cborg_models_live()
    if not live.available:
        return live
    present = [a for a in ALIASES if a in live.models]
    print(f"# live listing: {len(live.models)} ids; lbl/ aliases already listed: {present or 'none'}; appending"
          f" {sorted(set(ALIASES) - set(present))} (accepted by the chat API in the probe)")
    return dataclasses.replace(live, models=tuple(sorted(set(live.models) | set(ALIASES))))

t = time.monotonic()
rc = cli.main(sys.argv[1:], lister=lister)
print(f"# labelling rc={rc} elapsed={time.monotonic()-t:.0f}s")
raise SystemExit(rc)
