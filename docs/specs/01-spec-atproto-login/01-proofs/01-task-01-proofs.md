# Proof Artifacts — Task 1.0: Project skeleton + DID-keyed identity model

Spec: [`01-spec-atproto-login.md`](../01-spec-atproto-login.md) · Task:
[`01-tasks-atproto-login.md`](../01-tasks-atproto-login.md) §1.0

Environment: Python 3.13.14, Django 5.2.15, Postgres (local, `:5432`),
`DATABASE_URL=postgres://localhost:5432/zai_auth`.

## CLI Output — fresh migrate succeeds

`makemigrations` generated the custom user model migration:

```text
Migrations for 'accounts':
  accounts/migrations/0001_initial.py
    + Create model User
```

`migrate` applies cleanly on a fresh database (custom `AUTH_USER_MODEL` first):

```text
Operations to perform:
  Apply all migrations: accounts, admin, auth, contenttypes, sessions
Running migrations:
  Applying contenttypes.0001_initial... OK
  ...
  Applying auth.0012_alter_user_first_name_max_length... OK
  Applying accounts.0001_initial... OK
  Applying admin.0001_initial... OK
  ...
  Applying sessions.0001_initial... OK
```

`manage.py check`:

```text
System check identified no issues (0 silenced).
```

## Test Results — identity contract

`.venv/bin/python manage.py test accounts -v2`:

```text
Found 5 test(s).
test_create_user_with_did ... ok
test_did_is_unique ... ok
test_handle_lives_in_username_and_is_mutable ... ok
test_str_prefers_handle_then_did ... ok
test_touch_last_seen ... ok
----------------------------------------------------------------------
Ran 5 tests in 0.010s

OK
```

Covers: DID-keyed creation with handle in `username`; DID uniqueness
(`IntegrityError` on duplicate); handle mutability with a fixed DID; `__str__`;
`last_seen` stamping.

## Screenshots — Django admin (textual equivalent)

> A live screenshot is a manual step (`runserver` + browser at
> `/admin/accounts/user/`). The equivalent below is captured programmatically via
> the Django test client against the dev database, asserting the admin renders the
> DID-keyed identity fields.

```text
admin changelist status: 200
changelist has did column: True
changelist shows member did: True
changelist shows pds_url: True
changelist shows last_seen value present: True
detail status: 200
detail has ATProto identity fieldset: True
detail shows pds_url field: True
```

(`did` renders as read-only text on the detail page — it's `editable=False` — and
is shown in full on the changelist `did` column above.)

## Configuration — env-driven, no secrets

`.env.example` (placeholders only; real `.env` is git-ignored) documents every
variable `settings.py` reads — `SECRET_KEY`, `DATABASE_URL`, `PUBLIC_BASE_URL`,
signing-key paths, and the OIDC client config. `AUTH_USER_MODEL = "accounts.User"`
is set before the first migration.

## Verification

| Requirement (spec Unit 1) | Evidence |
| ------------------------- | -------- |
| Django project under `apps/zai-auth/`, env-driven, no secrets committed | `settings.py` reads all config from env; `.env.example` placeholders; `.gitignore` excludes `.env`/`*.pem` |
| Custom `User` (`AbstractUser`) keyed by DID; `username`=handle, `pds_url`, `last_seen` | `accounts/models.py`; migration `0001_initial`; tests pass |
| Persist + migrate against the configured database | `migrate` output above (Postgres) |
| Expose the model in Django admin | admin changelist/detail evidence above |
