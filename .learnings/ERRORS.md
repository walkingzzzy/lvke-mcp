# Errors

Command failures and integration errors.

---

## [ERR-20260807-002] source-layout Python diagnostics

**Logged**: 2026-08-07T00:40:47+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
Ad hoc system-Python schema introspection failed because the repository uses a `src` layout and project-local dependencies.

### Error
```
ModuleNotFoundError: No module named 'lvke_mcp'
ModuleNotFoundError: No module named 'mcp'
```

### Context
- Command: inline Python import of `lvke_mcp.servers.lvke_deep_research.server`.
- Environment: system Python outside the repository virtual environment.

### Suggested Fix
Use `.venv/bin/python` with `PYTHONPATH=src` for repository module diagnostics.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_deep_research/server.py

### Resolution
- **Resolved**: 2026-08-07T00:40:47+08:00
- **Notes**: Repository virtual environment and source path were identified before retrying.

---

## [ERR-20260807-001] py_compile cache permission

**Logged**: 2026-08-07T00:28:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
System Python could not create its default bytecode cache while compiling workspace files.

### Error
```
PermissionError: [Errno 1] Operation not permitted: /Users/mac/Library/Caches/com.apple.python/...
```

### Context
- Command: `python3 -m py_compile ...`
- Environment: managed Codex workspace on macOS.

### Suggested Fix
Set `PYTHONPYCACHEPREFIX` to a writable temporary directory for syntax checks.

### Metadata
- Reproducible: yes
- Related Files: src/lvke_mcp/servers/lvke_deep_research/server.py

### Resolution
- **Resolved**: 2026-08-07T00:28:00+08:00
- **Notes**: Re-run with `PYTHONPYCACHEPREFIX=/tmp/lvke-pycache`.

---
