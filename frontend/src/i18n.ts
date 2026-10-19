// Dashboard text. Strings are written in English and passed through t(), so a
// translation can be added later without touching the components; until then
// t() returns the English text with its {name} placeholders filled in.

export const locale = () => "en-US";

/** Dashboard text; `{name}` placeholders are filled from `vars`. */
export function t(text: string, vars?: Record<string, string | number>): string {
  let out = text;
  for (const [key, value] of Object.entries(vars ?? {})) out = out.split(`{${key}}`).join(String(value));
  return out;
}

/** A status message from the Bridge, as shown on the page. */
export function tDetail(text: string | null | undefined): string {
  return text ?? "";
}
