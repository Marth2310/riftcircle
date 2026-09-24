// Vorschläge beim Tippen (wie bei OP.GG) für jedes <input data-spieler-suche="...">:
//   "absenden" - Auswahl setzt Name#Tag und schickt das Formular ab (Profilsuche)
//   "fuellen"  - Auswahl setzt nur Name#Tag ins Feld (z.B. Mitglied zu Gruppe hinzufügen)
// Die Liste hängt direkt am <body>, damit sie nicht von Containern mit overflow:hidden
// (z.B. dem Hero auf der Startseite) abgeschnitten wird.
(function () {
  var API = "/api/spieler-suche?q=";
  var VERZOEGERUNG_MS = 150;

  document.querySelectorAll("input[data-spieler-suche]").forEach(function (input) {
    var modus = input.getAttribute("data-spieler-suche");
    var form = input.form;
    var liste = document.createElement("div");
    liste.className = "suche-vorschlaege";
    liste.hidden = true;
    liste.setAttribute("role", "listbox");
    document.body.appendChild(liste);

    var treffer = [];
    var aktiv = -1;
    var timer = null;
    var anfrageNr = 0;

    function positionieren() {
      var r = input.getBoundingClientRect();
      liste.style.top = (r.bottom + window.scrollY + 6) + "px";
      liste.style.left = (r.left + window.scrollX) + "px";
      liste.style.width = Math.max(r.width, 260) + "px";
    }

    function schliessen() {
      liste.hidden = true;
      aktiv = -1;
    }

    function markieren(index) {
      aktiv = index;
      Array.prototype.forEach.call(liste.children, function (el, i) {
        el.classList.toggle("active", i === aktiv);
      });
    }

    function auswaehlen(index) {
      var t = treffer[index];
      if (!t) return;
      input.value = t.name + "#" + t.tag;
      schliessen();
      if (modus === "absenden" && form) {
        // requestSubmit löst (anders als submit) auch die submit-Listener aus, z.B. die Ladeanzeige
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      } else {
        input.focus();
      }
    }

    function anzeigen(daten) {
      treffer = daten;
      liste.textContent = "";
      if (!daten.length) { schliessen(); return; }
      daten.forEach(function (t, i) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "suche-item";
        item.setAttribute("role", "option");

        var avatar;
        if (t.icon) {
          avatar = document.createElement("img");
          avatar.src = t.icon;
          avatar.alt = "";
        } else {
          avatar = document.createElement("span");
          avatar.className = "suche-buchstabe";
          avatar.textContent = (t.name[0] || "?").toUpperCase();
        }
        var name = document.createElement("span");
        name.className = "suche-name";
        name.textContent = t.name;
        var tag = document.createElement("span");
        tag.className = "suche-tag";
        tag.textContent = "#" + t.tag;

        item.appendChild(avatar);
        item.appendChild(name);
        item.appendChild(tag);
        // mousedown statt click: sonst verliert das Feld vorher den Fokus und die Liste schließt
        item.addEventListener("mousedown", function (e) {
          e.preventDefault();
          auswaehlen(i);
        });
        liste.appendChild(item);
      });
      positionieren();
      liste.hidden = false;
      markieren(-1);
    }

    function suchen() {
      var wert = input.value.trim();
      if (wert.split("#")[0].trim().length < 2) { schliessen(); return; }
      var nr = ++anfrageNr;
      fetch(API + encodeURIComponent(wert))
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (daten) {
          // Nur die Antwort auf die zuletzt gestellte Anfrage zählt (Tippen überholt ältere)
          if (nr === anfrageNr && document.activeElement === input) anzeigen(daten);
        })
        .catch(function () {});
    }

    input.setAttribute("autocomplete", "off");
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(suchen, VERZOEGERUNG_MS);
    });
    input.addEventListener("focus", function () {
      if (treffer.length && input.value.trim().length >= 2) { positionieren(); liste.hidden = false; }
    });
    input.addEventListener("blur", schliessen);
    input.addEventListener("keydown", function (e) {
      if (liste.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        markieren((aktiv + 1) % treffer.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        markieren(aktiv <= 0 ? treffer.length - 1 : aktiv - 1);
      } else if (e.key === "Enter" && aktiv >= 0) {
        e.preventDefault();
        auswaehlen(aktiv);
      } else if (e.key === "Escape") {
        schliessen();
      }
    });
    window.addEventListener("resize", function () { if (!liste.hidden) positionieren(); });
  });
})();
