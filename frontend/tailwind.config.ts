import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "#FBF7ED",   // warm cream page background
        ink: "#111111",     // near-black borders/text
        accent: {
          yellow: "#FFD23F",
          pink: "#FF4D8D",
          cyan: "#3FC9E0",
          lime: "#A8E80C",
          orange: "#FF7A2F",
          purple: "#7C5CFF",
        },
      },
      boxShadow: {
        // Hard offset shadows — no blur. The brutalist signature.
        "brutal-sm": "2px 2px 0 0 #111111",
        brutal: "4px 4px 0 0 #111111",
        "brutal-lg": "6px 6px 0 0 #111111",
        "brutal-white": "4px 4px 0 0 #ffffff",
      },
    },
  },
  plugins: [],
};

export default config;
