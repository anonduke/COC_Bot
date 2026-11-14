import copy
import json
import os

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_raw(name="export_th18.json"):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def export_text(raw):
    return json.dumps(copy.deepcopy(raw))
