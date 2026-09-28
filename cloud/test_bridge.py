"""Unit tests for the Comfy Cloud bridge graph translation."""

import pytest

import bridge


def studio_graph(token="0123456789ab"):
    return {
        "6": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"prompt": "x"}},
        "12": {
            "class_type": "H3SaveVideo",
            "inputs": {
                "video": ["11", 0],
                "filename_prefix": "video/h3_studio_" + token,
            },
        },
        "20": {
            "class_type": "LoadImage",
            "inputs": {"image": "h3_studio_kf_" + "a" * 32 + ".png"},
        },
        "21": {
            "class_type": "LoadVideo",
            "inputs": {"file": "h3_studio_kf_" + "b" * 32 + ".mp4"},
        },
    }


def test_translate_swaps_save_node_and_media():
    graph = studio_graph()
    names = bridge.local_media_names(graph)
    uploaded = {names[0]: "cloud_a.png", names[1]: "cloud_b.mp4"}
    result, token = bridge.translate_graph(graph, uploaded)
    assert token == "0123456789ab"
    assert result["12"]["class_type"] == "SaveVideo"
    assert result["12"]["inputs"]["format"] == "auto"
    assert result["20"]["inputs"]["image"] == "cloud_a.png"
    assert result["21"]["inputs"]["file"] == "cloud_b.mp4"
    assert graph["12"]["class_type"] == "H3SaveVideo"


def test_translate_rejects_graph_without_studio_save():
    graph = studio_graph()
    del graph["12"]
    with pytest.raises(ValueError):
        bridge.translate_graph(graph, {})


def test_translate_rejects_foreign_prefix():
    graph = studio_graph()
    graph["12"]["inputs"]["filename_prefix"] = "../../etc/x"
    with pytest.raises(ValueError):
        bridge.translate_graph(graph, {})


def test_local_media_names_ignore_foreign_files():
    graph = {"1": {"class_type": "LoadImage", "inputs": {"image": "other.png"}}}
    assert bridge.local_media_names(graph) == []


def test_find_output_prefers_save_node_then_preview():
    item = {"filename": "abc.mp4", "subfolder": "video", "type": "output"}
    assert bridge.find_output({"12": {"images": [item]}}, None) == item
    preview = {"filename": "p.mp4", "nodeId": "12"}
    assert bridge.find_output({}, preview) == preview
    assert bridge.find_output({}, {"filename": "p.png", "nodeId": "9"}) is None


def test_enum_options_handles_list_and_combo_specs():
    info = {
        "A": {"input": {"required": {"f": [["x", "y"], {}]}}},
        "B": {"input": {"required": {"f": ["COMBO", {"options": ["z"]}]}}},
    }
    assert bridge.enum_options(info, "A", "f") == ["x", "y"]
    assert bridge.enum_options(info, "B", "f") == ["z"]
    assert bridge.enum_options(info, "C", "f") == []
