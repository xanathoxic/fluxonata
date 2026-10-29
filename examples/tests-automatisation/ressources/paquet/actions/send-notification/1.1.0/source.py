import time


def run(inputs):
    time.sleep(inputs.get("seconds", 2))
    print("notification simulée pour", inputs["recipient"])
    return {"delivered": True, "recipient": inputs["recipient"]}
