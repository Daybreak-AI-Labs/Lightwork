import { useColorScheme } from "react-native";
/* One set of colors and text styles for every screen. */
export const theme = {
  background: "#ffffff",
  onAccent: "#ffffff",
  result: "#ddf4ff",
  text: "#1f2328",
  muted: "#57606a",
  border: "#d0d7de",
  accent: "#0969da",
  success: "#1a7f37",
  danger: "#cf222e",
  dangerBg: "#ffebe9",
  warnBg: "#fff8c5",
  warnText: "#4d2d00",
  surface: "#f6f8fa",
};

export type Palette = typeof theme;
const dark: Palette = {
  background: '#111820', onAccent: '#111820', result: '#17334a',
  text: '#f0f4f8', muted: '#b7c5d3', border: '#657789', accent: '#8cc8ff',
  success: '#79dda0', danger: '#ff9b9b', dangerBg: '#3a1922',
  warnBg: '#382d0f', warnText: '#ffe49c', surface: '#202b37',
};
export function useTheme(): Palette { return useColorScheme() === 'dark' ? dark : theme; }
