import time

from backend.cache import TTLCache


def test_set_and_get_returns_value(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    cache.set("key1", {"a": 1}, ttl_seconds=60)
    assert cache.get("key1") == {"a": 1}


def test_missing_key_returns_none(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    assert cache.get("nope") is None


def test_expired_entry_is_a_miss(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    cache.set("key1", "value", ttl_seconds=0.05)
    time.sleep(0.1)
    assert cache.get("key1") is None


def test_overwrite_updates_value_and_ttl(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    cache.set("key1", "old", ttl_seconds=60)
    cache.set("key1", "new", ttl_seconds=60)
    assert cache.get("key1") == "new"


def test_delete_removes_entry(tmp_path):
    cache = TTLCache(db_path=tmp_path / "cache.db")
    cache.set("key1", "value", ttl_seconds=60)
    cache.delete("key1")
    assert cache.get("key1") is None


def test_persists_across_instances(tmp_path):
    db_path = tmp_path / "cache.db"
    cache1 = TTLCache(db_path=db_path)
    cache1.set("key1", "value", ttl_seconds=60)
    cache1.close()

    cache2 = TTLCache(db_path=db_path)
    assert cache2.get("key1") == "value"
