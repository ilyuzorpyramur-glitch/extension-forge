import json

import conveyor


row = conveyor.run_stand("aniyomi", "amedia")
steps = row.get("steps", [])
print("стенд amedia: %s/%s" % (row.get("passed", 0), len(steps)))
for step in steps:
    mark = "OK  " if step.get("ok") else "FAIL"
    value = step.get("value")
    note = step.get("note") or step.get("error") or ""
    print("  %s %-10s %s" % (mark, step.get("name", "?"), str(note)[:100]))
    if step.get("name") in ("video", "episodes") and value:
        print("      ", json.dumps(value, ensure_ascii=False)[:600])
