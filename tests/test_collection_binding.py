import pytest

from tools.xhs_library import podcast_segments, podcast_card


def listing(names=("First", "Second")):
    nodes = [{"ref": "container", "role": "AXGroup", "parent": None}]
    for index, name in enumerate(names):
        prefix = str(index)
        group, heading = prefix + "cover", prefix + "heading"
        nodes += [
            {"ref": group, "role": "AXGroup", "parent": "container"},
            {"ref": prefix + "image", "role": "AXImage", "title": name, "parent": group},
            {"ref": heading, "role": "AXHeading", "title": name, "parent": "container",
             "bounds": {"width": 100, "height": 20}},
            {"ref": prefix + "headingText", "role": "AXStaticText", "title": name, "parent": heading},
            {"ref": prefix + "desc", "role": "AXGroup", "parent": "container"},
            {"ref": prefix + "descText", "role": "AXStaticText", "title": "Description " + name,
             "parent": prefix + "desc"},
            {"ref": prefix + "count", "role": "AXGroup", "parent": "container"},
            {"ref": prefix + "countText", "role": "AXStaticText", "title": "共 " + str(index) + " 篇",
             "parent": prefix + "count"},
            {"ref": prefix + "edit", "role": "AXButton", "title": "", "parent": "container"},
        ]
    return {"web": nodes}


def test_flattened_collection_controls_never_cross_into_the_next_card():
    current = listing()
    records = podcast_segments(current)
    assert [(r["name"], r["note_count"]) for r in records] == [("First", 0), ("Second", 1)]
    first, _, count = podcast_card(current, "First", "Description First")
    refs = {n["ref"] for n in first["web"]}
    assert "0edit" in refs and "1edit" not in refs and "1cover" not in refs
    assert count == 0
    with pytest.raises(RuntimeError):
        podcast_card(current, "First", "Description Second")


def test_duplicate_collection_identity_is_rejected():
    with pytest.raises(RuntimeError, match="not_unique"):
        podcast_card(listing(("Same", "Same")), "Same", "Description Same")
