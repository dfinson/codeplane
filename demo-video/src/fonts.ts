/**
 * Font loading for Remotion.
 * Loads Inter (UI text) and Roboto Mono (terminal/code).
 * Restricted to latin subset and used weights only.
 */
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadRobotoMono } from "@remotion/google-fonts/RobotoMono";

const inter = loadInter("normal", {
  weights: ["300", "400", "500", "600", "700"],
  subsets: ["latin"],
});
const robotoMono = loadRobotoMono("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

export const FONT_FAMILY = inter.fontFamily;
export const FONT_MONO = robotoMono.fontFamily;
