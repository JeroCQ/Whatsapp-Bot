"""Preserve existing object keys and validate bytes through the Storage API."""

import hashlib
import os
import re
import tempfile
from urllib.parse import quote, urlsplit

import httpx

from crm_service.central_copy_connection import CENTRAL_REF, CopyError, connect

CHUNK_SIZE = 256 * 1024
MAX_BYTES = 200 * 1024 * 1024


def storage_settings(origin_name, key_name, *, central=False):
    origin = os.environ.get(origin_name, "").rstrip("/")
    key = os.environ.get(key_name, "")
    parsed = urlsplit(origin)
    if (not key or parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise CopyError(f"Conexión privada de Storage inválida: {origin_name}")
    if central and parsed.netloc != f"{CENTRAL_REF}.supabase.co":
        raise CopyError("El Storage destino debe pertenecer al proyecto de Tanaka.")
    return origin, {"apikey": key, "Authorization": "Bearer " + key}


def object_url(origin, bucket, name, *, authenticated=False):
    prefix = "object/authenticated" if authenticated else "object"
    return f"{origin}/storage/v1/{prefix}/{quote(bucket, safe='')}/{quote(name, safe='/')}"


def read_object(client, settings, bucket, name, *, missing=False, file=None):
    origin, headers = settings
    with client.stream("GET", object_url(origin, bucket, name, authenticated=True),
                       headers={**headers, "Accept-Encoding": "identity"}) as response:
        if response.status_code == 404 and missing:
            return None
        # Some Storage versions use 400 with an explicit missing-object error.
        if response.status_code == 400 and missing:
            body = next(response.iter_bytes(4097), b"")
            if len(body) <= 4096 and b'"statusCode":"404"' in body.replace(b" ", b""):
                return None
        if response.status_code != 200:
            raise CopyError(f"Storage no confirmó la lectura de un objeto (HTTP {response.status_code}).")
        count, digest = 0, hashlib.sha256()
        for chunk in response.iter_bytes(CHUNK_SIZE):
            count += len(chunk)
            if count > MAX_BYTES:
                raise CopyError("Un objeto excede el límite de archivo de Chatty.")
            digest.update(chunk)
            if file is not None:
                file.write(chunk)
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return count, digest.hexdigest(), mime


def validate_bucket(db, bucket, expected_public):
    row = db.execute("SELECT public FROM storage.buckets WHERE id = %s", (bucket,)).fetchone()
    if row != (expected_public,):
        raise CopyError(f"Falta el bucket esperado o su visibilidad difiere: {bucket}")


def check_asset_references(source, dataset, media_bucket):
    pattern = dataset + "/%"
    invalid = source.execute("""
        SELECT EXISTS (
            SELECT 1 FROM public.crm_media_assets AS a
            CROSS JOIN LATERAL (VALUES
                (a.storage_key, a.sha256, a.size_bytes),
                (a.original_storage_key, a.source_sha256, NULL),
                (a.playback->>'storage_key', a.playback->>'sha256',
                 (a.playback->>'size_bytes')::bigint)
            ) AS asset(object_key, sha, size)
            WHERE a.status = 'ready' AND (
              (asset.object_key IS NOT NULL AND (
                asset.object_key NOT LIKE %s OR NOT EXISTS (
                  SELECT 1 FROM storage.objects AS o
                   WHERE o.bucket_id = %s AND o.name = asset.object_key
                     AND (asset.size IS NULL OR (o.metadata->>'size')::bigint = asset.size)
                )
              )) OR a.storage_key IS NULL
            )
        )
    """, (pattern, media_bucket)).fetchone()[0]
    if invalid:
        raise CopyError(f"Hay adjuntos listos con objetos fuera de su negocio o ausentes: {dataset}")


def verify_media_bytes(source, media_bucket, objects):
    with source.cursor(name="chatty_media_references") as cursor:
        cursor.execute("""
            SELECT asset.object_key, asset.sha, asset.size
            FROM public.crm_media_assets AS a
            CROSS JOIN LATERAL (VALUES
                (a.storage_key, a.sha256, a.size_bytes),
                (a.original_storage_key, a.source_sha256, NULL),
                (a.playback->>'storage_key', a.playback->>'sha256',
                 (a.playback->>'size_bytes')::bigint)
            ) AS asset(object_key, sha, size)
            WHERE a.status = 'ready' AND asset.object_key IS NOT NULL
        """)
        for key, sha, size in cursor:
            copied = objects.get((media_bucket, key))
            if copied is None or (sha is not None and sha != copied[1]) or (size is not None and size != copied[0]):
                raise CopyError("El contenido verificado no coincide con los metadatos de un adjunto.")


def object_inventory(source, dataset, catalog, media):
    with source.cursor(name="chatty_object_inventory") as cursor:
        cursor.execute("""
            SELECT bucket_id, name, (metadata->>'size')::bigint,
                   lower(COALESCE(metadata->>'mimetype', ''))
            FROM storage.objects
            WHERE (bucket_id = %s AND (name LIKE %s OR name = ANY(%s)))
               OR (bucket_id = %s AND name LIKE %s)
            ORDER BY bucket_id, name
        """, (catalog, dataset + "/%", [dataset + "." + ext for ext in ("pdf", "jpg", "jpeg", "png", "webp")],
              media, dataset + "/%"))
        yield from cursor


def copy_object(client, source_settings, dest_settings, ledger, epoch, dataset, item):
    bucket, name, expected_size, expected_mime = item
    if (not isinstance(expected_size, int) or not 0 < expected_size <= MAX_BYTES
            or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", expected_mime)
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise CopyError(f"Metadatos de un objeto incompatibles: {dataset}/{bucket}")
    with tempfile.TemporaryFile(mode="w+b") as file:
        desired = read_object(client, source_settings, bucket, name, file=file)
        if desired[0] != expected_size or desired[2] != expected_mime:
            raise CopyError(f"Storage cambió durante la lectura: {dataset}/{bucket}")
        found = read_object(client, dest_settings, bucket, name, missing=True)
        if found != desired:
            previous = ledger.execute("""
                SELECT content_sha256, object_size, mime_type FROM chatty_migration_stage.storage_copies
                WHERE epoch = %s AND dataset = %s AND bucket = %s AND object_key = %s
            """, (epoch, dataset, bucket, name)).fetchone()
            if found is not None and previous != (found[1], found[0], found[2]):
                raise CopyError(f"Un objeto destino preexistente difiere: {dataset}/{bucket}")
            origin, headers = dest_settings
            file.seek(0)
            response = client.post(object_url(origin, bucket, name), content=iter(lambda: file.read(CHUNK_SIZE), b""),
                                   headers={**headers, "Content-Length": str(desired[0]), "Content-Type": desired[2],
                                            "x-upsert": "true" if found is not None else "false"})
            if not response.is_success:
                raise CopyError(f"Storage rechazó la escritura (HTTP {response.status_code}): {dataset}/{bucket}")
            if read_object(client, dest_settings, bucket, name) != desired:
                raise CopyError(f"Bytes de destino distintos: {dataset}/{bucket}")
        ledger.execute("""
            INSERT INTO chatty_migration_stage.storage_copies
                (epoch, dataset, bucket, object_key, content_sha256, object_size, mime_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (epoch, dataset, bucket, object_key) DO UPDATE
            SET content_sha256 = EXCLUDED.content_sha256,
                object_size = EXCLUDED.object_size, mime_type = EXCLUDED.mime_type
        """, (epoch, dataset, bucket, name, desired[1], desired[0], desired[2]))
    return desired


def transfer_storage(source, target, epoch, dataset):
    src = storage_settings("CHATTY_MIGRATION_SOURCE_STORAGE_URL", "CHATTY_MIGRATION_SOURCE_STORAGE_KEY")
    dest = storage_settings("CHATTY_CENTRAL_STORAGE_URL", "SUPABASE_SERVICE_ROLE_KEY", central=True)
    if src[0] == dest[0]:
        raise CopyError("Storage de origen y destino son el mismo proyecto.")
    catalog = os.environ.get("CATALOG_STORAGE_BUCKET", "catalogos")
    media = os.environ.get("CRM_MEDIA_BUCKET", "crm-private-media")
    for bucket, public in ((catalog, True), (media, False)):
        validate_bucket(source, bucket, public)
        validate_bucket(target, bucket, public)
    check_asset_references(source, dataset, media)
    seen, total = {}, 0
    try:
        with (connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as ledger,
              httpx.Client(timeout=httpx.Timeout(120, connect=5), follow_redirects=False, trust_env=False) as client):
            for item in object_inventory(source, dataset, catalog, media):
                verified = copy_object(client, src, dest, ledger, epoch, dataset, item)
                seen[item[0], item[1]] = verified
                total += verified[0]
            verify_media_bytes(source, media, seen)
            copied = ledger.execute("""
                SELECT bucket, object_key FROM chatty_migration_stage.storage_copies
                WHERE epoch = %s AND dataset = %s
            """, (epoch, dataset))
            if any(tuple(row) not in seen for row in copied):
                raise CopyError(f"Un objeto preparado desapareció del origen; requiere reconciliación: {dataset}")
    except httpx.HTTPError as error:
        raise CopyError(f"Conexión con Storage interrumpida: {dataset} ({type(error).__name__}).") from None
    return {"objects": len(seen), "bytes": total}
