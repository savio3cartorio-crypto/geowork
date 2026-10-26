// Gera css/tailwind.css com as classes usadas nas páginas e no JS: scripts/build-css.sh
module.exports = {
  content: ['./index.html', './login.html', './js/**/*.js'],
  theme: { extend: {} },
  plugins: [],
};
