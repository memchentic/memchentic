/**
 * Copy `text` to the system clipboard.
 *
 * Returns false when the Clipboard API is unavailable (`navigator.clipboard`
 * is undefined outside secure contexts) or the browser rejects the write.
 */
export async function copyText(
  text: string,
  clipboard: Pick<Clipboard, "writeText"> | undefined = navigator.clipboard,
): Promise<boolean> {
  if (!clipboard) return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
