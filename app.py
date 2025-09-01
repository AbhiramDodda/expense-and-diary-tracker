from flask import Flask, jsonify, request, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, date
import os, base64
from cryptography.fernet import Fernet
from sqlalchemy import func

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

def get_fernet():
    key = os.environ.get("FERNET_KEY")
    if not key:
        # set FERNET_KEY env var to a 32-byte base64 urlsafe key - this is used to encrypt all the diary entries
        key = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
        os.environ["FERNET_KEY"] = key
    return Fernet(key.encode("utf-8"))

fernet = get_fernet()

def encrypt_text(plain: str) -> bytes:
    return fernet.encrypt(plain.encode("utf-8"))

def decrypt_text(cipher: bytes) -> str:
    try:
        return fernet.decrypt(cipher).decode("utf-8")
    except Exception:
        return "[decryption failed]"

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255), nullable=True)

class DiaryEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    content_enc = db.Column(db.LargeBinary, nullable=False)

@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/expenses")
def add_expense():
    data = request.get_json(force=True)
    dt = datetime.strptime(data["date"], "%Y-%m-%d").date()
    exp = Expense(
        date=dt,
        category=data["category"],
        amount=float(data["amount"]),
        note=data.get("note", "")
    )
    db.session.add(exp)
    db.session.commit()
    return jsonify({"status": "ok", "id": exp.id}), 201

@app.get("/api/expenses")
def list_expenses():
    date_str = request.args.get("date")
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    q = Expense.query
    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        q = q.filter(Expense.date == dt)
    if year:
        q = q.filter(func.strftime("%Y", Expense.date) == f"{year:04d}")
    if month:
        q = q.filter(func.strftime("%m", Expense.date) == f"{month:02d}")
    
    items = q.order_by(Expense.date.desc(), Expense.id.desc()).all()
    return jsonify([{
        "id": e.id,
        "date": e.date.isoformat(),
        "category": e.category,
        "amount": e.amount,
        "note": e.note or ""
    } for e in items])

@app.put("/api/expenses/<int:expense_id>")
def update_expense(expense_id):
    data = request.get_json(force=True)
    exp = Expense.query.get_or_404(expense_id)
    exp.category = data.get("category", exp.category)
    exp.amount = float(data.get("amount", exp.amount))
    exp.note = data.get("note", exp.note)
    db.session.commit()
    return jsonify({"status": "ok", "id": exp.id}), 200

@app.delete("/api/expenses/<int:expense_id>")
def delete_expense(expense_id):
    exp = Expense.query.get_or_404(expense_id)
    db.session.delete(exp)
    db.session.commit()
    return jsonify({"status": "ok", "id": exp.id}), 200

@app.get("/api/expenses/summary/monthly")
def monthly_category_pie():
    year = request.args.get("year", type=int, default=date.today().year)
    month = request.args.get("month", type=int, default=date.today().month)
    q = db.session.query(
        Expense.category,
        func.sum(Expense.amount).label("total")
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}",
        func.strftime("%m", Expense.date) == f"{month:02d}"
    ).group_by(Expense.category)
    data = [{"category": cat, "total": float(total)} for cat, total in q]
    return jsonify({"year": year, "month": month, "data": data})

@app.get("/api/expenses/summary/yearly")
def yearly_line():
    year = request.args.get("year", type=int, default=date.today().year)
    q = db.session.query(
        func.strftime("%m", Expense.date).label("m"),
        func.sum(Expense.amount).label("total")
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}"
    ).group_by("m").order_by("m")
    totals = {int(m): float(total) for m, total in q}
    series = [totals.get(m, 0.0) for m in range(1, 13)]
    return jsonify({"year": year, "series": series})

# Diary
@app.post("/api/diary")
def add_diary():
    data = request.get_json(force=True)
    dt = datetime.strptime(data["date"], "%Y-%m-%d").date()
    enc = encrypt_text(data["content"])
    entry = DiaryEntry(date=dt, content_enc=enc)
    db.session.add(entry)
    db.session.commit()
    return jsonify({"status": "ok", "id": entry.id}), 201

@app.get("/api/diary")
def list_diary():
    q = DiaryEntry.query
    date_str = request.args.get("date")
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        q = q.filter(DiaryEntry.date == dt)
    if year:
        q = q.filter(func.strftime("%Y", DiaryEntry.date) == f"{year:04d}")
    if month:
        q = q.filter(func.strftime("%m", DiaryEntry.date) == f"{month:02d}")
    items = q.order_by(DiaryEntry.date.desc(), DiaryEntry.id.desc()).all()
    return jsonify([{
        "id": d.id,
        "date": d.date.isoformat(),
        "content": decrypt_text(d.content_enc)
    } for d in items])

@app.get("/api/calendar/daily_totals")
def calendar_totals():
    """
    Returns daily totals for a given month in ISO dates with:
    - total_expenses
    - diary_count
    """
    year = request.args.get("year", type=int, default=date.today().year)
    month = request.args.get("month", type=int, default=date.today().month)

    # Expenses per day
    q1 = db.session.query(
        func.strftime("%Y-%m-%d", Expense.date),
        func.sum(Expense.amount)
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}",
        func.strftime("%m", Expense.date) == f"{month:02d}"
    ).group_by(func.strftime("%Y-%m-%d", Expense.date))

    expenses_map = {d: float(t) for d, t in q1}

    # Diary counts per day
    q2 = db.session.query(
        func.strftime("%Y-%m-%d", DiaryEntry.date),
        func.count(DiaryEntry.id)
    ).filter(
        func.strftime("%Y", DiaryEntry.date) == f"{year:04d}",
        func.strftime("%m", DiaryEntry.date) == f"{month:02d}"
    ).group_by(func.strftime("%Y-%m-%d", DiaryEntry.date))

    diary_map = {d: int(c) for d, c in q2}

    # Merge
    all_days = set(expenses_map.keys()) | set(diary_map.keys())
    result = [{
        "date": d,
        "total_expenses": expenses_map.get(d, 0.0),
        "diary_count": diary_map.get(d, 0)
    } for d in sorted(all_days)]
    return jsonify({"year": year, "month": month, "days": result})

# # Utility route to initialize DB
# @app.get("/api/_init_db")
# def init_db():
#     db.create_all()
#     return jsonify({"status": "ok", "message": "Database initialized."})

if __name__ == "__main__":
    # Create DB tables on first run
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)