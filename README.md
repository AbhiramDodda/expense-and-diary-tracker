# Expense & Diary Tracker (Flask + Vue CDN)

Features
- REST API with Flask
- Vue (CDN) frontend, Tailwind UI
- Chart.js pie (monthly by category) and line (yearly totals)
- Encrypted diary entries at rest using Fernet
- Calendar view with daily expense totals and diary count
- SQLite for easy local dev

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# (optional) cp .env.example .env && set FERNET_KEY=<your 32-byte base64 urlsafe key>
python app.py
```

Open http://localhost:5000 in your browser.

## Notes on Encryption
- Diary entries are encrypted before storing in the DB using a Fernet key.
- Set a persistent `FERNET_KEY` environment variable in production, otherwise a volatile key is generated each run (you will not be able to decrypt past entries).
- To generate a key:
  ```python
  import base64, os
  print(base64.urlsafe_b64encode(os.urandom(32)).decode())
  ```

## API (selected)
- `POST /api/expenses` JSON `{date, category, amount, note?}`
- `GET /api/expenses?year=YYYY&month=MM`
- `GET /api/expenses/summary/monthly?year=YYYY&month=MM`
- `GET /api/expenses/summary/yearly?year=YYYY`
- `POST /api/diary` JSON `{date, content}`
- `GET /api/diary?date=YYYY-MM-DD` (or `year`, `month`)
- `GET /api/calendar/daily_totals?year=YYYY&month=MM`
