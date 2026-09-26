"""No response cache in the open-source kit: the event memo falls back to a
local SQLite file (config.GOOGLE_MEMO_SQLITE_PATH)."""
import json


def storage():
    return None


class PostgresResponseCache:
    @staticmethod
    def canonical(data):
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
