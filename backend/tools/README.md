# Backend Tools

Manual maintenance tools live here. They are not imported by `backend/app` and
are not part of the service runtime.

- `data/`: DuckDB, Tushare, and local data inspection probes.

Keep production-facing commands in `backend/` only when they are active entry
points, such as `run_okx_comprehensive.py`.
