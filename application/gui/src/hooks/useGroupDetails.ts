import { api } from "../api";
import type { Space, SpaceDetail } from "../apiTypes";
import {
  useAsyncResource,
  type AsyncResource,
  type AsyncResourceOptions,
} from "./useAsyncResource";

/**
 * Every group the caller can see, with creator and members.
 *
 * `GET /api/spaces` returns only id, name and kind, so one detail request
 * per group follows. One failed request rejects the whole load.
 */
export async function fetchGroupDetails(): Promise<SpaceDetail[]> {
  const spaces = await api<Space[]>("/api/spaces");
  return Promise.all(
    spaces
      .filter((s) => s.kind === "group")
      .map((s) => api<SpaceDetail>(`/api/spaces/${s.id}`)),
  );
}

/** Group details as a resource. Loads only while `enabled` is true. */
export function useGroupDetails(
  enabled: boolean,
  options: AsyncResourceOptions = {},
): AsyncResource<SpaceDetail[]> {
  return useAsyncResource(enabled ? fetchGroupDetails : null, options);
}
