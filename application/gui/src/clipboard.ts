/**
 * Copy `text` to the system clipboard.
 *
 * Prefers the Clipboard API. When it is unavailable (for example outside a
 * secure context) or the write is rejected, runs an optional synchronous
 * ``fallback`` (typically select + ``document.execCommand("copy")`` on an
 * existing input) so the caller's click still counts as a user gesture when
 * the Clipboard API is missing.
 *
 * Returns false when both paths fail.
 */
export async function copyText(
  text: string,
  clipboard: Pick<Clipboard, "writeText"> | undefined = navigator.clipboard,
  fallback?: () => boolean,
): Promise<boolean> {
  if (clipboard) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // rejected; try the caller fallback
    }
  }
  if (!fallback) return false;
  try {
    return fallback();
  } catch {
    return false;
  }
}
