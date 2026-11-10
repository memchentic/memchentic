/** Shapes of REST responses used by more than one page. */

/** Item of `GET /api/spaces`. */
export type Space = { id: string; name: string; kind: string };

export type SpaceMember = {
  user_id: string;
  email: string | null;
  display_name: string | null;
  /** True for an invitation that is not accepted yet; sent to administrators only. */
  pending?: boolean;
};

/** `GET /api/spaces/{id}`. */
export type SpaceDetail = Space & {
  created_by: string | null;
  members: SpaceMember[];
};

/** Directory entry: `GET /api/users/lookup` and the base of `GET /api/users`. */
export type DirectoryUser = {
  id: string;
  email: string;
  display_name: string | null;
};

/** Item of `GET /api/users`. The optional fields are sent to administrators only. */
export type User = DirectoryUser & {
  providers?: string[];
  created_at?: string | null;
  deletable?: boolean;
  delete_blocked_reason?: string | null;
};

/** `GET /api/users/{id}`. */
export type UserDetail = User & { groups: Space[] };
