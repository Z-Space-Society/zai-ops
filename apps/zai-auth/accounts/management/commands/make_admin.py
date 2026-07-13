"""Promote an ATProto handle to zai-auth admin, keyed on DID.

  manage.py make_admin alice.bsky.social
  manage.py make_admin alice.bsky.social --did did:plc:abc123   # skip resolution

Resolves the handle to a DID (DNS TXT, then HTTPS well-known — see
atproto_oauth.client.resolve_handle_for_admin) and verifies it by checking
the DID document's alsoKnownAs actually lists that handle, unless --did is
given, which trusts the operator and skips both steps entirely.

Keys on `did` — the same field atproto_oauth.views._upsert_member keys on —
so a later ATProto OAuth login with this DID lands on the exact row this
command creates or promotes. No password is ever set here (set_unusable_password
on creation): this row only ever authenticates via ATProto OAuth.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from atproto_oauth import client

User = get_user_model()


class Command(BaseCommand):
    help = "Promote an ATProto handle to zai-auth admin (is_staff + is_superuser), keyed on DID."

    def add_arguments(self, parser):
        parser.add_argument("handle", help="ATProto handle, e.g. alice.bsky.social")
        parser.add_argument(
            "--did",
            default=None,
            help="Skip handle resolution/verification; use this DID directly.",
        )

    def handle(self, *args, **opts):
        handle = opts["handle"].strip().lstrip("@")
        did = opts["did"]

        if did:
            did = did.strip()
        else:
            try:
                did = client.resolve_handle_for_admin(handle)
                doc = client.fetch_did_document(did)
            except client.OAuthError as exc:
                raise CommandError(str(exc)) from exc
            resolved_handle = client.handle_from_doc(doc)
            if resolved_handle != handle:
                raise CommandError(
                    f"DID document for {did} does not list handle {handle!r} "
                    f"in alsoKnownAs (found {resolved_handle!r})"
                )

        user, created = User.objects.get_or_create(
            did=did,
            defaults={
                "username": handle,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"created admin {handle!r} ({did})"))
            return

        changed_fields = []
        if user.username != handle:
            user.username = handle
            changed_fields.append("username")
        if not user.is_staff:
            user.is_staff = True
            changed_fields.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            changed_fields.append("is_superuser")

        if changed_fields:
            user.save(update_fields=changed_fields)
            self.stdout.write(
                self.style.SUCCESS(f"promoted {handle!r} ({did}) to admin")
            )
        else:
            self.stdout.write(f"{handle!r} ({did}) is already admin")
