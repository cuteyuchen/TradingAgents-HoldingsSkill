/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"IBM Plex Sans"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        brand: {
          500: '#0C5CAB',
          600: '#0a4a8a',
        },
      },
      boxShadow: {
        panel: '0 18px 50px rgba(15, 23, 42, 0.14)',
      },
    },
  },
  plugins: [],
}
