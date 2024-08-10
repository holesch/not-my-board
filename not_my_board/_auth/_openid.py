#!/usr/bin/env python3

import base64
import dataclasses
import hashlib
import secrets
import urllib.parse

import jwt

import not_my_board._http as http


@dataclasses.dataclass
class IdentityProviderMinimal:
    issuer: str
    jwks_uri: str

    @classmethod
    async def from_url(cls, issuer_url, http_client, cache=None):
        config_url = urllib.parse.urljoin(
            f"{issuer_url}/", ".well-known/openid-configuration"
        )
        config = await http_client.get_json(config_url, cache=cache)

        init_args = {
            field.name: config[field.name] for field in dataclasses.fields(cls)
        }
        return cls(**init_args)


@dataclasses.dataclass
class IdentityProvider(IdentityProviderMinimal):
    authorization_endpoint: str
    token_endpoint: str


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

        validator = Validator(self.client_id, http_client)
        claims = await validator.extract_claims(response["id_token"])
        if claims["nonce"] != self.nonce:
            raise RuntimeError(
                "Nonce in the ID token doesn't match the one in the authorization request"
            )

        return response["id_token"], response["refresh_token"], claims


async def ensure_fresh(id_token, refresh_token, http_client):
    if _needs_refresh(id_token):
        claims = jwt.decode(id_token, options={"verify_signature": False})
        issuer_url = claims["iss"]
        client_id = claims["aud"]
        identity_provider = await IdentityProvider.from_url(issuer_url, http_client)

        params = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        response = await http_client.post_form(identity_provider.token_endpoint, params)
        return response["id_token"], response["refresh_token"]
    else:
        return id_token, refresh_token


def _needs_refresh(id_token):
    try:
        jwt.decode(
            id_token,
            options={
                "verify_signature": False,
                "require": ["iss", "sub", "aud", "exp", "iat"],
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
            },
        )
    except Exception:
        return True
    return False


class Validator:
    def __init__(self, client_id, http_client, trusted_issuers=None):
        self._client_id = client_id
        self._http = http_client
        self._trusted_issuers = trusted_issuers
        if trusted_issuers is not None:
            self._caches = {
                issuer: (http.CacheEntry(), http.CacheEntry())
                for issuer in trusted_issuers
            }

    async def extract_claims(self, id_token, leeway=0):
        unverified_token = jwt.api_jwt.decode_complete(
            id_token, options={"verify_signature": False}
        )
        key_id = unverified_token["header"]["kid"]
        issuer = unverified_token["payload"]["iss"]

        if self._trusted_issuers is not None:
            if issuer not in self._trusted_issuers:
                raise RuntimeError(f"Unknown issuer: {issuer}")

            idp_cache, jwk_cache = self._caches[issuer]
        else:
            idp_cache = jwk_cache = None

        identity_provider = await IdentityProviderMinimal.from_url(
            issuer, self._http, idp_cache
        )
        jwk_set_raw = await self._http.get_json(identity_provider.jwks_uri, jwk_cache)
        jwk_set = jwt.PyJWKSet.from_dict(jwk_set_raw)

        for key in jwk_set.keys:
            if key.public_key_use in ["sig", None] and key.key_id == key_id:
                signing_key = key
                break
        else:
            raise RuntimeError(f'Unable to find a signing key that matches "{key_id}"')

        return jwt.decode(
            id_token,
            key=signing_key.key,
            algorithms="RS256",
            audience=self._client_id,
            options={"require": ["sub", "exp", "iat"]},
            leeway=leeway,
        )
