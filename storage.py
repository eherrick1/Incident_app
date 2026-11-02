"""Record + file storage with the capital/expense dual backend:

- Cloud (durable): Firestore for records/register, GCS for files.
  Active when GCS_BUCKET and FIRESTORE_COLLECTION are set.
- Local dev: SQLite + ./storage, no cloud credentials needed.

Records are stored as one JSON document per incident, including the audit
trail and language-guard findings, so both backends stay trivially in sync.
"""
import json
import secrets
import sqlite3
from datetime import datetime, timezone

import config


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    """INC-YYYYMMDD-<10 hex>.

    The suffix was 6 hex characters (~16.7M per day), which is enumerable, and
    the ID is quoted to guests as a reference number. Ten characters (~1.1e12
    per day) puts guessing out of reach. Still uppercase hex so it survives
    being read aloud over the phone, and still safe as a storage path segment.
    Existing IDs keep working — lookup is by exact string.
    """
    return ("INC-" + datetime.now(timezone.utc).strftime("%Y%m%d")
            + "-" + secrets.token_hex(5).upper())


# --------------------------------------------------------------------------- #
# Record stores
# --------------------------------------------------------------------------- #
class SQLiteStore:
    def __init__(self, path):
        self.path = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, status TEXT, data TEXT,
                created_at TEXT, updated_at TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS register (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT,
                row TEXT, created_at TEXT)""")

    def _conn(self):
        return sqlite3.connect(self.path)

    def save(self, doc):
        doc["updated_at"] = now_iso()
        with self._conn() as c:
            c.execute("""INSERT INTO records (id, status, data, created_at, updated_at)
                         VALUES (?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET
                           status=excluded.status, data=excluded.data,
                           updated_at=excluded.updated_at""",
                      (doc["id"], doc["status"], json.dumps(doc),
                       doc.get("created_at", now_iso()), doc["updated_at"]))
        return doc

    def get(self, incident_id):
        with self._conn() as c:
            row = c.execute("SELECT data FROM records WHERE id=?", (incident_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit=200):
        with self._conn() as c:
            rows = c.execute(
                "SELECT data FROM records ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def register_append(self, incident_id, row):
        with self._conn() as c:
            c.execute("INSERT INTO register (incident_id, row, created_at) VALUES (?,?,?)",
                      (incident_id, json.dumps(row), now_iso()))

    def register_rows(self):
        with self._conn() as c:
            rows = c.execute("SELECT row FROM register ORDER BY seq").fetchall()
        return [json.loads(r[0]) for r in rows]


class FirestoreStore:
    def __init__(self, collection):
        from google.cloud import firestore  # lazy import: cloud only
        self.db = firestore.Client()
        self.col = self.db.collection(collection)
        self.reg = self.db.collection(collection + "_register")

    def save(self, doc):
        doc["updated_at"] = now_iso()
        self.col.document(doc["id"]).set(doc)
        return doc

    def get(self, incident_id):
        snap = self.col.document(incident_id).get()
        return snap.to_dict() if snap.exists else None

    def list(self, limit=200):
        q = self.col.order_by("updated_at", direction="DESCENDING").limit(limit)
        return [d.to_dict() for d in q.stream()]

    def register_append(self, incident_id, row):
        self.reg.document().set(dict(incident_id=incident_id, row=row, created_at=now_iso()))

    def register_rows(self):
        return [d.to_dict()["row"] for d in self.reg.order_by("created_at").stream()]


# --------------------------------------------------------------------------- #
# File stores
# --------------------------------------------------------------------------- #
class LocalFiles:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, incident_id, name, data: bytes):
        d = self.root / "files" / incident_id
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        return f"{incident_id}/{name}"

    def get(self, ref) -> bytes:
        return (self.root / "files" / ref).read_bytes()

    def exists(self, ref):
        return (self.root / "files" / ref).exists()


class GCSFiles:
    def __init__(self, bucket):
        from google.cloud import storage as gcs  # lazy import: cloud only
        self.bucket = gcs.Client().bucket(bucket)

    def put(self, incident_id, name, data: bytes):
        ref = f"{incident_id}/{name}"
        self.bucket.blob(ref).upload_from_string(data)
        return ref

    def get(self, ref) -> bytes:
        return self.bucket.blob(ref).download_as_bytes()

    def exists(self, ref):
        return self.bucket.blob(ref).exists()


_store = _files = None


def get_store():
    global _store
    if _store is None:
        _store = (FirestoreStore(config.FIRESTORE_COLLECTION) if config.USE_CLOUD
                  else SQLiteStore(config.SQLITE_PATH))
    return _store


def get_files():
    global _files
    if _files is None:
        _files = GCSFiles(config.GCS_BUCKET) if config.USE_CLOUD else LocalFiles(config.LOCAL_STORAGE)
    return _files


# --------------------------------------------------------------------------- #
# Record shape + audit helpers
# --------------------------------------------------------------------------- #
def new_record(intake_method, fields=None, files=None, submitter=None):
    rec_id = new_id()
    doc = dict(
        id=rec_id,
        status="pending_review",       # pending_review | changes_requested | approved | rejected
        intake_method=intake_method,   # online_form | excel_upload | pdf_photo_upload
        fields=fields or {},
        original_narratives={},        # reporter's untouched words, preserved forever
        files=files or {},             # name -> storage ref (original/converted/outputs)
        language_findings=[],          # language-guard findings incl. reviewer decisions
        audit=[],
        submitter=submitter or {},
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    audit(doc, "submitted", actor=(submitter or {}).get("name") or "reporter",
          detail=f"intake via {intake_method}")
    return doc


def audit(doc, action, actor="system", detail="", extra=None):
    entry = dict(ts=now_iso(), action=action, actor=actor, detail=detail)
    if extra:
        entry.update(extra)
    doc.setdefault("audit", []).append(entry)
    return entry
