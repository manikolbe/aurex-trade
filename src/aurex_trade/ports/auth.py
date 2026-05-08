"""OAuth provider port — defines the contract for OAuth authentication providers."""

from typing import Protocol

from aurex_trade.domain.models import OAuthUserInfo


class OAuthProviderPort(Protocol):
    """Any OAuth provider (Google, Facebook, GitHub) implements this."""

    @property
    def name(self) -> str: ...

    def get_authorization_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> OAuthUserInfo: ...
