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
    project_id: IdStr | None = None
    supersedes_id: IdStr | None = None
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
    project_id: IdStr | None = None
    supersedes_id: IdStr | None = None


class GroupIn(BaseModel):
    """Request body for creating or renaming a group."""

    name: SpaceNameStr


class MemberIn(BaseModel):
    """Request body for adding a group member by user id or email."""

    user_id: IdStr | None = None
    email: EmailText | None = None


class MemberRoleIn(BaseModel):
    """Request body for promoting or demoting a group member."""

    role: str


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
    project_id: IdStr | None = None
    supersedes_id: IdStr | None = None


class ProjectIn(BaseModel):
    """Request body for creating a project."""

    name: SpaceNameStr
    space_id: IdStr


class ProjectPatch(BaseModel):
    """Request body for renaming a project."""

    name: SpaceNameStr


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


class AutomationSettingIn(BaseModel):
    """Request body for hygiene Automation System Settings.

    Omitted fields are left unchanged.
    """

    resolve_supersedes: bool | None = None
    resolve_near_dups: bool | None = None
    propose_consolidations: bool | None = None
    auto_apply_consolidations: bool | None = None


class MemoryFeedbackIn(BaseModel):
    """Usefulness signal for one memory."""

    signal: TypeStr


class MemoryIngestMessageIn(BaseModel):
    """One role/content turn for ingest suggest."""

    role: TypeStr
    content: str = Field(min_length=0, max_length=limits.BODY_MAX)


class MemoryIngestSuggestIn(BaseModel):
    """Heuristic or LLM extraction of memory candidates from prose."""

    text: BodyStr
    messages: list[MemoryIngestMessageIn] | None = Field(default=None, max_length=200)
    use_llm: bool = False


class WebhookSettingIn(BaseModel):
    """Admin webhook delivery settings (secret never returned on GET)."""

    enabled: bool | None = None
    url: str | None = Field(default=None, max_length=limits.QUERY_MAX)
    secret: str | None = Field(default=None, max_length=limits.PASSWORD_INPUT_MAX)
    clear_secret: bool = False


class ResolveConflictIn(BaseModel):
    """Request body for accepting a supersedes conflict (forget ``b``)."""

    a: IdStr
    b: IdStr


class ResolveNearDuplicateIn(BaseModel):
    """Request body for keeping one near-duplicate and forgetting the other."""

    keep: IdStr
    drop: IdStr


class MailSettingIn(BaseModel):
    """Request body for updating outbound mail (SMTP) System Settings.

    Omitted fields are left unchanged. Pass ``password`` to replace the stored
    secret; pass ``clear_password=true`` to wipe it. An empty ``password`` with
    ``clear_password`` false is ignored (leave unchanged).
    """

    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=1024)
    clear_password: bool = False
    from_address: str | None = Field(default=None, max_length=320)
    use_tls: bool | None = None


class VerifyEmailIn(BaseModel):
    """Request body for confirming an email address with a one-time token."""

    token: str = Field(min_length=1, max_length=512)


class ResendVerificationIn(BaseModel):
    """Request body for re-sending a registration confirmation email."""

    email: EmailStr
