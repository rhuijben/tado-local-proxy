import textwrap
from domoticz.plugin import merge_voicecontrol


def test_append_new_block():
    desc = ""
    new = {"room": "Living", "hide": "True"}
    out = merge_voicecontrol(desc, new)
    assert "<voicecontrol>" in out
    assert "room = Living" in out
    assert "hide = True" in out


def test_merge_existing_block_updates_and_preserves():
    desc = textwrap.dedent("""
    Some description text
    <voicecontrol>
      room = OldRoom
      custom = kept
    </voicecontrol>
    More trailing text
    """)
    new = {"room": "NewRoom", "uuid": "abc-123"}
    out = merge_voicecontrol(desc, new)
    assert "room = NewRoom" in out
    assert "custom = kept" in out
    assert "uuid = abc-123" in out
    assert "More trailing text" in out


def test_add_uuid_to_existing_block_without_affecting_other_lines():
    desc = "<voicecontrol>\n  room = Kitchen\n</voicecontrol>"
    new = {"uuid": "zone-uuid-1"}
    out = merge_voicecontrol(desc, new)
    assert "uuid = zone-uuid-1" in out
    assert "room = Kitchen" in out
