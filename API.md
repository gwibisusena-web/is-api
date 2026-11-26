# Audit Logging API

Setiap request (kecuali `/static/`) dicatat ke tabel `api_log`, termasuk request
yang gagal autentikasi (401) dan endpoint tidak ditemukan (404).

## Konfigurasi (`.env`)

| Variabel | Default | Keterangan |
|---|---|---|
| `LOG_ENABLED` | `1` | `0` untuk mematikan penulisan log |
| `LOG_MAX_BODY_BYTES` | `8192` | Batas potongan request/response body per field |

Field sensitif (`password`, `token`, `authorization`, `secret`, `api_key`, dll.)
diganti `***`. Header `Authorization` tidak pernah disimpan. Response body hanya
disimpan untuk status `>= 400`. Setiap respons menyertakan header `X-Request-ID`
yang sama dengan kolom `request_id` di tabel log.

## Tabel `api_log`

Kolom utama: `id`, `request_id`, `created_at`, `method`, `path`, `endpoint`,
`query_params`, `status_code`, `level` (`INFO`/`WARN`/`ERROR`), `duration_ms`,
`user_id`, `user_email`, `user_role`, `ip_address`, `user_agent`, `request_body`,
`response_body`, `error_message`.

`id` memakai `SERIAL` dari database. Buat tabel dengan:

```bash
python manage.py init-db
```

## Endpoint (khusus admin)

- `GET /api-logs` — log terbaru. Filter: `?status=404&user_id=1&start=2026-01-01&end=2026-02-01&limit=100` (maks. `limit` = 1000).
- `GET /api-logs/<id>` — detail satu log.

Role `viewer` mendapat 403. Endpoint ini sengaja tidak punya POST/PUT/PATCH/DELETE.

## Pembersihan log

```bash
python manage.py cleanup-logs --days 30
```

Menghapus log yang `created_at`-nya lebih lama dari 30 hari. Karena request body
bisa memuat data pelanggan, jalankan perintah ini secara berkala dan batasi akses
tabel/endpoint ke admin.
