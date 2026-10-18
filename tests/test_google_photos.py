"""Tests hors ligne du module google_photos (HTTP simulé, aucun appel réseau)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import piexif
import pytest
from PIL import Image

from photoselect import google_photos as gp


# ---------------------------------------------------------------- utilitaires
def _jpeg(path: Path, gps: tuple[float, float] | None = None) -> Path:
    img = Image.new("RGB", (32, 24), (200, 120, 40))
    buf = io.BytesIO()
    if gps:
        exif = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: gp._to_dms(gps[0]),
            piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: gp._to_dms(gps[1]),
        }}
        img.save(buf, "JPEG", exif=piexif.dump(exif))
    else:
        img.save(buf, "JPEG")
    path.write_bytes(buf.getvalue())
    return path


def _read_gps(data: bytes) -> tuple[float, float] | None:
    g = piexif.load(data).get("GPS") or {}
    if piexif.GPSIFD.GPSLatitude not in g:
        return None

    def dec(v, ref):
        d, m, s = (a / b for a, b in v)
        x = d + m / 60 + s / 3600
        return -x if ref in (b"S", b"W") else x

    return (dec(g[piexif.GPSIFD.GPSLatitude], g[piexif.GPSIFD.GPSLatitudeRef]),
            dec(g[piexif.GPSIFD.GPSLongitude], g[piexif.GPSIFD.GPSLongitudeRef]))


class Resp:
    def __init__(self, status: int = 200, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else "")

    def json(self):
        return self._body


class FakeSession:
    """Simule l'API : uploads -> jeton, albums, batchCreate. `fail_plan` force des codes HTTP."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.albums: dict[str, dict] = {}
        self.fail_plan: list[int] = []
        self.item_errors: set[str] = set()  # noms de fichiers rejetés par batchCreate
        self._n = 0

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.fail_plan:
            return Resp(self.fail_plan.pop(0), {"error": {"status": "X", "message": "simulé"}})
        if url.endswith("/uploads"):
            self._n += 1
            return Resp(200, text=f"tok{self._n}")
        if url.endswith("/albums") and method == "POST":
            aid = f"alb{len(self.albums) + 1}"
            self.albums[aid] = {"id": aid, "productUrl": f"https://photos.google.com/lr/album/{aid}"}
            return Resp(200, self.albums[aid])
        if "/albums/" in url and method == "GET":
            aid = url.rsplit("/", 1)[1]
            if aid in self.albums:
                return Resp(200, self.albums[aid])
            return Resp(404, {"error": {"status": "NOT_FOUND", "message": "absent"}})
        if url.endswith("mediaItems:batchCreate"):
            res = []
            for it in kw["json"]["newMediaItems"]:
                s = it["simpleMediaItem"]
                if s["fileName"] in self.item_errors:
                    res.append({"uploadToken": s["uploadToken"], "status": {"code": 3, "message": "refusé"}})
                else:
                    res.append({"uploadToken": s["uploadToken"], "status": {"message": "Success"},
                                "mediaItem": {"id": "m_" + s["uploadToken"]}})
            return Resp(200, {"newMediaItemResults": res})
        return Resp(404, {"error": {"status": "NOT_FOUND", "message": url}})


@pytest.fixture
def env(tmp_path):
    sess = FakeSession()
    client = gp.GooglePhotosClient(sess, sleep=lambda s: None)
    ledger = gp.UploadLedger(tmp_path / "ledger.db")
    return sess, client, ledger


# ---------------------------------------------------------------- GPS
def test_keep_injects_db_gps_when_file_has_none(tmp_path):
    p = _jpeg(tmp_path / "a.jpg")
    data = gp.prepare_upload_bytes(p, gp.GPS_KEEP, 44.7518, -5.0221)
    lat, lon = _read_gps(data)
    assert lat == pytest.approx(44.7518, abs=1e-5) and lon == pytest.approx(-5.0221, abs=1e-5)
    assert _read_gps(p.read_bytes()) is None  # original intact


def test_keep_preserves_existing_file_gps(tmp_path):
    p = _jpeg(tmp_path / "a.jpg", gps=(10.0, 20.0))
    data = gp.prepare_upload_bytes(p, gp.GPS_KEEP, 44.0, 5.0)
    assert data == p.read_bytes()


def test_keep_ignores_invalid_coords(tmp_path):
    p = _jpeg(tmp_path / "a.jpg")
    for lat, lon in [(None, None), (0, 0), (95, 5), ("x", 1)]:
        assert gp.prepare_upload_bytes(p, gp.GPS_KEEP, lat, lon) == p.read_bytes()


def test_strip_removes_gps_without_touching_original(tmp_path):
    p = _jpeg(tmp_path / "a.jpg", gps=(44.75, 5.02))
    before = p.read_bytes()
    data = gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert _read_gps(data) is None
    assert p.read_bytes() == before
    Image.open(io.BytesIO(data)).verify()


def test_strip_refuses_non_jpeg(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(p)
    with pytest.raises(gp.GooglePhotosError):
        gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert gp.prepare_upload_bytes(p, gp.GPS_KEEP, 1, 1) == p.read_bytes()


def test_description():
    assert gp.build_description("France", "Auvergne-Rhône-Alpes", "Crest") == "Crest, Auvergne-Rhône-Alpes, France"
    assert gp.build_description("Monaco", "Monaco", "") == "Monaco"
    assert gp.build_description(None, "", None) == ""


# ---------------------------------------------------------------- envoi
def test_upload_creates_album_and_items(env, tmp_path):
    sess, client, ledger = env
    items = [gp.UploadItem(_jpeg(tmp_path / f"p{i}.jpg"), 44.7, 5.0, "Crest") for i in range(3)]
    seen = []
    rep = gp.upload_items(client, ledger, items, "Vacances", progress=lambda d, t, n: seen.append((d, t)))
    assert rep.uploaded == 3 and not rep.failed and rep.album_id == "alb1"
    assert rep.album_url.endswith("/alb1")
    bc = [c for c in sess.calls if c[1].endswith("batchCreate")]
    assert len(bc) == 1 and bc[0][2]["json"]["albumId"] == "alb1"
    assert bc[0][2]["json"]["newMediaItems"][0]["description"] == "Crest"
    assert seen[-1] == (3, 3)


def test_second_run_skips_and_reuses_album(env, tmp_path):
    sess, client, ledger = env
    items = [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))]
    gp.upload_items(client, ledger, items, "A")
    rep = gp.upload_items(client, ledger, items, "A")
    assert rep.skipped_already == 1 and rep.uploaded == 0
    assert len(sess.albums) == 1
    # gps_mode différent = autre version du fichier -> renvoyée
    rep = gp.upload_items(client, ledger, items, "A", gps_mode=gp.GPS_STRIP)
    assert rep.uploaded == 1


def test_album_recreated_if_deleted_remotely(env, tmp_path):
    sess, client, ledger = env
    gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))], "A")
    sess.albums.clear()
    rep = gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "q.jpg"))], "A")
    assert rep.album_id == "alb1" and rep.uploaded == 1  # recréé (compteur repart)
    assert ledger.get_album("A")["id"] == "alb1"


def test_batches_of_50(env, tmp_path):
    sess, client, ledger = env
    src = _jpeg(tmp_path / "base.jpg").read_bytes()
    items = []
    for i in range(120):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(src)
        items.append(gp.UploadItem(p))
    rep = gp.upload_items(client, ledger, items, "Gros")
    sizes = [len(c[2]["json"]["newMediaItems"]) for c in sess.calls if c[1].endswith("batchCreate")]
    assert sizes == [50, 50, 20] and rep.uploaded == 120


def test_partial_failures_and_unsupported(env, tmp_path):
    sess, client, ledger = env
    sess.item_errors = {"bad.jpg"}
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    items = [gp.UploadItem(_jpeg(tmp_path / "ok.jpg")), gp.UploadItem(_jpeg(tmp_path / "bad.jpg")),
             gp.UploadItem(txt), gp.UploadItem(tmp_path / "absent.jpg")]
    rep = gp.upload_items(client, ledger, items, "A")
    assert rep.uploaded == 1 and rep.skipped_unsupported == 1
    assert {Path(f).name for f, _ in rep.failed} == {"bad.jpg", "absent.jpg"}
    # le refusé n'est pas marqué comme envoyé
    assert gp.upload_items(client, ledger, items[1:2], "A").uploaded == 0  # toujours refusé
    assert not ledger.is_uploaded(items[1].path, gp.GPS_KEEP, "alb1")


def test_retry_on_429_then_success(env, tmp_path):
    sess, client, ledger = env
    sess.fail_plan = [429, 503]
    rep = gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))], "A")
    assert rep.uploaded == 1


def test_non_retryable_error_is_reported_without_crash(env, tmp_path):
    sess, client, ledger = env
    orig = sess.request

    def req(method, url, **kw):
        if url.endswith("/uploads"):
            return Resp(400, {"error": {"status": "INVALID_ARGUMENT", "message": "simulé"}})
        return orig(method, url, **kw)

    sess.request = req
    rep = gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))], "A")
    assert rep.uploaded == 0 and "HTTP 400" in rep.failed[0][1]


def test_cancel_stops_and_flushes(env, tmp_path):
    import threading

    _sess, client, ledger = env
    ev = threading.Event()
    items = [gp.UploadItem(_jpeg(tmp_path / f"p{i}.jpg")) for i in range(5)]

    def prog(done, total, name):
        if done == 2:
            ev.set()

    rep = gp.upload_items(client, ledger, items, "A", progress=prog, cancel=ev)
    assert rep.cancelled and rep.uploaded == 3


# ---------------------------------------------------------------- secrets
def test_client_config_from_store_and_errors(monkeypatch):
    monkeypatch.delenv("COFFRE_PHOTOSELECT__GOOGLE_PHOTOS_CLIENT", raising=False)
    store = gp.MemorySecretStore()
    with pytest.raises(gp.GooglePhotosError) as e:
        gp.load_client_config(store)
    assert "coffre definir photoselect/google-photos-client" in str(e.value)

    store.write("coffre:photoselect/google-photos-client", "pas du json SECRETVALUE")
    with pytest.raises(gp.GooglePhotosError) as e:
        gp.load_client_config(store)
    assert "SECRETVALUE" not in str(e.value)

    store.write("coffre:photoselect/google-photos-client",
                json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}))
    assert gp.load_client_config(store)["installed"]["client_id"] == "id"


def test_env_fallback(monkeypatch):
    assert gp._env_name("photoselect/google-photos-client") == "COFFRE_PHOTOSELECT__GOOGLE_PHOTOS_CLIENT"
    monkeypatch.setenv("COFFRE_PHOTOSELECT__GOOGLE_PHOTOS_CLIENT",
                       json.dumps({"web": {"client_id": "i", "client_secret": "s"}}))
    assert "installed" in gp.load_client_config(gp.MemorySecretStore())


def test_stored_token_rebuilds_credentials():
    store = gp.MemorySecretStore({gp.TOKEN_TARGET: json.dumps({"refresh_token": "r", "scopes": gp.SCOPES})})
    cfg = {"installed": {"client_id": "i", "client_secret": "s"}}
    creds = gp._credentials_from_store(cfg, store)
    assert creds.refresh_token == "r" and creds.client_id == "i"
    store.write(gp.TOKEN_TARGET, json.dumps({"refresh_token": "r", "scopes": gp.SCOPES[:1]}))
    assert gp._credentials_from_store(cfg, store) is None  # scopes insuffisants -> reconsentement


# ---------------------------------------------------------------- corrections relecture (18/10/2026)
def _with_xmp(jpeg: bytes, payload: bytes) -> bytes:
    body = b"http://ns.adobe.com/xap/1.0/\x00" + payload
    seg = b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    return jpeg[:2] + seg + jpeg[2:]


def test_strip_removes_xmp_gps(tmp_path):
    xmp = b'<x:xmpmeta><rdf:Description exif:GPSLatitude="44,45.1N" exif:GPSLongitude="5,1.2E"/></x:xmpmeta>'
    p = tmp_path / "lr.jpg"
    p.write_bytes(_with_xmp(_jpeg(tmp_path / "a.jpg", gps=(44.75, 5.02)).read_bytes(), xmp))
    data = gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert b"GPSLatitude" not in data and _read_gps(data) is None
    Image.open(io.BytesIO(data)).verify()
    # sans GPS EXIF mais avec XMP : le XMP part quand même
    q = tmp_path / "xmp_only.jpg"
    q.write_bytes(_with_xmp(_jpeg(tmp_path / "b.jpg").read_bytes(), xmp))
    assert b"GPSLatitude" not in gp.prepare_upload_bytes(q, gp.GPS_STRIP)
    # keep : XMP intact
    assert b"GPSLatitude" in gp.prepare_upload_bytes(q, gp.GPS_KEEP)


def test_strip_refuses_unreadable_exif(tmp_path, monkeypatch):
    p = _jpeg(tmp_path / "a.jpg", gps=(44.75, 5.02))

    def boom(_data):
        raise ValueError("EXIF corrompu")

    monkeypatch.setattr(piexif, "load", boom)
    with pytest.raises(gp.GooglePhotosError):
        gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert gp.prepare_upload_bytes(p, gp.GPS_KEEP, 1, 1) == p.read_bytes()


def test_batch_create_500_not_retried(env, tmp_path):
    sess, client, ledger = env
    orig = sess.request

    def req(method, url, **kw):
        if url.endswith("batchCreate"):
            sess.calls.append((method, url, kw))
            return Resp(500, {"error": {"status": "INTERNAL", "message": "simulé"}})
        return orig(method, url, **kw)

    sess.request = req
    rep = gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))], "A")
    assert rep.uploaded == 0 and len(rep.failed) == 1
    assert len([c for c in sess.calls if c[1].endswith("batchCreate")]) == 1


def test_fatal_403_aborts_whole_upload(env, tmp_path):
    sess, client, ledger = env
    orig = sess.request

    def req(method, url, **kw):
        if url.endswith("/uploads"):
            sess.calls.append((method, url, kw))
            return Resp(403, {"error": {"status": "PERMISSION_DENIED", "message": "quota"}})
        return orig(method, url, **kw)

    sess.request = req
    items = [gp.UploadItem(_jpeg(tmp_path / f"p{i}.jpg")) for i in range(5)]
    rep = gp.upload_items(client, ledger, items, "A")
    assert rep.aborted and len(rep.failed) == 1
    assert len([c for c in sess.calls if c[1].endswith("/uploads")]) == 1


def test_refresh_error_during_upload_not_retried(env, tmp_path):
    sess, client, ledger = env
    orig = sess.request
    n = {"uploads": 0}

    def req(method, url, **kw):
        if url.endswith("/uploads"):
            n["uploads"] += 1
            raise gp.RefreshError("invalid_grant")
        return orig(method, url, **kw)

    sess.request = req
    rep = gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / f"p{i}.jpg")) for i in range(3)], "A")
    assert n["uploads"] == 1 and rep.aborted


def test_cancel_interrupts_retries(env, tmp_path):
    import threading

    sess, client, ledger = env
    ev = threading.Event()
    sleeps = []
    client._sleep = lambda s: (sleeps.append(s), ev.set())
    sess.fail_plan = [503] * 2  # albums POST : 503 puis annulation pendant l'attente
    with pytest.raises(gp.GooglePhotosError):
        gp.upload_items(client, ledger, [gp.UploadItem(_jpeg(tmp_path / "p.jpg"))], "A", cancel=ev)
    assert len(sleeps) == 1


def _fake_auth_env(monkeypatch, refresh_exc):
    class Creds:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def refresh(self, _req):
            raise refresh_exc

    monkeypatch.setattr(gp, "Credentials", Creds)
    monkeypatch.setattr(gp, "Request", lambda: None)
    monkeypatch.setattr(gp, "dependencies_error", lambda: None)
    cfg = json.dumps({"installed": {"client_id": "i", "client_secret": "s"}})
    return gp.MemorySecretStore({
        "coffre:photoselect/google-photos-client": cfg,
        gp.TOKEN_TARGET: json.dumps({"refresh_token": "r", "scopes": gp.SCOPES}),
    })


def test_network_error_on_refresh_keeps_token(monkeypatch):
    store = _fake_auth_env(monkeypatch, ConnectionError("hors ligne"))
    with pytest.raises(gp.GooglePhotosError) as e:
        gp.authenticate(store, interactive=False)
    assert "Internet" in str(e.value)
    assert store.read(gp.TOKEN_TARGET) is not None


def test_revoked_token_is_forgotten(monkeypatch):
    store = _fake_auth_env(monkeypatch, gp.RefreshError("invalid_grant"))
    with pytest.raises(gp.GooglePhotosError):
        gp.authenticate(store, interactive=False)
    assert store.read(gp.TOKEN_TARGET) is None


def test_consent_requests_refresh_token(monkeypatch):
    store = _fake_auth_env(monkeypatch, gp.RefreshError("invalid_grant"))
    seen = {}

    class Flow:
        @classmethod
        def from_client_config(cls, _cfg, _scopes):
            return cls()

        def run_local_server(self, **kw):
            seen.update(kw)
            return type("C", (), {"refresh_token": "nouveau", "scopes": gp.SCOPES})()

    monkeypatch.setattr(gp, "InstalledAppFlow", Flow)
    gp.authenticate(store)
    assert seen.get("prompt") == "consent"
    assert json.loads(store.read(gp.TOKEN_TARGET))["refresh_token"] == "nouveau"


def _insert_after_soi(jpeg: bytes, marker: int, body: bytes) -> bytes:
    return jpeg[:2] + bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body + jpeg[2:]


def _exif_segments(data: bytes) -> int:
    segs, _sos = gp._jpeg_segments(data)
    return sum(1 for m, s in segs if m == 0xE1 and s[4:].startswith(b"Exif\x00\x00"))


def test_strip_when_icc_precedes_exif(tmp_path):
    """piexif.insert laissait l'ancien Exif (avec GPS) quand un segment le précédait."""
    base = _jpeg(tmp_path / "a.jpg", gps=(44.75, 5.02)).read_bytes()
    p = tmp_path / "icc.jpg"
    p.write_bytes(_insert_after_soi(base, 0xE2, b"ICC_PROFILE\x00\x01\x01" + b"x" * 64))
    assert _exif_segments(p.read_bytes()) == 1
    data = gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert _exif_segments(data) == 1 and _read_gps(data) is None
    assert b"ICC_PROFILE" in data  # profil couleur conservé
    Image.open(io.BytesIO(data)).load()


def test_strip_drops_secondary_images_and_trailer(tmp_path):
    base = _jpeg(tmp_path / "a.jpg", gps=(44.75, 5.02)).read_bytes()
    secondary = _jpeg(tmp_path / "b.jpg", gps=(1.0, 2.0)).read_bytes()
    p = tmp_path / "motion.jpg"
    p.write_bytes(_insert_after_soi(base, 0xE2, b"MPF\x00" + b"\x00" * 16) + secondary + b"ftypmp42VIDEO")
    data = gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert b"VIDEO" not in data and b"MPF\x00" not in data and data.endswith(b"\xff\xd9")
    assert _read_gps(data) is None
    Image.open(io.BytesIO(data)).load()
    # keep (GPS déjà présent) : fichier intact
    assert gp.prepare_upload_bytes(p, gp.GPS_KEEP, 1, 1) == p.read_bytes()


def test_strip_progressive_jpeg(tmp_path):
    img = Image.new("RGB", (64, 48), (10, 200, 30))
    exif = {"0th": {}, "Exif": {}, "1st": {}, "thumbnail": None, "GPS": {
        piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: gp._to_dms(44.0),
        piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: gp._to_dms(5.0)}}
    p = tmp_path / "prog.jpg"
    img.save(p, "JPEG", progressive=True, exif=piexif.dump(exif))
    data = gp.prepare_upload_bytes(p, gp.GPS_STRIP)
    assert _read_gps(data) is None
    out = Image.open(io.BytesIO(data))
    out.load()
    assert out.size == (64, 48)


def test_keep_injection_with_icc_first_has_single_exif(tmp_path):
    base = _jpeg(tmp_path / "a.jpg").read_bytes()
    p = tmp_path / "icc.jpg"
    p.write_bytes(_insert_after_soi(base, 0xE2, b"ICC_PROFILE\x00\x01\x01xx"))
    data = gp.prepare_upload_bytes(p, gp.GPS_KEEP, 44.7, 5.0)
    assert _exif_segments(data) == 1 and _read_gps(data) is not None


def test_retryable_refresh_error_keeps_token(monkeypatch):
    exc = gp.RefreshError("503", retryable=True)
    store = _fake_auth_env(monkeypatch, exc)
    with pytest.raises(gp.GooglePhotosError):
        gp.authenticate(store, interactive=False)
    assert store.read(gp.TOKEN_TARGET) is not None
