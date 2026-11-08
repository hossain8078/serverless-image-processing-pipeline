"""Structural checks for the Step Functions definition."""

import json
import os

ASL_PATH = os.path.join(os.path.dirname(__file__), "..", "statemachine", "image_processing.asl.json")


def load():
    with open(ASL_PATH) as f:
        return json.load(f)


def test_required_state_types_exist():
    states = load()["States"]
    types = {s["Type"] for s in states.values()}
    assert {"Task", "Choice", "Succeed", "Fail"} <= types


def test_every_next_points_to_a_real_state():
    definition = load()
    states = definition["States"]
    assert definition["StartAt"] in states
    for state in states.values():
        targets = [state.get("Next"), state.get("Default")]
        targets += [c["Next"] for c in state.get("Choices", [])]
        targets += [c["Next"] for c in state.get("Catch", [])]
        for target in filter(None, targets):
            assert target in states, f"Unknown state {target}"


def test_task_invokes_resize_lambda():
    task = load()["States"]["ResizeImage"]
    assert task["Resource"] == "arn:aws:states:::lambda:invoke"
    assert task["Parameters"]["FunctionName"] == "${ResizeFunctionArn}"
