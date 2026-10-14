// Seiten-Tabs (Profil, Gruppe): <nav data-seiten-tabs> mit .seiten-tab[data-tab], Inhalte als
// .tab-inhalt[data-tab]. Der aktive Tab steht im Hash (#matches), damit Links, Neuladen und der
// Zurück-Knopf funktionieren. data-aliase='{"discord": "einstellungen:discord"}' leitet alte bzw.
// fachliche Anker auf einen Tab um und scrollt optional zu einem Element darin.
// [data-gehe-zu="tab"] springt von überall in einen Tab. Beim Zeigen feuert "seiten-tab" auf
// document (detail = Tab-Name), z.B. für Diagramme, die erst sichtbar gebaut werden können.
(function () {
  var leiste = document.querySelector("[data-seiten-tabs]");
  if (!leiste) return;
  var tabs = Array.prototype.slice.call(leiste.querySelectorAll(".seiten-tab"));
  var inhalte = Array.prototype.slice.call(document.querySelectorAll(".tab-inhalt"));
  var aliase = {};
  try { aliase = JSON.parse(leiste.dataset.aliase || "{}"); } catch (e) {}
  var standard = tabs[0].dataset.tab;
  // data-start: Tab, der ohne Hash zuerst offen ist (z.B. nach einer Meldung des Servers)
  var start = leiste.dataset.start || standard;

  function zeige(name, scrollen) {
    var zielId = null;
    if (aliase[name]) {
      var teile = aliase[name].split(":");
      name = teile[0];
      zielId = teile[1] || null;
    }
    if (!tabs.some(function (t) { return t.dataset.tab === name; })) name = start;

    tabs.forEach(function (t) {
      var aktiv = t.dataset.tab === name;
      t.classList.toggle("aktiv", aktiv);
      t.setAttribute("aria-selected", String(aktiv));
    });
    inhalte.forEach(function (el) { el.hidden = el.dataset.tab !== name; });
    document.dispatchEvent(new CustomEvent("seiten-tab", { detail: name }));

    var hash = name === start && start === standard ? "" : "#" + name;
    if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname + location.search);

    var ziel = zielId && document.getElementById(zielId);
    if (ziel) {
      ziel.scrollIntoView({ block: "start" });
    } else if (scrollen && leiste.getBoundingClientRect().top < 0) {
      // Nur hochscrollen, wenn die Tab-Leiste schon aus dem Bild gescrollt ist
      var nav = document.getElementById("topnav");
      window.scrollTo({
        top: window.scrollY + leiste.getBoundingClientRect().top - (nav ? nav.offsetHeight : 0),
        behavior: "smooth",
      });
    }
  }

  tabs.forEach(function (t) {
    t.addEventListener("click", function () { zeige(t.dataset.tab, true); });
  });
  document.querySelectorAll("[data-gehe-zu]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.preventDefault();
      zeige(b.dataset.geheZu, true);
    });
  });
  // Pfeiltasten wechseln zwischen den Tabs (Tab-Muster nach WAI-ARIA)
  leiste.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    var i = tabs.findIndex(function (t) { return t.classList.contains("aktiv"); });
    var neu = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    neu.focus();
    zeige(neu.dataset.tab, false);
  });
  window.addEventListener("hashchange", function () { zeige(location.hash.slice(1), true); });
  zeige(location.hash.slice(1), false);
})();
