"""Static game data, decoded from the game's own CSV tables.

The installed game ships its balance tables in
split_install_time_asset_pack.apk under assets/logic/*.csv. Each file is a
"Sig:" header plus a 64-byte signature, then LZMA data whose header carries a
4-byte (not the standard 8-byte) uncompressed size.

Table layout: row 0 = column names, row 1 = column types, then data. A row
with a Name starts a new object; following rows with an empty Name are that
object's further levels. The IDs used in the in-game data export are the GlobalID column where a
table has one (buildings, characters, spells, traps; these are NOT in row
order), otherwise
    class_id * 1_000_000 + (index of the object among the named rows)
so e.g. the first named row of heroes.csv is 28000000 (Barbarian King).

Decoded tables are cached per game version under data/static/<version>/ and
are not committed (they are Supercell's data; regenerate from the device).
"""
import csv
import io
import json
import lzma
import os
import zipfile
from dataclasses import dataclass, field

from . import adb

# Export ID class -> table name. Verified against a live TH18 export
# (2026-11-14, game 18.647.5): every ID in these classes resolves.
TABLES = {
    1: "buildings",
    4: "characters",          # units and siege machines
    8: "obstacles",
    12: "traps",
    26: "spells",
    28: "heroes",
    52: "skins",
    73: "pets",
    90: "character_items",    # hero equipment
    93: "villager_apprentices",  # helpers (Builder's Apprentice, Lab Assistant...)
    102: "seasonal_defense_modules",
    103: "seasonal_defense_archetypes",
    107: "guardians",
}
EXTRA_TABLES = ("townhall_levels",)  # needed, but not addressed by export IDs

ASSET_PACK = "split_install_time_asset_pack.apk"
DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "static")

_SIG_LEN = 4 + 64  # b"Sig:" + signature


def decode_csv(raw: bytes) -> str:
    """Decode one assets/logic/*.csv file to text."""
    if raw.startswith(b"Sig:"):
        raw = raw[_SIG_LEN:]
    if raw.startswith(b'"'):  # some builds ship plain text
        return raw.decode("utf-8")
    # 5-byte LZMA props + 4-byte size -> pad the size to the 8 bytes the
    # "alone" format expects.
    data = raw[:9] + b"\x00" * 4 + raw[9:]
    return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(data).decode("utf-8")


@dataclass
class GameObject:
    id: int
    table: str
    name: str                       # internal name, e.g. "BuilderApprentice"
    rows: list = field(repr=False)  # rows[0] is level 1; dicts keyed by column
    display_name: str = ""          # English in-game name, e.g. "Builder's Apprentice"

    @property
    def max_level(self):
        return len(self.rows)

    def level(self, lvl):
        """Row for level `lvl` (1-based), or None if out of range."""
        return self.rows[lvl - 1] if 1 <= lvl <= len(self.rows) else None


def parse_table(text, table, class_id):
    """Parse a decoded table into {id: GameObject}."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    next(reader)  # types row
    objects, current = {}, None
    for row in reader:
        if not row:
            continue
        rec = dict(zip(header, row))
        name = rec.get("Name", "")
        if name:
            # Tables with a GlobalID column (buildings, characters, spells,
            # traps) are not in ID order; the others are.
            gid = rec.get("GlobalID")
            oid = int(gid) if gid else class_id * 1_000_000 + len(objects)
            current = GameObject(oid, table, name, [])
            objects[oid] = current
        if current is not None:
            current.rows.append(rec)
    return objects


def parse_rows(text):
    """Parse a decoded table into a plain list of row dicts (no grouping)."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    next(reader)
    return [dict(zip(header, r)) for r in reader if r]


class GameData:
    def __init__(self, objects, townhall_levels, version):
        self.objects = objects
        self.townhall_levels = townhall_levels  # list of row dicts, index 0 = TH1
        self.version = version

    def get(self, data_id):
        return self.objects.get(data_id)

    def name(self, data_id):
        """English display name, falling back to the internal name, then the ID."""
        obj = self.objects.get(data_id)
        if obj is None:
            return f"#{data_id}"
        return obj.display_name or obj.name

    @classmethod
    def load(cls, directory):
        with open(os.path.join(directory, "version.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        texts = {}
        path = os.path.join(directory, "texts.csv")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                texts = {r["TID"]: r["EN"] for r in parse_rows(f.read())}
        objects = {}
        for class_id, table in TABLES.items():
            path = os.path.join(directory, table + ".csv")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    objects.update(parse_table(f.read(), table, class_id))
        for obj in objects.values():
            obj.display_name = texts.get(obj.rows[0].get("TID", ""), "")
        th = []
        path = os.path.join(directory, "townhall_levels.csv")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                th = parse_rows(f.read())
        return cls(objects, th, version)


def extract_from_apk(apk_path, out_dir, version):
    """Decode the tables we use from an asset-pack APK into out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(apk_path) as z:
        for table in (*TABLES.values(), *EXTRA_TABLES):
            text = decode_csv(z.read(f"assets/logic/{table}.csv"))
            with open(os.path.join(out_dir, table + ".csv"), "w", encoding="utf-8",
                      newline="") as f:
                f.write(text)
        # English strings: texts_patch.csv overrides texts.csv.
        texts = {}
        for name in ("texts.csv", "texts_patch.csv"):
            entry = f"assets/localization/{name}"
            if entry in z.namelist():
                for r in parse_rows(decode_csv(z.read(entry))):
                    if r.get("TID") and r.get("EN"):
                        texts[r["TID"]] = r["EN"]
    with open(os.path.join(out_dir, "texts.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["TID", "EN"])
        w.writerow(["string", "string"])
        w.writerows(sorted(texts.items()))
    # version.json last: its presence marks a complete extraction.
    with open(os.path.join(out_dir, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"version": version}, f)


def static_dir(version, root=DEFAULT_ROOT):
    return os.path.join(root, version or "")


def ensure_static_data(root=DEFAULT_ROOT, serial=adb.SERIAL):
    """Return GameData for the installed game version, pulling and decoding the
    tables from the device the first time a version is seen (~650 MB pull,
    about a minute)."""
    version = adb.game_version(serial)
    out_dir = static_dir(version, root)
    if not os.path.exists(os.path.join(out_dir, "version.json")):
        paths = [line.split(":", 1)[1].strip()
                 for line in adb.shell(f"pm path {adb.COC_PACKAGE}", serial=serial).splitlines()
                 if line.startswith("package:")]
        src = next((p for p in paths if p.endswith("/" + ASSET_PACK)), None)
        if src is None:
            raise adb.AdbError(f"{ASSET_PACK} not found in: {paths}")
        os.makedirs(root, exist_ok=True)
        tmp = os.path.join(root, f"_{version}_{ASSET_PACK}")
        try:
            adb.run("pull", src, tmp, timeout=600, serial=serial)
            extract_from_apk(tmp, out_dir, version)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return GameData.load(out_dir)
