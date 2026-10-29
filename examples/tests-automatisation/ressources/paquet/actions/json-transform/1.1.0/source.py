def run(inputs):
    data, fields = inputs["data"], inputs["fields"]
    picked = {k: data[k] for k in fields if k in data}
    return {"result": picked, "kept": len(picked), "dropped": len(data) - len(picked)}
