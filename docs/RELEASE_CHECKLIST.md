# Release Checklist

- [x] Version synchronized across package, API, README, lock file, and changelog.
- [x] `.env`, credentials, caches, and local databases are ignored.
- [x] Full pytest suite passes.
- [x] Ruff and Pyright strict checks pass.
- [x] Frontend JavaScript syntax check passes.
- [x] Offline 120-case evaluation passes and report is committed.
- [x] API health, session, revision, lock, replace, undo, and explanation flows are tested.
- [x] Web Demo works without provider or OpenAI credentials.
- [ ] Docker image build is run on a machine with Docker available.
- [ ] Public deployment URL and release screenshots are added when hosting is selected.
