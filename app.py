from datetime import date, datetime

from flask import Flask, g, jsonify, request
from sqlalchemy import Date, DateTime, func
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from api_logger import finish_request, start_request
from auth import (
    VALID_ROLES,
    admin_required,
    authenticate,
    create_token,
    hash_password,
    verify_password,
)
from config import Config
from models import (
    Address,
    ApiLog,
    Customer,
    Device,
    Invoice,
    Payment,
    Plan,
    Subscription,
    Technician,
    Ticket,
    UsageRecord,
    User,
    db,
)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

if not app.config.get("JWT_SECRET_KEY"):
    raise RuntimeError(
        "JWT_SECRET_KEY belum diset di .env. Buat dengan: "
        'python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )

# Timer log dimulai lebih dulu agar request yang ditolak authenticate tetap tercatat.
app.before_request(start_request)

# Semua request lewat guard autentikasi (kecuali endpoint publik).
app.before_request(authenticate)

# Tulis audit log setelah respons siap (termasuk respons error).
app.after_request(finish_request)

# Status invoice. Sesuaikan dengan nilai yang kamu pakai di database.
STATUS_PAID = "paid"
STATUS_UNPAID = "unpaid"


# ---------------------------------------------------------------
# Error handler (semua error dikembalikan sebagai JSON)
# ---------------------------------------------------------------
@app.errorhandler(404)
def not_found(_):
    return jsonify(error="Data atau endpoint tidak ditemukan"), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify(error="Method tidak diizinkan"), 405


@app.errorhandler(401)
def unauthorized(_):
    return jsonify(error="Tidak terautentikasi"), 401


@app.errorhandler(403)
def forbidden(_):
    return jsonify(error="Tidak punya akses"), 403


@app.errorhandler(Exception)
def handle_exception(e):
    """Semua exception tak tertangani jadi JSON 500 dan tercatat di api_log."""
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    g.log_error_message = f"{type(e).__name__}: {e}"
    return jsonify(error="Terjadi kesalahan pada server"), 500


# ---------------------------------------------------------------
# Helper
# ---------------------------------------------------------------
def coerce_value(column, value):
    """Ubah string ISO (YYYY-MM-DD / YYYY-MM-DDTHH:MM:SS) menjadi date/datetime."""
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    return value


def db_error(e):
    db.session.rollback()
    return (
        jsonify(
            error="Data melanggar constraint (NOT NULL / UNIQUE / FOREIGN KEY)",
            detail=str(e.orig),
        ),
        400,
    )


def sync_invoice_status(invoice):
    """Samakan status invoice dengan total pembayaran yang tercatat."""
    db.session.refresh(invoice)
    total_paid = (
        db.session.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.invoice_id == invoice.id)
        .scalar()
    )
    if total_paid >= invoice.amount:
        invoice.status = STATUS_PAID
    elif invoice.status == STATUS_PAID:
        invoice.status = STATUS_UNPAID  # nominal naik / pembayaran dikoreksi turun


def after_invoice_update(invoice):
    sync_invoice_status(invoice)


def after_payment_update(payment):
    invoice = db.session.get(Invoice, payment.invoice_id)
    if invoice:
        sync_invoice_status(invoice)


def register_crud(path, model, readonly=(), on_update=None):
    """
    Mendaftarkan GET (list), GET (detail), POST, PUT, PATCH, DELETE untuk satu model.

    readonly  : nama kolom yang tidak boleh diubah lewat PUT/PATCH.
    on_update : fungsi(item) yang dipanggil setelah PUT/PATCH, sebelum commit.
    """
    name = model.__tablename__
    readonly = set(readonly)

    def list_all():
        items = model.query.order_by(model.id).all()
        return jsonify([i.to_dict() for i in items])

    def get_one(id):
        return jsonify(db.get_or_404(model, id).to_dict())

    def create():
        body = request.get_json(silent=True)
        if not body:
            return jsonify(error="Body JSON wajib diisi"), 400

        columns = model.writable_columns()
        try:
            data = {k: coerce_value(columns[k], v) for k, v in body.items() if k in columns}
            item = model(**data)
            db.session.add(item)
            db.session.commit()
        except ValueError:
            db.session.rollback()
            return jsonify(error="Format tanggal/waktu tidak valid (gunakan format ISO)"), 400
        except IntegrityError as e:
            return db_error(e)
        return jsonify(item.to_dict()), 201

    def apply_update(id, partial):
        """partial=True -> PATCH (ubah field yang dikirim saja); partial=False -> PUT (ganti penuh)."""
        item = db.get_or_404(model, id)
        body = request.get_json(silent=True)
        if not body:
            return jsonify(error="Body JSON wajib diisi"), 400

        columns = model.writable_columns()

        # Field readonly boleh dikirim, asalkan nilainya sama dengan yang tersimpan.
        locked = [
            k for k in readonly
            if k in body and str(getattr(item, k)) != str(body[k])
        ]
        if locked:
            return jsonify(error="Field tidak boleh diubah", fields=sorted(locked)), 400

        editable = {k: c for k, c in columns.items() if k not in readonly}

        # PUT: semua field wajib (NOT NULL tanpa default) harus dikirim.
        if not partial:
            missing = [
                k for k, c in editable.items()
                if k not in body
                and not c.nullable
                and c.default is None
                and c.server_default is None
            ]
            if missing:
                return jsonify(error="PUT membutuhkan data lengkap", missing=sorted(missing)), 400

        try:
            for key, col in editable.items():
                if key in body:
                    setattr(item, key, coerce_value(col, body[key]))
                elif not partial and col.nullable:
                    setattr(item, key, None)  # PUT: field nullable yang tidak dikirim di-reset
            if on_update:
                db.session.flush()
                on_update(item)
            db.session.commit()
        except ValueError:
            db.session.rollback()
            return jsonify(error="Format tanggal/waktu tidak valid (gunakan format ISO)"), 400
        except IntegrityError as e:
            return db_error(e)
        return jsonify(item.to_dict())

    def replace(id):
        return apply_update(id, partial=False)

    def patch(id):
        return apply_update(id, partial=True)

    def delete(id):
        item = db.get_or_404(model, id)
        try:
            db.session.delete(item)
            db.session.commit()
        except IntegrityError as e:
            return db_error(e)  # biasanya karena masih dipakai tabel lain
        return jsonify(message=f"{name} berhasil dihapus")

    app.add_url_rule(f"/{path}", f"{name}_list", list_all, methods=["GET"])
    app.add_url_rule(f"/{path}/<int:id>", f"{name}_get", get_one, methods=["GET"])
    app.add_url_rule(f"/{path}", f"{name}_create", create, methods=["POST"])
    app.add_url_rule(f"/{path}/<int:id>", f"{name}_put", replace, methods=["PUT"])
    app.add_url_rule(f"/{path}/<int:id>", f"{name}_patch", patch, methods=["PATCH"])
    app.add_url_rule(f"/{path}/<int:id>", f"{name}_delete", delete, methods=["DELETE"])


for _path, _model, _opts in [
    ("customers", Customer, {}),
    ("addresses", Address, {}),
    ("plans", Plan, {}),
    ("subscriptions", Subscription, {}),
    ("devices", Device, {}),
    # Ubah nominal invoice -> status ikut disesuaikan dengan total pembayaran.
    ("invoices", Invoice, {"on_update": after_invoice_update}),
    # invoice_id pada payment dikunci agar pembayaran tidak "pindah" invoice.
    ("payments", Payment, {"readonly": ("invoice_id",), "on_update": after_payment_update}),
    ("technicians", Technician, {}),
    ("tickets", Ticket, {}),
    ("usage-records", UsageRecord, {}),
]:
    register_crud(_path, _model, **_opts)


# ---------------------------------------------------------------
# Endpoint tambahan yang memanfaatkan relasi antar tabel
# ---------------------------------------------------------------
@app.get("/customers/<int:id>/addresses")
def customer_addresses(id):
    customer = db.get_or_404(Customer, id)
    return jsonify([a.to_dict() for a in customer.addresses])


@app.get("/customers/<int:id>/subscriptions")
def customer_subscriptions(id):
    customer = db.get_or_404(Customer, id)
    return jsonify([s.to_dict() for s in customer.subscriptions])


@app.get("/subscriptions/<int:id>/detail")
def subscription_detail(id):
    """Satu subscription lengkap dengan customer, plan, alamat, device, invoice, dan tiket."""
    sub = db.get_or_404(Subscription, id)
    data = sub.to_dict()
    data["customer"] = sub.customer.to_dict()
    data["plan"] = sub.plan.to_dict()
    data["address"] = sub.address.to_dict()
    data["devices"] = [d.to_dict() for d in sub.devices]
    data["invoices"] = [i.to_dict() for i in sub.invoices]
    data["tickets"] = [t.to_dict() for t in sub.tickets]
    return jsonify(data)


@app.get("/subscriptions/<int:id>/usage-summary")
def usage_summary(id):
    """Total pemakaian. Opsional: ?start=YYYY-MM-DD&end=YYYY-MM-DD"""
    db.get_or_404(Subscription, id)

    query = db.session.query(
        func.coalesce(func.sum(UsageRecord.download_gb), 0),
        func.coalesce(func.sum(UsageRecord.upload_gb), 0),
    ).filter(UsageRecord.subscription_id == id)

    try:
        if request.args.get("start"):
            query = query.filter(UsageRecord.record_date >= date.fromisoformat(request.args["start"]))
        if request.args.get("end"):
            query = query.filter(UsageRecord.record_date <= date.fromisoformat(request.args["end"]))
    except ValueError:
        return jsonify(error="Format start/end harus YYYY-MM-DD"), 400

    download, upload = query.one()
    return jsonify(
        subscription_id=id,
        total_download_gb=float(download),
        total_upload_gb=float(upload),
        total_gb=float(download + upload),
    )


@app.get("/invoices/<int:id>/payments")
def invoice_payments(id):
    invoice = db.get_or_404(Invoice, id)
    return jsonify([p.to_dict() for p in invoice.payments])


@app.post("/invoices/<int:id>/pay")
def pay_invoice(id):
    """Catat pembayaran. Jika total pembayaran >= nominal invoice, status invoice menjadi 'paid'."""
    invoice = db.get_or_404(Invoice, id)
    body = request.get_json(silent=True) or {}

    if "amount" not in body or "method" not in body:
        return jsonify(error="Field wajib: amount, method"), 400

    payment = Payment(
        invoice_id=invoice.id,
        amount=body["amount"],
        method=body["method"],
        reference_no=body.get("reference_no"),
    )
    try:
        db.session.add(payment)
        db.session.flush()

        total_paid = (
            db.session.query(func.coalesce(func.sum(Payment.amount), 0))
            .filter(Payment.invoice_id == invoice.id)
            .scalar()
        )
        if total_paid >= invoice.amount:
            invoice.status = STATUS_PAID

        db.session.commit()
    except IntegrityError as e:
        return db_error(e)

    return jsonify(payment=payment.to_dict(), invoice=invoice.to_dict()), 201


@app.get("/technicians/<int:id>/tickets")
def technician_tickets(id):
    technician = db.get_or_404(Technician, id)
    return jsonify([t.to_dict() for t in technician.tickets])


# ---------------------------------------------------------------
# Audit log (hanya admin)
# ---------------------------------------------------------------
@app.get("/api-logs")
@admin_required
def api_logs_list():
    """Log terbaru. Filter opsional: status, user_id, start, end, limit."""
    query = ApiLog.query

    status = request.args.get("status", type=int)
    if status is not None:
        query = query.filter(ApiLog.status_code == status)

    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        query = query.filter(ApiLog.user_id == user_id)

    try:
        if request.args.get("start"):
            query = query.filter(ApiLog.created_at >= datetime.fromisoformat(request.args["start"]))
        if request.args.get("end"):
            query = query.filter(ApiLog.created_at <= datetime.fromisoformat(request.args["end"]))
    except ValueError:
        return jsonify(error="Format start/end harus ISO (YYYY-MM-DD atau datetime ISO)"), 400

    limit = request.args.get("limit", default=100, type=int)
    if limit is None or limit < 1:
        return jsonify(error="limit harus berupa angka >= 1"), 400
    limit = min(limit, 1000)

    items = query.order_by(ApiLog.id.desc()).limit(limit).all()
    return jsonify([i.to_dict() for i in items])


@app.get("/api-logs/<int:id>")
@admin_required
def api_logs_get(id):
    return jsonify(db.get_or_404(ApiLog, id).to_dict())


# ---------------------------------------------------------------
# Autentikasi
# ---------------------------------------------------------------
@app.post("/auth/login")
def auth_login():
    """Tukar email + password dengan access token."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify(error="Field wajib: email, password"), 400

    user = User.query.filter(func.lower(User.email) == email).first()

    # Pesan sengaja sama untuk email tidak ada / password salah / user nonaktif,
    # supaya tidak membocorkan email mana yang terdaftar.
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return jsonify(error="Email atau password salah"), 401

    return jsonify(
        access_token=create_token(user),
        token_type="Bearer",
        expires_in=app.config["JWT_EXPIRES_MINUTES"] * 60,
        user=user.to_dict(),
    )


@app.get("/auth/me")
def auth_me():
    """Profil user yang sedang login."""
    return jsonify(g.user.to_dict())


@app.post("/auth/register")
def auth_register():
    """Buat akun baru. Hanya admin (sudah dijamin oleh before_request)."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    role = (body.get("role") or "viewer").strip().lower()

    if not email or not password:
        return jsonify(error="Field wajib: email, password"), 400
    if role not in VALID_ROLES:
        return jsonify(error=f"Role harus salah satu dari: {', '.join(VALID_ROLES)}"), 400
    if len(password) < 8:
        return jsonify(error="Password minimal 8 karakter"), 400
    if User.query.filter(func.lower(User.email) == email).first():
        return jsonify(error="Email sudah terdaftar"), 409

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError as e:
        return db_error(e)
    return jsonify(user.to_dict()), 201


if __name__ == "__main__":
    app.run(debug=True)