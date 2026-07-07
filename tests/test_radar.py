"""RadarModel: rolling aggregation of live UWB events for the dashboard."""

from uwb_explorer.radar import RadarModel
from uwb_explorer.parser import parse_line


def feed(model, line):
    model.ingest(parse_line(line))


def test_ranging_updates_contact_distance_and_history():
    m = RadarModel()
    feed(m, '{"Block":0,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":50}]}')
    feed(m, '{"Block":1,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":60}]}')
    c = m.contacts["0x0001"]
    assert c.last_distance_cm == 60
    assert c.samples == 2
    assert list(c.distance_history)[-2:] == [50, 60]


def test_error_status_increments_miss_not_distance():
    m = RadarModel()
    feed(m, '{"Block":0,"results":[{"Addr":"0x0001","Status":"Err"}]}')
    c = m.contacts["0x0001"]
    assert c.last_distance_cm is None
    assert c.misses == 1


def test_listener_frames_counted_and_classified_by_src():
    m = RadarModel()
    # Data frame, short src 0x0002 present
    feed(m, 'JS00EF{"LSTN":[41,88,42,ca,de,01,00,02,00],"TS":"0x1","O":1,'
            '"rsl":-70.5}')
    assert m.frame_count == 1
    assert m.last_rssi_dbm == -70.5
    # a sniffed source address becomes a passive contact
    assert "0x0002" in m.contacts
    assert m.contacts["0x0002"].passive is True


def test_active_ranging_contact_not_marked_passive():
    m = RadarModel()
    feed(m, '{"Block":0,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":50}]}')
    assert m.contacts["0x0001"].passive is False


def test_distance_history_is_bounded():
    m = RadarModel(history=8)
    for i in range(20):
        feed(m, f'{{"Block":{i},"results":[{{"Addr":"0x0001","Status":"Ok","D_cm":{i}}}]}}')
    assert len(m.contacts["0x0001"].distance_history) == 8


def test_ingest_none_is_ignored():
    m = RadarModel()
    m.ingest(None)
    assert m.frame_count == 0
    assert m.contacts == {}


def test_stats_summary():
    m = RadarModel()
    feed(m, '{"Block":0,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":50}]}')
    feed(m, 'JS00EF{"LSTN":[41,88],"TS":"0x1","O":1}')
    s = m.stats()
    assert s["contacts"] == 1  # 0x0001 active; the 2-byte frame has no src
    assert s["frames"] == 1
    assert s["ranges"] == 1
