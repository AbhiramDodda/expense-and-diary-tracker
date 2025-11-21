from flask import Flask, jsonify, request, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, date
import os, base64
from cryptography.fernet import Fernet
from sqlalchemy import func
from dateutil.relativedelta import relativedelta

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
    
def calculate_emi_schedule(plan):
    payments = []
    
    # Get all paid payments for this plan
    paid_dates = {p.due_date.isoformat() for p in plan.payments.all()}
    
    # Calculate scheduled payments
    for i in range(plan.duration_months):
        # Calculate the payment due date: start_date + i months
        due_date = plan.start_date + relativedelta(months=+i)
        
        date_str = due_date.isoformat()
        
        payments.append({
            "plan_id": plan.id,
            "emi_key": f"{plan.id}-{date_str}", # Unique key for payment
            "month": date_str[:7], # YYYY-MM
            "due_date": date_str,
            "amount": plan.monthly_payment,
            "note": plan.note,
            "is_paid": date_str in paid_dates
        })

    return payments

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

class Earning(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(80), nullable=True)

class EMIPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    duration_months = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255), nullable=True)
    monthly_payment = db.Column(db.Float, nullable=False)
    payments = db.relationship('EMIPayment', backref='plan', lazy='dynamic', cascade='all, delete-orphan')

class EMIPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('emi_plan.id'), nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    paid = db.Column(db.Boolean, default=False)

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

# app.py (after the existing expense endpoints)
# ...

# Earnings

@app.post("/api/earnings")
def add_earning():
    data = request.get_json(force=True)
    dt = datetime.strptime(data["date"], "%Y-%m-%d").date()
    earning = Earning(
        date=dt,
        amount=float(data["amount"]),
        source=data.get("source", "")
    )
    db.session.add(earning)
    db.session.commit()
    return jsonify({"status": "ok", "id": earning.id}), 201

@app.get("/api/earnings")
def list_earnings():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    q = Earning.query
    if year:
        q = q.filter(func.strftime("%Y", Earning.date) == f"{year:04d}")
    if month:
        q = q.filter(func.strftime("%m", Earning.date) == f"{month:02d}")

    items = q.order_by(Earning.date.desc(), Earning.id.desc()).all()
    return jsonify([{
        "id": e.id,
        "date": e.date.isoformat(),
        "amount": e.amount,
        "source": e.source or ""
    } for e in items])

# app.py (near yearly_line endpoint)
# ...
@app.get("/api/summary/yearly_financials")
def yearly_financials():
    year = request.args.get("year", type=int, default=date.today().year)

    # Expenses per month
    q_exp = db.session.query(
        func.strftime("%m", Expense.date).label("m"),
        func.sum(Expense.amount).label("total")
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}"
    ).group_by("m")
    expenses_totals = {int(m): float(total) for m, total in q_exp}

    # Earnings per month
    q_earn = db.session.query(
        func.strftime("%m", Earning.date).label("m"),
        func.sum(Earning.amount).label("total")
    ).filter(
        func.strftime("%Y", Earning.date) == f"{year:04d}"
    ).group_by("m")
    earnings_totals = {int(m): float(total) for m, total in q_earn}

    # Format for chart
    monthly_expenses = [expenses_totals.get(m, 0.0) for m in range(1, 13)]
    monthly_earnings = [earnings_totals.get(m, 0.0) for m in range(1, 13)]
    
    # Calculate Year-to-Date Totals
    total_expenses = sum(monthly_expenses)
    total_earnings = sum(monthly_earnings)
    profit = total_earnings - total_expenses

    return jsonify({
        "year": year, 
        "monthly_expenses": monthly_expenses,
        "monthly_earnings": monthly_earnings,
        "total_expenses": total_expenses,
        "total_earnings": total_earnings,
        "profit": profit
    })

# app.py (after the earnings endpoints)
# ...

# EMI

@app.post("/api/emi")
def add_emi_plan():
    data = request.get_json(force=True)
    amount = float(data["amount"])
    duration_months = int(data["duration_months"])
    note = data.get("note", "")
    
    if duration_months <= 0:
        return jsonify({"status": "error", "message": "Duration must be greater than 0"}), 400

    monthly_payment = amount / duration_months
    
    dt = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
    
    # Check for existing plan with the same note and start date (to avoid duplicates, simple check)
    existing_plan = EMIPlan.query.filter_by(note=note, start_date=dt).first()
    if existing_plan:
        return jsonify({"status": "error", "message": "A plan with this note and start date already exists."}), 409

    emi = EMIPlan(
        start_date=dt,
        amount=amount,
        duration_months=duration_months,
        note=note,
        monthly_payment=monthly_payment
    )
    db.session.add(emi)
    db.session.commit()
    return jsonify({"status": "ok", "id": emi.id, "monthly_payment": monthly_payment}), 201

@app.get("/api/emi")
def list_emi_plans():
    # 1. Fetch all EMI plans
    plans = EMIPlan.query.order_by(EMIPlan.start_date.desc(), EMIPlan.id.desc()).all()
    
    plans_list = []
    all_payments = []
    
    for plan in plans:
        # Calculate the payments and update status (paid/unpaid)
        payments_schedule = calculate_emi_schedule(plan)
        all_payments.extend(payments_schedule)
        
        # Calculate last due date for the summary table
        last_date = plan.start_date + relativedelta(months=+(plan.duration_months - 1))
        
        plans_list.append({
            "id": plan.id,
            "start_date": plan.start_date.isoformat(),
            "amount": plan.amount,
            "duration_months": plan.duration_months,
            "monthly_payment": plan.monthly_payment,
            "note": plan.note or "",
            "last_date": last_date.isoformat(),
            "total_paid": len([p for p in payments_schedule if p['is_paid']]),
            "total_payments": plan.duration_months
        })

    # Filter upcoming/unpaid payments for the first table
    upcoming_payments = [p for p in all_payments if not p['is_paid'] and p['due_date'] >= date.today().isoformat()]

    # Sort upcoming payments by due date
    upcoming_payments.sort(key=lambda x: x['due_date'])

    return jsonify({"plans": plans_list, "upcoming_payments": upcoming_payments})


@app.post("/api/emi/paid")
def mark_emi_paid():
    data = request.get_json(force=True)
    plan_id = int(data["plan_id"])
    due_date_str = data["due_date"]
    
    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()

    # Find or create the EMIPayment record
    payment = EMIPayment.query.filter_by(plan_id=plan_id, due_date=due_date).first()
    
    if not payment:
        payment = EMIPayment(plan_id=plan_id, due_date=due_date, paid=True)
        db.session.add(payment)
    else:
        # Toggle or ensure paid=True
        payment.paid = True

    db.session.commit()
    return jsonify({"status": "ok", "plan_id": plan_id, "due_date": due_date_str}), 200

@app.delete("/api/emi/<int:emi_id>")
def delete_emi_plan(emi_id):
    emi = EMIPlan.query.get_or_404(emi_id)
    db.session.delete(emi)
    db.session.commit()
    return jsonify({"status": "ok", "id": emi.id}), 200


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