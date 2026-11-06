from datetime import date, datetime
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, func, select, text

db = SQLAlchemy()


class BaseModel(db.Model):
    """Base class: menyediakan to_dict() dan daftar kolom yang boleh ditulis."""

    __abstract__ = True

    def to_dict(self):
        result = {}
        for col in self.__table__.columns:
            value = getattr(self, col.name)
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, (date, datetime)):
                value = value.isoformat()
            result[col.name] = value
        return result

    @classmethod
    def writable_columns(cls):
        return {c.name: c for c in cls.__table__.columns if not c.primary_key}


class Customer(BaseModel):
    __tablename__ = "customer"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(30))
    id_card_number = db.Column(db.String(32), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    addresses = db.relationship("Address", backref="customer", lazy=True)
    subscriptions = db.relationship("Subscription", backref="customer", lazy=True)


class Address(BaseModel):
    __tablename__ = "address"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    street = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    postal_code = db.Column(db.String(10))
    coordinates = db.Column(db.String(100))

    subscriptions = db.relationship("Subscription", backref="address", lazy=True)


class Plan(BaseModel):
    __tablename__ = "plan"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    speed_mbps = db.Column(db.Integer, nullable=False)
    quota_gb = db.Column(db.Integer)
    monthly_price = db.Column(db.Numeric(12, 2), nullable=False)

    subscriptions = db.relationship("Subscription", backref="plan", lazy=True)


class Subscription(BaseModel):
    __tablename__ = "subscription"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey("plan.id"), nullable=False)
    address_id = db.Column(db.Integer, db.ForeignKey("address.id"), nullable=False)
    status = db.Column(db.String(30), default="active")
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    devices = db.relationship("Device", backref="subscription", lazy=True)
    invoices = db.relationship("Invoice", backref="subscription", lazy=True)
    tickets = db.relationship("Ticket", backref="subscription", lazy=True)
    usage_records = db.relationship("UsageRecord", backref="subscription", lazy=True)


class Device(BaseModel):
    __tablename__ = "device"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscription.id"), nullable=False)
    serial_number = db.Column(db.String(100), unique=True, nullable=False)
    model = db.Column(db.String(100))
    ip_address = db.Column(db.String(45))
    mac_address = db.Column(db.String(17))
    status = db.Column(db.String(30))


class Invoice(BaseModel):
    __tablename__ = "invoice"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscription.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), default="unpaid")
    issued_at = db.Column(db.DateTime, default=datetime.now)

    payments = db.relationship("Payment", backref="invoice", lazy=True)


class Payment(BaseModel):
    __tablename__ = "payment"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(50))
    reference_no = db.Column(db.String(100))
    paid_at = db.Column(db.DateTime, default=datetime.now)


class Technician(BaseModel):
    __tablename__ = "technician"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30))
    specialty = db.Column(db.String(100))
    region = db.Column(db.String(100))

    tickets = db.relationship("Ticket", backref="technician", lazy=True)


class Ticket(BaseModel):
    __tablename__ = "ticket"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscription.id"), nullable=False)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))  # boleh kosong sebelum ditugaskan
    title = db.Column(db.String(200), nullable=False)
    priority = db.Column(db.String(20))
    status = db.Column(db.String(30), default="open")
    reported_at = db.Column(db.DateTime, default=datetime.now)
    resolved_at = db.Column(db.DateTime)


class UsageRecord(BaseModel):
    __tablename__ = "usage_record"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscription.id"), nullable=False)
    record_date = db.Column(db.Date, nullable=False)
    download_gb = db.Column(db.Numeric(10, 2), default=0)
    upload_gb = db.Column(db.Numeric(10, 2), default=0)


# ---------------------------------------------------------------
# Alokasi id di sisi aplikasi
# ---------------------------------------------------------------
# Kolom `id` pada tabel database bertipe INTEGER NOT NULL tanpa DEFAULT
# auto-increment (tidak ada sequence / IDENTITY), sehingga INSERT harus
# selalu menyertakan nilai id. Id dialokasikan di sini dengan MAX(id) + 1.
#
# Advisory lock dipakai agar dua request yang berjalan bersamaan tidak
# memperoleh id yang sama: lock ditahan sampai transaksi selesai (xact_lock).
# Query MAX(id) dijalankan pada koneksi transaksi yang sama, sehingga id yang
# baru saja di-commit oleh transaksi lain ikut terhitung.

def _allocate_id(connection, table):
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": table.name},
    )
    max_id = connection.execute(
        select(func.coalesce(func.max(table.c.id), 0))
    ).scalar_one()
    return int(max_id) + 1


def _assign_id(mapper, connection, target):
    """Isi kolom id otomatis bila INSERT tidak menyertakan id."""
    if target.id is None:
        target.id = _allocate_id(connection, mapper.local_table)


for _model in (
    Customer,
    Address,
    Plan,
    Subscription,
    Device,
    Invoice,
    Payment,
    Technician,
    Ticket,
    UsageRecord,
):
    event.listen(_model, "before_insert", _assign_id)
