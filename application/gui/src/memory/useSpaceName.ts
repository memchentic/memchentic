import { spaceLabel, useAuth } from "../auth";

export function useSpaceName() {
  const { me } = useAuth();
  return (id?: string) => spaceLabel(me, id);
}
