# Synchronous local GLB import on Linux

Use Python 3.12+ and a trusted, explicitly selected Blender binary. Tested on
Linux x64 with Blender 5.3.0 release build 02f0899d78e3. This change does not
qualify Windows behavior on Linux: provider calls, financial/transaction
operations, detached submissions, and job cancel are not enabled. Interrupt the
foreground CLI to cancel. Windows still uses its existing supervisor and guards.

Use an **absolute**, owner-only jobs root (0700, or a new directory underneath
an existing trusted parent) and a local filesystem. Supply a static, unrigged
GLB v2 with embedded resources and a precomputed SHA-256. A minimal params file:

```json
{"manifest":{"schema_version":"1.1","provider":"local","operation":"import_local",
 "input":{"file":"/absolute/model.glb","expected_sha256":"REPLACE_WITH_64_HEX_DIGITS"},
 "preview":{"enabled":false}}}
```

```sh
python3 scripts/modeling_workflow.py --blender /absolute/blender --jobs-dir /absolute/private/jobs modeling run --params /absolute/params.json
```

Check exit status **and** JSON `ok`. On success, consume the top-level
`modeling-report.json` from the returned job directory. Its candidate was saved,
reopened, independently read in a second Blender process, and rehashed. The
report distinguishes original input and its job-private snapshot. Panel adoption
is a separate operation. Preserve the whole job. The internal `worker/` tree
is intermediate evidence, not a successful host acceptance receipt. No visual
quality, topology suitability, or production asset approval follows from this test.

## Protection boundary

* Never modifies the input or reuses an existing job. Local source paths are
  opened component-by-component without following symlinks. Single-link regular
  files only, maximum GLB size 256 MiB. Blender receives a hash-checked private
  snapshot rather than the original path. Before acceptance, the host reopens
  the original and checks inode, size, change timestamps and digest.
* The worker executes a private copy of the runtime Python sources, checked
  against the requested implementation identity by both worker and verifier.
  Jobs and their configuration live beneath the owner-private root. Use trusted
  program binaries, a trusted same-user environment and a trusted local filesystem.
  No protection against a hostile process running as the same user is claimed.
* The Blender worker starts with factory settings, disabled automatic scripts,
  offline mode, private HOME/profile and a restricted inherited environment.
  These switches are **not** a network namespace, resource quota or sandbox.
  An ordinary Blender reader inherits the worker process group. On observed
  timeout or foreground INT/TERM/HUP the supervisor kills that group, including
  an orphaned ordinary reader. A process escaping its group, supervisor SIGKILL,
  power loss or disk exhaustion is **not** equivalent to Windows Job-object
  kill-on-close; inspect partial results rather than treating them as accepted.
* No async supervisor recovery, provider fallback, or cross-platform release
  equivalence. Run `python3 tests/linux_local_smoke.py` for offline guard tests;
  add `--blender /absolute/blender` for the generated GLB end-to-end test.
