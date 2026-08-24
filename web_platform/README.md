# Blue Horizon Platform (Adel)

Full platform layer: backend APIs + User/Admin frontend for Agents, HITL, and Tickets.

## Structure

```
web_platform/
├── README.md
├── backend/
│   ├── app.py              # Flask / FastAPI-style entry
│   ├── routes_agents.py    # Agent list, start, status, resume
│   ├── routes_admin.py     # HITL decisions, tickets, tool permissions
│   └── services.py         # Shared service helpers
└── frontend/
    ├── index.html
    ├── app.js
    └── styles.css
```

## Run backend (example)

```bash
cd web_platform/backend
pip install flask flask-cors
python app.py
# → http://127.0.0.1:5050
```

## Run frontend

Open `frontend/index.html` in a browser, or serve statically:

```bash
cd web_platform/frontend
python -m http.server 8080
```

Point the frontend API base URL at the backend (see `app.js`).

## Delivery criteria covered

| Criterion | How |
|-----------|-----|
| HITL UI | Admin can review safety report, request changes or approve |
| Graph resume | Admin decision is applied via `resume_safety_incident` from current checkpoint |
| Ticket recovery | Admin can resolve failure tickets; workflow resumes without restart |
| Checkpoint resume | Shared `StateGraphRunner` + MySQL checkpoints (Mostafa base) |
