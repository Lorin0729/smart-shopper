"""SQLite 持久化：商品、价格历史、库存历史。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import (HealthState, InventorySnapshot, PricePoint, Product,
                     VariantSnapshot)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    display_id TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,
    pid        TEXT NOT NULL,
    url        TEXT NOT NULL,
    name       TEXT DEFAULT '',
    category   TEXT DEFAULT 'general',
    launch_date TEXT,
    target_price REAL,
    watched_variant TEXT DEFAULT '',
    added_at   REAL
);
CREATE TABLE IF NOT EXISTS prices (
    display_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    price      REAL NOT NULL,
    original_price REAL,
    PRIMARY KEY (display_id, ts)
);
CREATE TABLE IF NOT EXISTS inventories (
    display_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    in_stock   INTEGER NOT NULL,
    state      TEXT DEFAULT '',
    quantity   INTEGER,
    PRIMARY KEY (display_id, ts)
);
CREATE TABLE IF NOT EXISTS variants (
    display_id TEXT NOT NULL,
    variant    TEXT NOT NULL,
    ts         REAL NOT NULL,
    in_stock   INTEGER NOT NULL,
    state      TEXT DEFAULT '',
    quantity   INTEGER,
    PRIMARY KEY (display_id, variant, ts)
);
CREATE TABLE IF NOT EXISTS alert_log (
    display_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ts         REAL NOT NULL,
    PRIMARY KEY (display_id, kind)
);
CREATE TABLE IF NOT EXISTS health (
    display_id           TEXT PRIMARY KEY,
    consecutive_failures INTEGER DEFAULT 0,
    last_ok_ts           REAL,
    last_error           TEXT DEFAULT '',
    updated_ts           REAL
);
"""


class Store:
    def __init__(self, path: str | Path = "shopper.db"):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        """旧库升级：缺列/缺表则补。"""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(products)")}
        if "target_price" not in cols:
            self.conn.execute("ALTER TABLE products ADD COLUMN target_price REAL")
        if "watched_variant" not in cols:
            self.conn.execute(
                "ALTER TABLE products ADD COLUMN watched_variant TEXT DEFAULT ''"
            )

    # ---------- 商品 ----------
    def add_product(self, p: Product) -> bool:
        """新增商品，已存在则返回 False。"""
        try:
            self.conn.execute(
                "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p.display_id(), p.platform, p.pid, p.url, p.name,
                 p.category, p.launch_date, p.target_price,
                 p.watched_variant, p.added_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_product(self, p: Product) -> None:
        self.conn.execute(
            "UPDATE products SET name=?, category=?, launch_date=?, target_price=?, "
            "watched_variant=? WHERE display_id=?",
            (p.name, p.category, p.launch_date, p.target_price,
             p.watched_variant, p.display_id()),
        )
        self.conn.commit()

    def get_product(self, key: str) -> Product | None:
        """key 可为 display_id、URL 或序号前的 display_id 片段。"""
        row = self.conn.execute(
            "SELECT * FROM products WHERE display_id=? OR url=?", (key, key)
        ).fetchone()
        if not row:  # 宽松匹配，支持只输 pid
            row = self.conn.execute(
                "SELECT * FROM products WHERE pid=? LIMIT 1", (key,)
            ).fetchone()
        return self._row_to_product(row) if row else None

    def list_products(self) -> list[Product]:
        rows = self.conn.execute("SELECT * FROM products ORDER BY added_at").fetchall()
        return [self._row_to_product(r) for r in rows]

    def remove_product(self, key: str) -> bool:
        p = self.get_product(key)
        if not p:
            return False
        did = p.display_id()
        with self.conn:
            self.conn.execute("DELETE FROM products WHERE display_id=?", (did,))
            self.conn.execute("DELETE FROM prices WHERE display_id=?", (did,))
            self.conn.execute("DELETE FROM inventories WHERE display_id=?", (did,))
            self.conn.execute("DELETE FROM variants WHERE display_id=?", (did,))
            self.conn.execute("DELETE FROM alert_log WHERE display_id=?", (did,))
            self.conn.execute("DELETE FROM health WHERE display_id=?", (did,))
        return True

    @staticmethod
    def _row_to_product(r: sqlite3.Row) -> Product:
        return Product(
            platform=r["platform"], pid=r["pid"], url=r["url"], name=r["name"],
            category=r["category"], launch_date=r["launch_date"],
            target_price=r["target_price"],
            watched_variant=r["watched_variant"] or "", added_at=r["added_at"],
        )

    # ---------- 价格 ----------
    def add_price(self, display_id: str, point: PricePoint) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
            (display_id, point.ts, point.price, point.original_price),
        )
        self.conn.commit()

    def get_prices(self, display_id: str) -> list[PricePoint]:
        rows = self.conn.execute(
            "SELECT * FROM prices WHERE display_id=? ORDER BY ts", (display_id,)
        ).fetchall()
        return [PricePoint(r["ts"], r["price"], r["original_price"]) for r in rows]

    def last_price(self, display_id: str) -> PricePoint | None:
        pts = self.get_prices(display_id)
        return pts[-1] if pts else None

    def lowest_price(self, display_id: str) -> PricePoint | None:
        pts = self.get_prices(display_id)
        return min(pts, key=lambda x: x.price) if pts else None

    # ---------- 库存 ----------
    def add_inventory(self, display_id: str, snap: InventorySnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO inventories VALUES (?,?,?,?,?)",
            (display_id, snap.ts, int(snap.in_stock), snap.state, snap.quantity),
        )
        self.conn.commit()

    def last_inventory(self, display_id: str) -> InventorySnapshot | None:
        row = self.conn.execute(
            "SELECT * FROM inventories WHERE display_id=? ORDER BY ts DESC LIMIT 1",
            (display_id,),
        ).fetchone()
        if not row:
            return None
        return InventorySnapshot(row["ts"], bool(row["in_stock"]), row["state"], row["quantity"])

    # ---------- 尺码 / SKU 变体 ----------
    def add_variant(self, display_id: str, snap: VariantSnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?)",
            (display_id, snap.variant, snap.ts, int(snap.in_stock), snap.state, snap.quantity),
        )
        self.conn.commit()

    def add_variants(self, display_id: str, snaps: list[VariantSnapshot]) -> None:
        with self.conn:
            for s in snaps:
                self.conn.execute(
                    "INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?)",
                    (display_id, s.variant, s.ts, int(s.in_stock), s.state, s.quantity),
                )

    def latest_variants(self, display_id: str) -> list[VariantSnapshot]:
        """每个变体取最新一次采样。"""
        rows = self.conn.execute(
            "SELECT v.* FROM variants v JOIN ("
            "  SELECT variant, MAX(ts) AS mts FROM variants WHERE display_id=? GROUP BY variant"
            ") t ON v.variant=t.variant AND v.ts=t.mts WHERE v.display_id=? ORDER BY v.variant",
            (display_id, display_id),
        ).fetchall()
        return [
            VariantSnapshot(r["ts"], r["variant"], bool(r["in_stock"]), r["state"], r["quantity"])
            for r in rows
        ]

    def last_variant(self, display_id: str, variant: str) -> VariantSnapshot | None:
        row = self.conn.execute(
            "SELECT * FROM variants WHERE display_id=? AND variant=? ORDER BY ts DESC LIMIT 1",
            (display_id, variant),
        ).fetchone()
        if not row:
            return None
        return VariantSnapshot(row["ts"], row["variant"], bool(row["in_stock"]),
                               row["state"], row["quantity"])

    # ---------- 提醒冷却 ----------
    def last_alert(self, display_id: str, kind: str) -> float | None:
        row = self.conn.execute(
            "SELECT ts FROM alert_log WHERE display_id=? AND kind=?",
            (display_id, kind),
        ).fetchone()
        return row["ts"] if row else None

    def mark_alert(self, display_id: str, kind: str, ts: float | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO alert_log VALUES (?,?,?)",
            (display_id, kind, ts if ts is not None else time.time()),
        )
        self.conn.commit()

    # ---------- 抓取健康度 ----------
    def record_fetch(self, display_id: str, ok: bool, error: str = "") -> HealthState:
        now = time.time()
        cur = self.health(display_id)
        failures = 0 if ok else (cur.consecutive_failures if cur else 0) + 1
        last_ok = now if ok else (cur.last_ok_ts if cur else None)
        last_error = "" if ok else (error or "未知错误")[:200]
        self.conn.execute(
            "INSERT OR REPLACE INTO health VALUES (?,?,?,?,?)",
            (display_id, failures, last_ok, last_error, now),
        )
        self.conn.commit()
        return HealthState(display_id, failures, last_ok, last_error, now)

    def health(self, display_id: str) -> HealthState | None:
        row = self.conn.execute(
            "SELECT * FROM health WHERE display_id=?", (display_id,)
        ).fetchone()
        return self._row_to_health(row) if row else None

    def all_health(self) -> dict[str, HealthState]:
        rows = self.conn.execute("SELECT * FROM health").fetchall()
        return {r["display_id"]: self._row_to_health(r) for r in rows}

    @staticmethod
    def _row_to_health(r: sqlite3.Row) -> HealthState:
        return HealthState(
            display_id=r["display_id"],
            consecutive_failures=r["consecutive_failures"] or 0,
            last_ok_ts=r["last_ok_ts"],
            last_error=r["last_error"] or "",
            updated_ts=r["updated_ts"],
        )

    # ---------- 统计 ----------
    def summary(self) -> dict:
        prods = self.list_products()
        return {
            "products": len(prods),
            "price_points": self.conn.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"],
            "inventory_points": self.conn.execute("SELECT COUNT(*) c FROM inventories").fetchone()["c"],
            "variant_points": self.conn.execute("SELECT COUNT(*) c FROM variants").fetchone()["c"],
        }


def default_db_path(config: dict) -> str:
    return config.get("db", "shopper.db")


def now() -> float:
    return time.time()
