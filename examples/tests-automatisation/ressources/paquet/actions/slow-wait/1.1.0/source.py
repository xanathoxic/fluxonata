import time


def run(inputs):
    for i in range(inputs["seconds"]):
        print("étape", i + 1)
        time.sleep(1)
    return {"slept": inputs["seconds"]}
