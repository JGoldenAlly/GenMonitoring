import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#0d1117',
          raised: '#151b23',
          card: '#1a2129',
          border: '#2a333d',
        },
      },
    },
  },
  plugins: [],
};
export default config;
