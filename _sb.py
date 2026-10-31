import conveyor
row = conveyor.run_stand("aniyomi", "anilibria")
steps = row.get("steps", [])
print("стенд anilibria: %s/%s" % (row.get("passed", 0), len(steps)))
for s in steps:
    if s.get("name") in ("audio", "video", "order", "episodes"):
        print("  ", "OK " if s.get("ok") else "FAIL", s.get("name"), str(s.get("value"))[:160])
