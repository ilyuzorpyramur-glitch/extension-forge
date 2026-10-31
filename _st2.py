import conveyor

for site in ("anilibria", "amedia"):
    row = conveyor.run_stand("aniyomi", site)
    steps = row.get("steps", [])
    print("== %s: %s/%s" % (site, row.get("passed", 0), len(steps)))
    for step in steps:
        name = step.get("name")
        if name in ("audio", "video", "order", "episodes", "status", "filters"):
            mark = "OK  " if step.get("ok") else "FAIL"
            print("   ", mark, name, str(step.get("value"))[:120])
