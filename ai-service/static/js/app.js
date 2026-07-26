// Avireon 全局前端脚本 — 语言加载 + Alpine 上下文 + 鉴权辅助
// 依赖：Alpine.js (CDN 已在页面引入)
window.Avireon = window.Avireon || {};

Avireon.initLang = function () {
  var lang = 'en';
  try {
    lang = localStorage.getItem('lang') ||
      document.cookie.replace(/(?:(?:^|.*;\s*)lang\s*=\s*([^;]*).*$)|^.*$/, '$1') ||
      'en';
  } catch (e) {}
  document.documentElement.lang = lang;
  return lang;
};

Avireon.loadTranslations = function () {
  return fetch('/api/v1/lang/translations')
    .then(function (r) { return r.json(); })
    .catch(function () { return {}; });
};

Avireon.setLang = function (l) {
  try { localStorage.setItem('lang', l); } catch (e) {}
  document.cookie = 'lang=' + l + ';path=/;max-age=' + 365 * 86400;
  document.documentElement.lang = l;
  fetch('/api/v1/lang/set', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locale: l })
  }).catch(function () {});
};

Avireon.isLoggedIn = function () {
  try { return !!localStorage.getItem('token'); } catch (e) { return false; }
};

Avireon.logout = function () {
  try { localStorage.removeItem('token'); localStorage.removeItem('user'); } catch (e) {}
  window.location.href = '/';
};

// 首页 / 控制台公用 Alpine 工厂
function app() {
  return {
    lang: Avireon.initLang(),
    translations: {},
    isLoggedIn: Avireon.isLoggedIn(),
    t: function (key) {
      var dict = this.translations && this.translations[this.lang];
      return (dict && dict[key]) ? dict[key] : key;
    },
    setLang: function (l) {
      this.lang = l;
      Avireon.setLang(l);
    },
    logout: function () { Avireon.logout(); },
    init: function () {
      var self = this;
      this.isLoggedIn = Avireon.isLoggedIn();
      Avireon.loadTranslations().then(function (d) { self.translations = d; });
    }
  };
}
