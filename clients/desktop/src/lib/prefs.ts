/** Preference display helpers (TUI settings.py parity). */

export type PreferenceRow = {
  key: string;
  value?: string | null;
};

export function formatPreferenceDisplay(key: string, value: string | null | undefined): string {
  let shown = "(unset)";
  if (value != null && String(value).trim()) {
    if (key === "favorite_genres") {
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed) && parsed.length) {
          shown = parsed.map(String).join(", ");
        } else {
          shown = "(unset)";
        }
      } catch {
        shown = String(value).trim();
      }
    } else {
      shown = String(value).trim();
    }
  }
  return `${key}  ·  ${shown}`;
}

export function editSeedValue(key: string, value: string | null | undefined): string {
  if (value == null || !String(value).trim()) return "";
  if (key === "favorite_genres") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(String).join(", ");
    } catch {
      /* fall through */
    }
  }
  return String(value).trim();
}
