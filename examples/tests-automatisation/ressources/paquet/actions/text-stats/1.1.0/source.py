def run(inputs):
    text = inputs["text"]
    words = [w for w in text.split() if w]
    print("analyse de", len(text), "caractères")  # logs go to stderr, never to the result
    return {
        "characters": len(text),
        "words": len(words),
        "longest_word": max(words, key=len) if words else "",
        "uppercase": text.upper() if inputs.get("shout") else None,
    }
