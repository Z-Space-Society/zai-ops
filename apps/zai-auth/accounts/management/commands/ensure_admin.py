"""Idempotently create the local break-glass admin, for provision time.

  manage.py ensure_admin

Local username/password login only (via /admin/login/) — no ATProto
identity, no DID resolution. `did` is unique + required on the User model,
so this row gets a sentinel value (`did:local:admin`) that can never collide
with a real, resolvable atproto DID (those only ever start with `did:plc:`
or `did:web:`). The password is read from ZAI_AUTH_ADMIN_PASSWORD (never a
CLI arg — keeps it out of the process list, matching SECURITY.md's
"secrets never touch argv" posture) and is only ever set at creation:
re-running this command against an existing `admin` row never rotates it,
even if an operator has since changed it by hand.

is_staff/is_superuser ARE re-asserted on every run, unlike the password —
this row's whole purpose is a guaranteed way in, so a re-run of provisioning
(the documented recovery path) must heal it if something ever flipped those
flags off, rather than reporting "already exists" over a locked door.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

User = get_user_model()

ADMIN_USERNAME = "admin"
ADMIN_DID = "did:local:admin"


class Command(BaseCommand):
    help = "Create the local break-glass admin (username/password login only) if absent."

    def handle(self, *args, **opts):
        # Wrapped in a transaction so a missing-password failure rolls back the
        # get_or_create insert too — otherwise a failed first run leaves a
        # passwordless row behind, and every retry after that sees created=False
        # and never sets a password at all.
        with transaction.atomic():
            user, created = User.objects.get_or_create(
                username=ADMIN_USERNAME,
                defaults={
                    "did": ADMIN_DID,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )

            if not created and user.did != ADMIN_DID:
                # Some other row already owns this username (manual createsuperuser,
                # a bug, an operator typo) — refuse rather than silently treating a
                # stranger's row as the break-glass account forever.
                raise CommandError(
                    f"a user named {ADMIN_USERNAME!r} already exists with did "
                    f"{user.did!r}, not the break-glass sentinel {ADMIN_DID!r}"
                )

            if created:
                password = os.environ.get("ZAI_AUTH_ADMIN_PASSWORD")
                if not password:
                    raise CommandError(
                        "ZAI_AUTH_ADMIN_PASSWORD is required to create the admin user"
                    )
                user.set_password(password)
                user.save(update_fields=["password"])
                self.stdout.write(
                    self.style.SUCCESS(f"created break-glass admin {ADMIN_USERNAME!r}")
                )
                return

        healed_fields = []
        if not user.is_staff:
            user.is_staff = True
            healed_fields.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            healed_fields.append("is_superuser")
        if healed_fields:
            user.save(update_fields=healed_fields)
            self.stdout.write(
                self.style.SUCCESS(f"healed break-glass admin: {', '.join(healed_fields)}")
            )
            return

        self.stdout.write("break-glass admin unchanged")
