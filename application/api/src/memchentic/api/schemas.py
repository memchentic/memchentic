"""Request bodies and constrained field types for the REST API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

from memchentic.auth.passwords import password_too_long
from memchentic.services import limits
from memchentic.services.write_target import Visibility

TitleStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=limits.TITLE_MAX)
]


BodyStr = Annotated[str, StringConstraints(min_length=1, max_length=limits.BODY_MAX)]


TagStr = Annotated[str, StringConstraints(max_length=limits.TAG_MAX_LEN)]


TagList = Annotated[list[TagStr], Field(max_length=limits.TAG_MAX_COUNT)]


IdStr = Annotated[str, StringConstraints(max_length=limits.ID_TEXT_MAX)]


DatetimeStr = Annotated[str, StringConstraints(max_length=limits.DATETIME_TEXT_MAX)]


TypeStr = Annotated[str, StringConstraints(max_length=limits.TYPE_TEXT_MAX)]


SpaceNameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=limits.SPACE_NAME_MAX)
]


KeyNameStr = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=limits.API_KEY_NAME_MAX)
]


EmailText = Annotated[str, StringConstraints(max_length=limits.EMAIL_MAX)]


TtlDays = Annotated[int, Field(ge=0, le=limits.TTL_DAYS_MAX)]


class RegisterIn(BaseModel):
    """Request body for internal-mode registration."""

    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = Field(default=None, max_length=limits.DISPLAY_NAME_MAX)

    @field_validator("password")
    @classmethod
    def _password_fits_bcrypt(cls, value: str) -> str:
        if password_too_long(value):
            raise ValueError("password must be at most 72 bytes when encoded as UTF-8")
        return value


class LoginIn(BaseModel):
    """Request body for email and password login."""

    email: str = Field(min_length=1, max_length=limits.EMAIL_MAX)
    password: str = Field(min_length=1, max_length=limits.PASSWORD_INPUT_MAX)
    remember: bool = False


class RememberIn(BaseModel):
    """Request body for creating an entry."""

    title: TitleStr
    body: BodyStr
    type: TypeStr = "note"
    tags: TagList = Field(default_factory=list)
    space_id: IdStr | None = None
    group: str | None = Field(default=None, max_length=limits.SPACE_NAME_MAX)
    visibility: Visibility | None = None
    expires_at: DatetimeStr | None = None
    ttl_days: TtlDays | None = None
    permanent: bool = False
    path_hint: str | None = Field(default=None, max_length=limits.PATH_HINT_MAX)
    repo_hint: str | None = Field(default=None, max_length=limits.REPO_HINT_MAX)
    force: bool = False


class MeIn(BaseModel):
    """Request body for updating the caller's preferences."""

    default_shared_space_id: IdStr | None = None


class ReviseIn(BaseModel):
    """Request body for patching an entry or guide."""

    title: TitleStr | None = None
    body: BodyStr | None = None
    type: TypeStr | None = None
    tags: TagList | None = None
    expires_at: DatetimeStr | None = None
    clear_expiry: bool = False
    ttl_days: TtlDays | None = None
    permanent: bool = False
    path_hint: str | None = Field(default=None, max_length=limits.PATH_HINT_MAX)
    repo_hint: str | None = Field(default=None, max_length=limits.REPO_HINT_MAX)


class GroupIn(BaseModel):
    """Request body for creating or renaming a group."""

    name: SpaceNameStr


class MemberIn(BaseModel):
    """Request body for adding a group member by user id or email."""

    user_id: IdStr | None = None
    email: EmailText | None = None


class ApiKeyIn(BaseModel):
    """Request body for creating an API key."""

    name: KeyNameStr = "default"
    user_id: IdStr | None = None


class ApiKeyCycleIn(BaseModel):
    """Replace a user's active keys with one new random secret."""

    user_id: IdStr | None = None
    name: KeyNameStr = "default"


class GuideIn(BaseModel):
    """Request body for writing a guide."""

    title: TitleStr
    body: BodyStr
    tags: TagList = Field(default_factory=list)
    space_id: IdStr | None = None
    group: str | None = Field(default=None, max_length=limits.SPACE_NAME_MAX)
    visibility: Visibility | None = None
    expires_at: DatetimeStr | None = None
    ttl_days: TtlDays | None = None
    permanent: bool = False


class AuthGroupMemberIn(BaseModel):
    """Request body for adding a user to an auth group."""

    user_id: IdStr


class AuthGroupIdpBindIn(BaseModel):
    """Request body for binding an auth group to an IdP group."""

    idp_group_id: str | None = Field(default=None, max_length=limits.IDP_GROUP_MAX)
    idp_group_name: str | None = Field(default=None, max_length=limits.IDP_GROUP_MAX)


class RegistrationSettingIn(BaseModel):
    """Request body for turning self-registration on or off."""

    enabled: bool
