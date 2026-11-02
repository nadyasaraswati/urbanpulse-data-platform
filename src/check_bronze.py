import json, glob, collections

for kind in ["weather", "traffic"]:
    files = sorted(glob.glob(f"data/bronze/{kind}/**/*.jsonl", recursive=True))
    print(f"{kind}: {len(files)} file")
    for f in files[-2:]:
        rows = [json.loads(l) for l in open(f, encoding="utf-8")]
        c = collections.Counter(
            (r.get("scheduled_hour") or r.get("observation_time") or "?", r.get("http_status"))
            for r in rows
        )
        no_key = sum(1 for r in rows if "scheduled_hour" not in r)
        print(f, len(rows), f"| tanpa scheduled_hour: {no_key}")
        for k, v in sorted(c.items(), key=lambda x: str(x[0])):
            print("  ", k, v)