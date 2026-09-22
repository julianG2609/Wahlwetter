/* Charts for Wahlwetter.
 *
 * Reads precomputed JSON written by `wahlwetter site-data`. Nothing is fetched
 * from a third party: d3 and Observable Plot are served from this site.
 *
 * Every chart shows an interval. A median on its own is the most misleading
 * thing a poll aggregator can display, so no chart here draws one alone.
 */
(function () {
  "use strict";

  // Party colours. Deliberately muted rather than the parties' own branding:
  // this is an estimate with uncertainty, not a campaign page.
  var PARTY_COLOURS = {
    "1": "#3b3b3b", // CDU/CSU
    "2": "#c8434b", // SPD
    "3": "#c9a227", // FDP
    "4": "#4c8c4a", // Grüne
    "5": "#8e4585", // Linke
    "7": "#2f6f8f", // AfD
    "8": "#7a6a53", // Freie Wähler
    "23": "#a0522d", // BSW
    "0": "#8c939c"  // Sonstige
  };
  var FALLBACK = "#8c939c";

  function colour(partyId) {
    return PARTY_COLOURS[partyId] || FALLBACK;
  }

  function fetchJSON(path) {
    return fetch(path, { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    });
  }

  function fail(node, error) {
    node.innerHTML =
      '<p class="note caution">Diagramm konnte nicht geladen werden: ' +
      String(error.message || error) +
      "</p>";
  }

  function formatDate(iso) {
    var p = iso.split("-");
    return p[2] + "." + p[1] + "." + p[0];
  }

  /* Current estimate per party, as a bar with its 80% and 95% intervals. */
  function renderLatest(node, data) {
    var rows = data.latest;
    var width = Math.min(node.clientWidth || 700, 700);
    var maxValue = Math.max.apply(
      null,
      rows.map(function (r) { return r.q97_5; })
    );

    var plot = Plot.plot({
      width: width,
      height: rows.length * 38 + 40,
      marginLeft: 92,
      marginRight: 56,
      x: { domain: [0, Math.ceil(maxValue / 5) * 5], label: "Anteil in Prozent", grid: true },
      y: { domain: rows.map(function (r) { return r.party_shortcut; }), label: null },
      marks: [
        // 95% interval, drawn faintest: it is the least informative of the two.
        Plot.ruleY(rows, {
          y: "party_shortcut", x1: "q2_5", x2: "q97_5",
          stroke: function (d) { return colour(d.party_id); },
          strokeWidth: 2, strokeOpacity: 0.3
        }),
        Plot.ruleY(rows, {
          y: "party_shortcut", x1: "q10", x2: "q90",
          stroke: function (d) { return colour(d.party_id); },
          strokeWidth: 8, strokeOpacity: 0.55
        }),
        Plot.dot(rows, {
          y: "party_shortcut", x: "median",
          fill: function (d) { return colour(d.party_id); },
          r: 5, stroke: "var(--bg)", strokeWidth: 1.5,
          title: function (d) {
            return d.party_shortcut + "\n" +
              d.median.toFixed(1) + " %\n" +
              "80 %: " + d.q10.toFixed(1) + "–" + d.q90.toFixed(1) + "\n" +
              "95 %: " + d.q2_5.toFixed(1) + "–" + d.q97_5.toFixed(1);
          }
        }),
        Plot.text(rows, {
          y: "party_shortcut", x: "q97_5", dx: 8, textAnchor: "start",
          text: function (d) { return d.median.toFixed(1); },
          fontVariant: "tabular-nums"
        }),
        // The 5% threshold, which decides whether a party enters parliament.
        Plot.ruleX([5], { stroke: "var(--fg-muted)", strokeDasharray: "3,3" })
      ]
    });
    node.replaceChildren(plot);
  }

  /* Latent trend over time, as a fan: 95% band, 80% band, median line. */
  function renderTrend(node, data, options) {
    options = options || {};
    var series = data.trend.series;
    var width = Math.min(node.clientWidth || 760, 760);

    var bands95 = [];
    var bands80 = [];
    var lines = [];
    series.forEach(function (s) {
      if (options.only && options.only.indexOf(s.party_id) === -1) return;
      s.dates.forEach(function (d, i) {
        var row = { date: new Date(d), party: s.party_shortcut, id: s.party_id };
        bands95.push(Object.assign({ lo: s.q2_5[i], hi: s.q97_5[i] }, row));
        bands80.push(Object.assign({ lo: s.q10[i], hi: s.q90[i] }, row));
        lines.push(Object.assign({ value: s.median[i] }, row));
      });
    });

    var plot = Plot.plot({
      width: width,
      height: options.height || 420,
      marginRight: 58,
      marginLeft: 44,
      x: { label: null, grid: true },
      y: { label: "Anteil in Prozent", grid: true, zero: true },
      marks: [
        Plot.areaY(bands95, {
          x: "date", y1: "lo", y2: "hi",
          fill: function (d) { return colour(d.id); }, fillOpacity: 0.12
        }),
        Plot.areaY(bands80, {
          x: "date", y1: "lo", y2: "hi",
          fill: function (d) { return colour(d.id); }, fillOpacity: 0.22
        }),
        Plot.line(lines, {
          x: "date", y: "value",
          stroke: function (d) { return colour(d.id); }, strokeWidth: 1.8
        }),
        Plot.text(
          lines.filter(function (d, i, a) {
            return i === a.length - 1 || a[i + 1].party !== d.party;
          }),
          {
            x: "date", y: "value", text: "party", dx: 6, textAnchor: "start",
            fill: function (d) { return colour(d.id); }, fontSize: 11
          }
        ),
        Plot.ruleY([5], { stroke: "var(--fg-muted)", strokeDasharray: "3,3" })
      ]
    });
    node.replaceChildren(plot);
  }

  /* House effects, in approximate percentage points at current levels. */
  function renderHouseEffects(node, data) {
    var rows = data.house_effects.slice().sort(function (a, b) {
      return Math.abs(b.approx_effect_pp) - Math.abs(a.approx_effect_pp);
    }).slice(0, 24);
    if (!rows.length) {
      node.innerHTML = '<p class="note">Keine belastbaren Institutseffekte verfügbar.</p>';
      return;
    }
    var width = Math.min(node.clientWidth || 700, 700);
    var labels = rows.map(function (r) { return r.institute_name + " · " + r.party_shortcut; });

    var plot = Plot.plot({
      width: width,
      height: rows.length * 22 + 50,
      marginLeft: 210,
      x: { label: "Abweichung vom Trend in Prozentpunkten", grid: true },
      y: { domain: labels, label: null },
      marks: [
        Plot.ruleX([0], { stroke: "var(--fg-muted)" }),
        Plot.barX(
          rows.map(function (r, i) { return Object.assign({ label: labels[i] }, r); }),
          {
            y: "label", x: "approx_effect_pp",
            fill: function (d) { return colour(d.party_id); },
            fillOpacity: function (d) { return d.excludes_zero_80 ? 0.85 : 0.35; },
            title: function (d) {
              return d.institute_name + " · " + d.party_shortcut + "\n" +
                d.approx_effect_pp.toFixed(2) + " Prozentpunkte\n" +
                "Polls: " + d.n_polls;
            }
          }
        )
      ]
    });
    node.replaceChildren(plot);
  }

  function mount(id, path, render, options) {
    var node = document.getElementById(id);
    if (!node) return;
    fetchJSON(path)
      .then(function (data) {
        render(node, data, options);
        var stamp = document.querySelector("[data-stamp]");
        if (stamp && data.generated_at) {
          stamp.textContent = "Stand: " + formatDate(data.generated_at.slice(0, 10));
        }
      })
      .catch(function (e) { fail(node, e); });
  }

  window.Wahlwetter = {
    colour: colour,
    fetchJSON: fetchJSON,
    formatDate: formatDate,
    mountLatest: function (id, path) { mount(id, path, renderLatest); },
    mountTrend: function (id, path, options) { mount(id, path, renderTrend, options); },
    mountHouseEffects: function (id, path) { mount(id, path, renderHouseEffects); }
  };
})();
