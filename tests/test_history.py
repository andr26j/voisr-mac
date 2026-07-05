from voisr.history import HistoryStore


def test_add_and_list(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("Hello world today", duration_s=2.0)
    items = h.list()
    assert len(items) == 1
    assert items[0]["text"] == "Hello world today"
    assert items[0]["words"] == 3
    assert "id" in items[0] and "ts" in items[0]


def test_list_newest_first(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("first", 1.0)
    h.add("second", 1.0)
    assert h.list()[0]["text"] == "second"


def test_delete(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("keep", 1.0)
    victim = h.add("remove", 1.0)["id"]
    h.delete(victim)
    assert [i["text"] for i in h.list()] == ["keep"]


def test_stats(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("one two three four five six", duration_s=3.0)  # 6 words in 3s = 120 wpm
    s = h.stats()
    assert s["total_words"] == 6
    assert s["dictations"] == 1
    assert s["avg_wpm"] == 120
    assert s["streak_days"] == 1


def test_corrupt_line_is_skipped(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("good", 1.0)
    with open(h.path, "a") as f:
        f.write('{"torn line\n')  # crash mid-write
    h.add("after", 1.0)
    assert [i["text"] for i in h.list()] == ["after", "good"]


def test_stats_empty(tmp_path):
    s = HistoryStore(tmp_path / "h.jsonl").stats()
    assert s == {"total_words": 0, "dictations": 0, "avg_wpm": 0, "streak_days": 0}
