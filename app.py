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
    paid_dates = {p.due_date.isoformat() for p in plan.payments.all()}
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
    q1 = db.session.query(
        func.strftime("%Y-%m-%d", Expense.date),
        func.sum(Expense.amount)
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}",
        func.strftime("%m", Expense.date) == f"{month:02d}"
    ).group_by(func.strftime("%Y-%m-%d", Expense.date))
    expenses_map = {d: float(t) for d, t in q1}
    q2 = db.session.query(
        func.strftime("%Y-%m-%d", DiaryEntry.date),
        func.count(DiaryEntry.id)
    ).filter(
        func.strftime("%Y", DiaryEntry.date) == f"{year:04d}",
        func.strftime("%m", DiaryEntry.date) == f"{month:02d}"
    ).group_by(func.strftime("%Y-%m-%d", DiaryEntry.date))
    diary_map = {d: int(c) for d, c in q2}
    all_days = set(expenses_map.keys()) | set(diary_map.keys())
    result = [{
        "date": d,
        "total_expenses": expenses_map.get(d, 0.0),
        "diary_count": diary_map.get(d, 0)
    } for d in sorted(all_days)]
    return jsonify({"year": year, "month": month, "days": result})

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

@app.get("/api/summary/yearly_financials")
def yearly_financials():
    year = request.args.get("year", type=int, default=date.today().year)
    q_exp = db.session.query(
        func.strftime("%m", Expense.date).label("m"),
        func.sum(Expense.amount).label("total")
    ).filter(
        func.strftime("%Y", Expense.date) == f"{year:04d}"
    ).group_by("m")
    expenses_totals = {int(m): float(total) for m, total in q_exp}
    q_earn = db.session.query(
        func.strftime("%m", Earning.date).label("m"),
        func.sum(Earning.amount).label("total")
    ).filter(
        func.strftime("%Y", Earning.date) == f"{year:04d}"
    ).group_by("m")
    earnings_totals = {int(m): float(total) for m, total in q_earn}
    monthly_expenses = [expenses_totals.get(m, 0.0) for m in range(1, 13)]
    monthly_earnings = [earnings_totals.get(m, 0.0) for m in range(1, 13)]
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
    plans = EMIPlan.query.order_by(EMIPlan.start_date.desc(), EMIPlan.id.desc()).all()
    plans_list = []
    finished_list = []
    all_payments = []
    for plan in plans:
        # Calculate the payments and update status (paid/unpaid)
        payments_schedule = calculate_emi_schedule(plan)
        total_paid = len([p for p in payments_schedule if p['is_paid']])
        last_date = plan.start_date + relativedelta(months=+(plan.duration_months - 1))

        # A plan whose every scheduled installment is paid off is "finished" and
        # gets pushed to a separate archive instead of the active summary/upcoming.
        if plan.duration_months > 0 and total_paid >= plan.duration_months:
            finished_list.append({
                "id": plan.id,
                "note": plan.note or "",
                "total_amount": round(plan.monthly_payment * plan.duration_months, 2),
                "monthly_payment": plan.monthly_payment,
                "months": plan.duration_months,
                "start_date": plan.start_date.isoformat(),
                "end_date": last_date.isoformat()
            })
            continue

        all_payments.extend(payments_schedule)
        plans_list.append({
            "id": plan.id,
            "start_date": plan.start_date.isoformat(),
            "amount": plan.amount,
            "duration_months": plan.duration_months,
            "monthly_payment": plan.monthly_payment,
            "note": plan.note or "",
            "last_date": last_date.isoformat(),
            "total_paid": total_paid,
            "total_payments": plan.duration_months
        })
    upcoming_payments = [p for p in all_payments if not p['is_paid'] and p['due_date'] >= date.today().isoformat()]
    upcoming_payments.sort(key=lambda x: x['due_date'])
    finished_list.sort(key=lambda x: x['end_date'], reverse=True)
    return jsonify({
        "plans": plans_list,
        "finished_plans": finished_list,
        "upcoming_payments": upcoming_payments
    })
 
@app.post("/api/emi/paid")
def mark_emi_paid():
    data = request.get_json(force=True)
    plan_id = int(data["plan_id"])
    due_date_str = data["due_date"]
    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
    plan = EMIPlan.query.get_or_404(plan_id)
    payment = EMIPayment.query.filter_by(plan_id=plan_id, due_date=due_date).first()
    if not payment:
        payment = EMIPayment(plan_id=plan_id, due_date=due_date, paid=True)
        db.session.add(payment)
    else:
        if payment.paid:
             return jsonify({"status": "ok", "message": "Already paid"}), 200
        payment.paid = True
    category_name = f"EMI - {plan.note}" if plan.note else "EMI"
    new_expense = Expense(
        date=due_date,
        category=category_name,
        amount=plan.monthly_payment,
        note=f"Auto-generated payment for EMI Plan #{plan.id}"
    )
    db.session.add(new_expense)
    db.session.commit()
    return jsonify({"status": "ok", "plan_id": plan_id, "due_date": due_date_str}), 200

@app.put("/api/emi/<int:emi_id>")
def edit_emi_plan(emi_id):
    """
    Edit an EMI plan mid-way.
      remaining          = plan.amount - (paid_count * plan.monthly_payment)
      new_monthly_payment = remaining / new_duration   (i.e. (x-y)/period)
    """
    data = request.get_json(force=True)
    plan = EMIPlan.query.get_or_404(emi_id)

    # ── read everything we need BEFORE touching the plan object ──
    paid_count   = EMIPayment.query.filter_by(plan_id=emi_id, paid=True).count()
    remaining    = plan.amount - (paid_count * plan.monthly_payment)   # x - y

    # compute the current full schedule so we can find the first unpaid date
    schedule_before = calculate_emi_schedule(plan)   # uses the ORIGINAL values

    # ── validate ──
    if remaining <= 0:
        return jsonify({"status": "error", "message": "Plan is already fully paid."}), 400

    new_duration = int(data.get("duration_months", plan.duration_months - paid_count))
    if new_duration <= 0:
        return jsonify({"status": "error", "message": "New duration must be > 0."}), 400

    new_monthly = round(remaining / new_duration, 2)

    # ── find the first unpaid due-date from the schedule we already captured ──
    first_unpaid_date = None
    for slot in schedule_before:
        if not slot["is_paid"]:
            first_unpaid_date = datetime.strptime(slot["due_date"], "%Y-%m-%d").date()
            break
    if first_unpaid_date is None:
        last_paid = plan.start_date + relativedelta(months=+(paid_count - 1))
        first_unpaid_date = last_paid + relativedelta(months=+1)

    # ── NOW mutate the plan ──
    plan.amount          = round(remaining, 2)
    plan.duration_months = paid_count + new_duration
    plan.monthly_payment = new_monthly
    # shift start_date so the generator rebuilds unpaid slots from first_unpaid_date
    plan.start_date      = first_unpaid_date - relativedelta(months=+paid_count)
    if "note" in data:
        plan.note = data["note"]

    db.session.commit()

    return jsonify({
        "status": "ok",
        "id": plan.id,
        "remaining": plan.amount,
        "new_monthly_payment": new_monthly,
        "new_duration_months": new_duration,
        "paid_count": paid_count
    }), 200

@app.delete("/api/emi/<int:emi_id>")
def delete_emi_plan(emi_id):
    emi = EMIPlan.query.get_or_404(emi_id)
    db.session.delete(emi)
    db.session.commit()
    return jsonify({"status": "ok", "id": emi.id}), 200

@app.get("/api/summary/rolling_12_months")
def rolling_12_months():
    today = date.today()
    months_data = []
    for i in range(11, -1, -1):  # 11 months ago to current month
        target_date = today - relativedelta(months=i)
        year = target_date.year
        month = target_date.month
        exp_total = db.session.query(
            func.sum(Expense.amount)
        ).filter(
            func.strftime("%Y", Expense.date) == f"{year:04d}",
            func.strftime("%m", Expense.date) == f"{month:02d}"
        ).scalar() or 0.0
        earn_total = db.session.query(
            func.sum(Earning.amount)
        ).filter(
            func.strftime("%Y", Earning.date) == f"{year:04d}",
            func.strftime("%m", Earning.date) == f"{month:02d}"
        ).scalar() or 0.0
        months_data.append({
            "month": f"{year}-{month:02d}",
            "expenses": float(exp_total),
            "earnings": float(earn_total),
            "profit": float(earn_total - exp_total)
        })
    return jsonify({"data": months_data})

@app.get("/api/summary/lifetime_stats")
def lifetime_stats():
    total_expenses = db.session.query(
        func.sum(Expense.amount)
    ).scalar() or 0.0
    total_earnings = db.session.query(
        func.sum(Earning.amount)
    ).scalar() or 0.0
    first_expense = db.session.query(
        func.min(Expense.date)
    ).scalar()
    first_earning = db.session.query(
        func.min(Earning.date)
    ).scalar()
    dates = [d for d in [first_expense, first_earning] if d]
    earliest_date = min(dates).isoformat() if dates else None
    
    return jsonify({
        "total_expenses": float(total_expenses),
        "total_earnings": float(total_earnings),
        "lifetime_profit": float(total_earnings - total_expenses),
        "earliest_date": earliest_date,
        "latest_date": date.today().isoformat()
    })

@app.get("/api/analytics/summary")
def analytics_summary():
    """High-level analytics used by the advanced dashboard cards."""
    total_expenses = db.session.query(func.sum(Expense.amount)).scalar() or 0.0
    total_earnings = db.session.query(func.sum(Earning.amount)).scalar() or 0.0
    expense_count = db.session.query(func.count(Expense.id)).scalar() or 0

    # How many calendar months of history we have, so averages are meaningful.
    first_exp = db.session.query(func.min(Expense.date)).scalar()
    first_earn = db.session.query(func.min(Earning.date)).scalar()
    dates = [d for d in [first_exp, first_earn] if d]
    if dates:
        earliest = min(dates)
        today = date.today()
        months_tracked = (today.year - earliest.year) * 12 + (today.month - earliest.month) + 1
    else:
        months_tracked = 1
    months_tracked = max(months_tracked, 1)

    biggest = Expense.query.order_by(Expense.amount.desc()).first()
    biggest_expense = {
        "amount": biggest.amount,
        "category": biggest.category,
        "date": biggest.date.isoformat(),
        "note": biggest.note or ""
    } if biggest else None

    top_cat = db.session.query(
        Expense.category, func.sum(Expense.amount).label("t")
    ).group_by(Expense.category).order_by(func.sum(Expense.amount).desc()).first()
    top_category = {"category": top_cat[0], "total": float(top_cat[1])} if top_cat else None

    # Outstanding EMI = sum of unpaid installments across active (unfinished) plans.
    emi_outstanding = 0.0
    active_emi = 0
    finished_emi = 0
    for plan in EMIPlan.query.all():
        schedule = calculate_emi_schedule(plan)
        total_paid = len([p for p in schedule if p['is_paid']])
        if plan.duration_months > 0 and total_paid >= plan.duration_months:
            finished_emi += 1
        else:
            active_emi += 1
            emi_outstanding += max(plan.monthly_payment * (plan.duration_months - total_paid), 0.0)

    return jsonify({
        "total_expenses": float(total_expenses),
        "total_earnings": float(total_earnings),
        "expense_count": int(expense_count),
        "months_tracked": months_tracked,
        "avg_monthly_expense": float(total_expenses) / months_tracked,
        "avg_monthly_earning": float(total_earnings) / months_tracked,
        "avg_expense": (float(total_expenses) / expense_count) if expense_count else 0.0,
        "biggest_expense": biggest_expense,
        "top_category": top_category,
        "emi_outstanding": round(emi_outstanding, 2),
        "active_emi": active_emi,
        "finished_emi": finished_emi
    })

@app.get("/api/analytics/category_trends")
def category_trends():
    """Per-category expense totals for the last N months (stacked bar)."""
    months = request.args.get("months", type=int, default=6)
    months = max(1, min(months, 24))
    today = date.today()
    month_keys = [
        (today - relativedelta(months=i)).strftime("%Y-%m")
        for i in range(months - 1, -1, -1)
    ]
    start = (today - relativedelta(months=months - 1)).replace(day=1)

    rows = db.session.query(
        func.strftime("%Y-%m", Expense.date),
        Expense.category,
        func.sum(Expense.amount)
    ).filter(Expense.date >= start).group_by(
        func.strftime("%Y-%m", Expense.date), Expense.category
    ).all()

    cat_totals = {}
    data_map = {}
    for m, cat, total in rows:
        cat_totals[cat] = cat_totals.get(cat, 0.0) + float(total)
        data_map[(m, cat)] = float(total)

    # Only chart the top categories to keep the stack readable.
    top_cats = sorted(cat_totals, key=cat_totals.get, reverse=True)[:6]
    datasets = [
        {"category": cat, "data": [data_map.get((m, cat), 0.0) for m in month_keys]}
        for cat in top_cats
    ]
    return jsonify({"months": month_keys, "datasets": datasets})

@app.get("/api/analytics/weekday_spending")
def weekday_spending():
    """Total and average spend grouped by day of the week."""
    rows = db.session.query(
        func.strftime("%w", Expense.date),
        func.sum(Expense.amount),
        func.count(Expense.id)
    ).group_by(func.strftime("%w", Expense.date)).all()
    totals = {int(w): float(t) for w, t, c in rows}
    counts = {int(w): int(c) for w, t, c in rows}
    # SQLite %w: 0=Sunday..6=Saturday; reorder to Mon..Sun for display.
    order = [1, 2, 3, 4, 5, 6, 0]
    names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    return jsonify({
        "labels": names,
        "totals": [totals.get(w, 0.0) for w in order],
        "averages": [(totals.get(w, 0.0) / counts[w]) if counts.get(w) else 0.0 for w in order]
    })

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