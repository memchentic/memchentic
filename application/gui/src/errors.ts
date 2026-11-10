/** Text shown to the user for a caught value. */
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
