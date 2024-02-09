#!/usr/bin/env python3

import base64
import dataclasses
import hashlib
import secrets
import urllib.parse

import jwt

import not_my_board._http as http


@dataclasses.dataclass
class IdentityProvider:
    authorization_endpoint: str
    token_endpoint: str
    issuer: str
    jwks_uri: str

    @classmethod
    async def from_url(cls, issuer_url):
        config_url = urllib.parse.urljoin(
            f"{issuer_url}/", ".well-known/openid-configuration"
        )
        config = await http.get_json(config_url)

        init_args = {
            field.name: config[field.name] for field in dataclasses.fields(cls)
        }
        return cls(**init_args)


class Client:
    def __init__(self, client_id, identity_provider, redirect_uri):
        self._client_id = client_id
        self._identity_provider = identity_provider
        self._redirect_uri = redirect_uri
        self._state = secrets.token_urlsafe()
        self._nonce = secrets.token_urlsafe()
        self._code_verifier = secrets.token_urlsafe()

        hashed = hashlib.sha256(self._code_verifier.encode()).digest()
        code_challange = base64.urlsafe_b64encode(hashed).rstrip(b"=").decode("ascii")

        auth_params = {
            "scope": "openid profile offline_access",
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": self._state,
            "nonce": self._nonce,
            "prompt": "consent",
            "code_challenge": code_challange,
            "code_challenge_method": "S256",
        }

        url_parts = list(
            urllib.parse.urlparse(self._identity_provider.authorization_endpoint)
        )
        query = dict(urllib.parse.parse_qsl(url_parts[4]))
        query.update(auth_params)

        url_parts[4] = urllib.parse.urlencode(query)

        self._login_url = urllib.parse.urlunparse(url_parts)

    @property
    def state(self):
        return self._state

    @property
    def login_url(self):
        return self._login_url

    async def request_tokens(self, auth_response):
        if "error" in auth_response:
            if "error_description" in auth_response:
                msg = f'{auth_response["error_description"]} ({auth_response["error"]})'
            else:
                msg = auth_response["error"]

            raise ProtocolError(f"Authentication error: {msg}")

        url = self._identity_provider.token_endpoint
        params = {
            "grant_type": "authorization_code",
            "code": auth_response["code"],
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "code_verifier": self._code_verifier,
        }
        response = await http.post_form(url, params)

        if response["token_type"].lower() != "bearer":
            raise ProtocolError(
                f'Expected token type "Bearer", got "{response["token_type"]}"'
            )

        id_token = verify(response["id_token"], self._client_id)
        if id_token["nonce"] != self._nonce:
            raise ProtocolError(
                "Nonce in the ID token doesn't match the one in the authorization request"
            )

        return response


async def verify(token, client_id):
    unverified_token = jwt.api_jwt.decode_complete(
        token, options={"verify_signature": False}
    )
    kid = unverified_token["header"]["kid"]
    issuer = unverified_token["payload"]["iss"]

    identity_provider = await IdentityProvider.from_url(issuer)
    jwk_set_raw = await http.get_json(identity_provider.jwks_uri)
    jwk_set = jwt.PyJWKSet.from_dict(jwk_set_raw)

    for key in jwk_set.keys:
        if key.public_key_use in ["sig", None] and key.key_id == kid:
            signing_key = key
            break
    else:
        raise ProtocolError(f'Unable to find a signing key that matches "{kid}"')

    return jwt.decode(
        token,
        key=signing_key.key,
        algorithms="RS256",
        audience=client_id,
    )


async def ensure_fresh(tokens, identity_provider, client_id):
    try:
        verify(tokens["id_token"], client_id)
        return tokens
    except Exception:
        params = {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        }
        new_tokens = await http.post_form(identity_provider.token_endpoint, params)
        verify(new_tokens["id_token"], client_id)
        return new_tokens


class ProtocolError(Exception):
    pass
