import json

from dmux.connectors import IncrementalJsonlReader, JsonCache, tail_log


def test_incremental_jsonl_connector_commits_only_newline_terminated_objects(tmp_path):
    source = tmp_path / "metrics.jsonl"
    source.write_bytes(b'{"step": 1}\n{"step"')
    reader = IncrementalJsonlReader(source)
    records, reset = reader.read()
    assert reset and records == [{"step": 1}] and reader.pending

    with source.open("ab") as handle:
        handle.write(b': 2}\nnot-json\n')
    records, reset = reader.read()
    assert not reset and records == [{"step": 2}]
    assert reader.malformed == 1 and not reader.pending


def test_log_connector_is_bounded_sanitized_and_read_only(tmp_path):
    source = tmp_path / "train.log"
    source.write_text("\x1b[31mloss=2.0\x1b[0m\nloss=1.0\n", encoding="utf-8")
    before = source.read_bytes()
    assert tail_log(source, n=1) == ["loss=1.0"]
    assert source.read_bytes() == before


def test_json_and_jsonl_reads_have_explicit_size_bounds(tmp_path):
    document = tmp_path / "status.json"
    document.write_text(json.dumps({"payload": "x" * 100}), encoding="utf-8")
    cache = JsonCache(max_bytes=16)
    assert cache.read(document, {"fallback": True}) == {"fallback": True}
    assert document in cache.warnings

    stream = tmp_path / "metrics.jsonl"
    stream.write_text("".join(json.dumps({"step": step}) + "\n" for step in range(20)))
    reader = IncrementalJsonlReader(stream, max_bytes_per_read=32)
    first, _ = reader.read()
    assert first and reader.offset <= 32 and reader.offset < stream.stat().st_size
    records = list(first)
    while reader.offset < stream.stat().st_size:
        batch, _ = reader.read()
        records.extend(batch)
    assert [row["step"] for row in records] == list(range(20))
