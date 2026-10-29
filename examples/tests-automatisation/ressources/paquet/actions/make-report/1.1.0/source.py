def run(inputs):
    lines = "\n".join(f"- {line}" for line in inputs["lines"])
    body = f"# {inputs['title']}\n\n{lines}\n"
    return {"lines": len(inputs["lines"]), "__artifacts__": [{"name": "rapport.md", "content_type": "text/markdown", "text": body}]}
