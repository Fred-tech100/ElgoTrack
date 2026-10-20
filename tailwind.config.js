/** Tailwind build for the ElgonNova design system (white / deep blue / orange accent). */
module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js", "./**/*.py"],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#eef4fb", 100: "#d9e6f5", 200: "#b3cdea", 300: "#7fa9d6", 400: "#4a80bd", 500: "#2a5f9e", 600: "#1c4a80", 700: "#143a66", 800: "#0f2d51", 900: "#0b2240", 950: "#071730" },
        accent: { 50: "#fff4eb", 100: "#ffe4cc", 400: "#f59340", 500: "#e8731a", 600: "#cc5f0f", 700: "#a84a0c" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"] },
      boxShadow: { card: "0 1px 2px rgba(15,23,42,.06)" },
    },
  },
  plugins: [],
};
