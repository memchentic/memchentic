"""Upper bounds for caller-supplied values, shared by REST models and services.

Values that mirror a database column carry the column in a comment. Change a
column and its bound together.
"""

from __future__ import annotations

TITLE_MAX = 512  # memories.title String(512)
BODY_MAX = 100_000  # memories.body is Text; bound keeps embedding and tsvector cost finite
TAG_MAX_COUNT = 32
TAG_MAX_LEN = 64
PATH_HINT_MAX = 1024  # memories.path_hint String(1024)
REPO_HINT_MAX = 512  # memories.repo_hint String(512)
SPACE_NAME_MAX = 255  # spaces.name String(255)
API_KEY_NAME_MAX = 128  # api_keys.name String(128)
DISPLAY_NAME_MAX = 255  # users.display_name String(255)
EMAIL_MAX = 320  # users.email String(320)
IDP_GROUP_MAX = 255  # auth_groups.idp_group_id / idp_group_name String(255)
QUERY_MAX = 2_000
TTL_DAYS_MAX = 36_500  # 100 years; keeps datetime arithmetic in range
OFFSET_MAX = 100_000

# REST boundary only: textual forms that are parsed, never stored as text.
ID_TEXT_MAX = 64
DATETIME_TEXT_MAX = 64
TYPE_TEXT_MAX = 32
TYPES_QUERY_MAX = 256
PASSWORD_INPUT_MAX = 1024  # sign-in only; new passwords are bounded by SEC-054
