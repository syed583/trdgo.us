/**
 * Light / dark theme.
 *
 * The palette lives entirely in CSS custom properties, so switching is a
 * matter of setting data-theme on <html>; no component needs to know.
 *
 * Applied from index.html before React mounts, which avoids the dark-to-light
 * flash a light-theme user would otherwise see on every page load.
 */

export type Theme = 'dark' | 'light';

const KEY = 'usr.theme';

export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    // Private windows and blocked site data throw rather than return null.
    return null;
  }
}

/** Stored choice, else the OS preference, else dark. */
export function resolveTheme(): Theme {
  const stored = storedTheme();
  if (stored) return stored;
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches
      ? 'light'
      : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
}

export function saveTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // Preference simply does not persist; the toggle still works.
  }
}
