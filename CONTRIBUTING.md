# Contributing

MAG lives in this repository. Read `AGENTS.md` first: it holds the rules for
models, deploys and the bots' execution contract.

- Branches are short-lived: open a pull request, merge when CI is green,
  delete the branch. `main` is what gets deployed.
- `bash ops/ci/check.sh`, `python3 ops/models/tests/test_modeld.py` and
  `python3 mag-room/tests/test_native.py` must pass.
- No secrets in code, pull requests or logs.
- Never delete a model file and never start or stop a model from a pull
  request, a deploy hook or a cleanup script; `mag-modeld` owns the models.
