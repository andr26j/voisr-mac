import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path


class HistoryStore:
    """Append-only JSONL store of dictations with derived usage stats."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def _write(self, items: list[dict]) -> None:
        self.path.write_text("".join(json.dumps(i) + "\n" for i in items))

    def add(self, text: str, duration_s: float) -> dict:
        item = {"id": uuid.uuid4().hex, "ts": time.time(),
                "text": text, "words": len(text.split()), "duration_s": duration_s}
        items = self._read()
        items.append(item)
        self._write(items)
        return item

    def list(self) -> list[dict]:
        return sorted(self._read(), key=lambda i: i["ts"], reverse=True)

    def delete(self, item_id: str) -> None:
        self._write([i for i in self._read() if i["id"] != item_id])

    def stats(self) -> dict:
        items = self._read()
        if not items:
            return {"total_words": 0, "dictations": 0, "avg_wpm": 0, "streak_days": 0}
        total_words = sum(i["words"] for i in items)
        total_time = sum(i["duration_s"] for i in items) or 1e-9
        days = {date.fromtimestamp(i["ts"]) for i in items}
        streak, d = 0, date.today()
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        return {"total_words": total_words, "dictations": len(items),
                "avg_wpm": round(total_words / (total_time / 60)),
                "streak_days": streak}
