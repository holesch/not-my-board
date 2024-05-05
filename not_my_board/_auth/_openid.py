#!/usr/bin/env python3

import base64
import dataclasses
import hashlib
import secrets
import urllib.parse

import jwt


@dataclasses.dataclass
class IdentityProvider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str

    @classmethod
    async def from_url(cls, issuer_url, http_client):
        config_url = urllib.parse.urljoin(
            f"{issuer_url}/", ".well-known/openid-configuration"
        )
        config = await http_client.get_json(config_url)

        init_args = {
            field.name: config[field.name] for field in dataclasses.fields(cls)
        }
        return cls(**init_args)


@dataclasses.dataclass
class AuthRequest:
    client_id: str
    redirect_uri: str
    state: str
    nonce: str
    code_verifier: str
    identity_provider: IdentityProvider

    @classmethod
    async def create(cls, issuer_url, client_id, redirect_uri, http_client):
        identity_provider = await IdentityProvider.from_url(issuer_url, http_client)
        state = secrets.token_urlsafe()
        nonce = secrets.token_urlsafe()
        code_verifier = secrets.token_urlsafe()

        return cls(
            client_id, redirect_uri, state, nonce, code_verifier, identity_provider
        )

    @property
    def login_url(self):
        hashed = hashlib.sha256(self.code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(hashed).rstrip(b"=").decode("ascii")

        auth_params = {
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        url_parts = list(
            urllib.parse.urlparse(self.identity_provider.authorization_endpoint)
        )
        query = dict(urllib.parse.parse_qsl(url_parts[4]))
        query.update(auth_params)

        url_parts[4] = urllib.parse.urlencode(query)

        return urllib.parse.urlunparse(url_parts)

    async def request_tokens(self, auth_response, http_client):
        if "error" in auth_response:
            if "error_description" in auth_response:
                msg = f'{auth_response["error_description"]} ({auth_response["error"]})'
            else:
                msg = auth_response["error"]

            raise RuntimeError(f"Authentication error: {msg}")

        url = self.identity_provider.token_endpoint
        params = {
            "grant_type": "authorization_code",
            "code": auth_response["code"],
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": self.code_verifier,
        }
        response = await http_client.post_form(url, params)

        if response["token_type"].lower() != "bearer":
            raise RuntimeError(
                f'Expected token type "Bearer", got "{response["token_type"]}"'
            )

        claims = await verify(response["id_token"], self.client_id, http_client)
        if claims["nonce"] != self.nonce:
            raise RuntimeError(
                "Nonce in the ID token doesn't match the one in the authorization request"
            )

        return response["id_token"], response["refresh_token"], claims


async def verify(token, client_id, http_client):
    unverified_token = jwt.api_jwt.decode_complete(
        token, options={"verify_signature": False}
    )
    kid = unverified_token["header"]["kid"]
    issuer = unverified_token["payload"]["iss"]

    identity_provider = await IdentityProvider.from_url(issuer, http_client)
    jwk_set_raw = await http_client.get_json(identity_provider.jwks_uri)
    jwk_set = jwt.PyJWKSet.from_dict(jwk_set_raw)

    for key in jwk_set.keys:
        if key.public_key_use in ["sig", None] and key.key_id == kid:
            signing_key = key
            break
    else:
        raise RuntimeError(f'Unable to find a signing key that matches "{kid}"')

    return jwt.decode(
        token,
        key=signing_key.key,
        algorithms="RS256",
        audience=client_id,
    )
