from django.test import TestCase, override_settings


@override_settings(PUBLIC_BASE_URL="https://auth.example.test")
class ClientMetadataTests(TestCase):
    def test_metadata_endpoint_is_schema_shaped(self):
        resp = self.client.get("/client-metadata.json")
        self.assertEqual(resp.status_code, 200)
        m = resp.json()
        # client_id IS the metadata URL (atproto requirement).
        self.assertEqual(
            m["client_id"], "https://auth.example.test/client-metadata.json"
        )
        self.assertEqual(m["token_endpoint_auth_method"], "private_key_jwt")
        self.assertEqual(m["token_endpoint_auth_signing_alg"], "ES256")
        self.assertTrue(m["dpop_bound_access_tokens"])
        self.assertEqual(m["application_type"], "web")
        self.assertIn(
            "https://auth.example.test/oauth/callback", m["redirect_uris"]
        )
        self.assertEqual(
            m["jwks_uri"], "https://auth.example.test/.well-known/jwks.json"
        )
        self.assertIn("atproto", m["scope"])
        self.assertIn("authorization_code", m["grant_types"])
