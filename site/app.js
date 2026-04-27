// ENOUGH x Judy Center coverage map
// Data: site/data/{grantee_tracts,schools,catchments}.geojson + grantees.json
// Roles: jc_in_tract, jc_near_miss, candidate

const MD_BOUNDS = [[37.85, -79.55], [39.75, -75.00]];

const map = L.map("map", {
  zoomControl: true,
  scrollWheelZoom: true,
}).fitBounds(MD_BOUNDS);

L.tileLayer(
  "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 18,
  }
).addTo(map);

const state = {
  selectedGrantee: null,
  showAllSchools: false,
  granteeIndex: {},   // grantee name -> payload row
  tracts: null,       // GeoJSON layer (all 106)
  schools: null,      // GeoJSON layer (filtered)
  catchments: null,   // GeoJSON layer (filtered)
  rawSchools: null,   // FeatureCollection (unfiltered)
  rawCatchments: null,
  rawTracts: null,
};

// ---- Symbols / styles ----

const tractStyleDim = {
  fillColor: "#e6e6e1",
  fillOpacity: 0.18,
  color: "#a8a8a4",
  weight: 0.6,
};
const tractStyleSelected = {
  fillColor: "#fde0c1",
  fillOpacity: 0.55,
  color: "#d4731c",
  weight: 1.6,
};

function schoolStyleForRole(role, props) {
  // role: "jc_in_tract" | "jc_near_miss" | "candidate" | null
  const cpg = props.cpg || 0;
  const radius = role === "jc_in_tract" || role === "jc_near_miss"
    ? 9
    : 5 + Math.round(cpg * 8); // candidate 5..13 px by CPG
  if (role === "jc_in_tract") {
    return { radius, fillColor: "#1c4ed4", color: "#0d2b85", weight: 1.5,
             fillOpacity: 0.95 };
  }
  if (role === "jc_near_miss") {
    return { radius, fillColor: "#7a9be7", color: "#1c4ed4", weight: 1.5,
             fillOpacity: 0.85, dashArray: "3,3" };
  }
  if (role === "candidate") {
    return { radius, fillColor: "#c0392b", color: "#7a221a", weight: 1.2,
             fillOpacity: 0.9 };
  }
  return { radius: 3, fillColor: "#aaa", color: "#777", weight: 0.6,
           fillOpacity: 0.4 };
}

function catchmentStyleForRole(role) {
  if (role === "jc_in_tract") {
    return { color: "#1c4ed4", weight: 1.2, fillColor: "#1c4ed4",
             fillOpacity: 0.07, dashArray: null };
  }
  if (role === "jc_near_miss") {
    return { color: "#1c4ed4", weight: 1.2, fillColor: "#7a9be7",
             fillOpacity: 0.06, dashArray: "4,4" };
  }
  if (role === "candidate") {
    return { color: "#c0392b", weight: 1.0, fillColor: "#c0392b",
             fillOpacity: 0.05, dashArray: null };
  }
  return { color: "#aaa", weight: 0.4, fillOpacity: 0 };
}

// ---- Sidebar rendering ----

function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  // tract values come in as 0..100, school CPG comes in as 0..1
  const f = v <= 1 ? v : v / 100;
  return Math.round(f * 100) + "%";
}
function fmtCpg(v) { return v === null || v === undefined ? "—" : Math.round(v * 100) + "%"; }
function escape(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function renderSidebar(grantee) {
  const pane = document.getElementById("grantee-pane");
  if (!grantee) {
    pane.innerHTML =
      '<div class="meta" style="margin-top:18px;color:var(--muted);">' +
      "Pick a grantee from the menu above.</div>";
    return;
  }
  const p = state.granteeIndex[grantee];
  if (!p) {
    pane.innerHTML = "<div class=meta>Grantee not found.</div>";
    return;
  }
  const pill = p.tract_level_coverage
    ? '<span class="pill has">✓ Already covered</span>'
    : '<span class="pill needs">Needs Judy Center</span>';
  const namedHint = p.named_hint
    ? '<span class="pill named">★ named-overlap exemplar</span>'
    : "";

  let h = "";
  h += pill + namedHint;
  h += '<div class="meta">';
  h += "<b>" + escape(p.county) + "</b> · " + p.n_tracts +
       " served tract" + (p.n_tracts === 1 ? "" : "s");
  h += "</div>";

  // JCs in tracts
  h += "<h2>Judy Centers in served tracts</h2>";
  if (p.jcs_in_tracts.length === 0) {
    h += '<div class="meta">None — no Judy Center is currently sited in any of this grantee\'s tracts.</div>';
  } else {
    h += '<ul class="compact">';
    p.jcs_in_tracts.forEach((s) => { h += "<li>" + escape(s) + "</li>"; });
    h += "</ul>";
  }

  // Near-miss JCs
  if (p.jcs_near_miss.length > 0) {
    h += "<h2>Judy Centers nearby (catchment overlaps tracts)</h2>";
    h += '<div class="meta" style="margin-bottom:6px;">School is outside the grantee\'s tracts but its attendance zone overlaps. Possible coordination already.</div>';
    h += '<ul class="compact">';
    p.jcs_near_miss.forEach((nm) => {
      h += "<li>" + escape(nm.school_name.trim()) +
           ' <span style="color:var(--muted);font-size:12px;">(' +
           fmtPct(nm.pct_of_tract) + " of tract)</span></li>";
    });
    h += "</ul>";
  }

  // Candidates
  h += "<h2>Candidate elementary schools</h2>";
  if (p.candidates.length === 0) {
    h += '<div class="meta">No traditional elementary or combined school in this grantee\'s tracts. ' +
         'Investigate alternative school types (charter, middle).</div>';
  } else {
    h += '<table class="cands"><thead><tr>';
    h += "<th>School</th><th>Type</th><th class='cpg'>CPG</th><th></th>";
    h += "</tr></thead><tbody>";
    p.candidates.forEach((c) => {
      h += "<tr" + (c.is_jc ? " class='is-jc'" : "") + ">";
      h += "<td>" + escape(c.school_name.trim()) + "</td>";
      h += "<td style='color:var(--muted);'>" + escape(c.school_type) + "</td>";
      h += "<td class='cpg'>" + fmtCpg(c.cpg) + "</td>";
      h += "<td>" + (c.is_jc ? "<span class='jc-tag'>JC</span>" : "") + "</td>";
      h += "</tr>";
    });
    h += "</tbody></table>";
  }

  // Recommendation
  h += '<div class="recco"><b>Recommendation:</b> ' +
       escape(p.recommendation_short) + "</div>";

  pane.innerHTML = h;
}

// ---- Layer filter / draw ----

function rolesFor(props, grantee) {
  if (!props || !props.roles) return null;
  return props.roles[grantee] || null;
}

function relevantSchools(grantee) {
  if (!grantee) return [];
  return state.rawSchools.features.filter((f) => rolesFor(f.properties, grantee));
}

function relevantCatchments(grantee, schoolNcesids) {
  if (!grantee) return [];
  return state.rawCatchments.features.filter((f) =>
    schoolNcesids.has(f.properties.ncessch_id)
  );
}

function drawTracts(grantee) {
  if (state.tracts) state.tracts.remove();
  state.tracts = L.geoJSON(state.rawTracts, {
    style: (f) => {
      if (grantee && f.properties.grantees.includes(grantee)) {
        return tractStyleSelected;
      }
      return tractStyleDim;
    },
    onEachFeature: (f, layer) => {
      const grantees = f.properties.grantees || [];
      const tip = "<b>Tract " + f.properties.geoid20 + "</b><br/>" +
                  grantees.join(", ");
      layer.bindTooltip(tip, { sticky: true });
    },
  }).addTo(map);
}

function drawSchools(grantee) {
  if (state.schools) state.schools.remove();
  if (state.catchments) state.catchments.remove();

  let features;
  if (state.showAllSchools) {
    features = state.rawSchools.features;
  } else {
    features = relevantSchools(grantee);
  }

  const ncesidsToShow = new Set(features.map((f) => f.properties.ncessch_id));
  const cats = relevantCatchments(grantee, ncesidsToShow);

  // Catchments first so points render on top
  state.catchments = L.geoJSON(
    { type: "FeatureCollection", features: cats },
    {
      style: (f) => {
        const role = rolesFor(
          state.rawSchools.features.find(
            (s) => s.properties.ncessch_id === f.properties.ncessch_id
          )?.properties,
          grantee
        );
        return catchmentStyleForRole(role);
      },
      interactive: false,
    }
  ).addTo(map);

  state.schools = L.geoJSON(
    { type: "FeatureCollection", features: features },
    {
      pointToLayer: (f, latlng) => {
        const role = rolesFor(f.properties, grantee);
        return L.circleMarker(latlng, schoolStyleForRole(role, f.properties));
      },
      onEachFeature: (f, layer) => {
        const p = f.properties;
        const role = rolesFor(p, grantee);
        const roleLabel = role === "jc_in_tract"
          ? "Judy Center (in served tract)"
          : role === "jc_near_miss"
          ? "Judy Center (catchment overlap)"
          : role === "candidate"
          ? "Candidate elementary"
          : p.is_judy_center ? "Judy Center" : "Elementary";
        const tip = "<b>" + escape((p.school_name || "").trim()) + "</b><br/>" +
                    "CPG " + fmtCpg(p.cpg) +
                    (p.countywide_catchment ? " · 🚩 county-wide catchment" : "") +
                    "<br/><span style='color:#666;font-size:11px;'>" +
                    escape(roleLabel) + "</span>";
        layer.bindTooltip(tip, { className: "school-tip", sticky: true });
        const popup =
          "<b>" + escape((p.school_name || "").trim()) + "</b><br/>" +
          escape(p.school_type) + " · tract " + p.geoid20 + "<br/>" +
          escape(p.address || "") + "<br/>" +
          "CPG: <b>" + fmtCpg(p.cpg) + "</b> · " +
          "child poverty: " + fmtPct(p.child_pov_pct) + "<br/>" +
          "<span style='color:#666;font-size:12px;'>" + escape(roleLabel) + "</span>";
        layer.bindPopup(popup);
      },
    }
  ).addTo(map);
}

function fitToGrantee(grantee) {
  if (!grantee) {
    map.fitBounds(MD_BOUNDS);
    return;
  }
  const sel = state.rawTracts.features.filter(
    (f) => f.properties.grantees.includes(grantee)
  );
  if (sel.length === 0) return;
  const layer = L.geoJSON({ type: "FeatureCollection", features: sel });
  map.fitBounds(layer.getBounds().pad(0.15));
}

function setGrantee(g) {
  state.selectedGrantee = g;
  drawTracts(g);
  drawSchools(g);
  renderSidebar(g);
  fitToGrantee(g);
}

// ---- Dropdown ----

function buildDropdown(grantees) {
  const sel = document.getElementById("grantee-select");
  sel.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "— select a grantee —";
  sel.appendChild(placeholder);

  // Group: needs JC first, then has JC
  const needs = grantees.filter((g) => !g.tract_level_coverage);
  const has = grantees.filter((g) => g.tract_level_coverage);

  const og1 = document.createElement("optgroup");
  og1.label = "Needs Judy Center (sorted by best candidate CPG)";
  needs.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g.grantee;
    opt.textContent = g.grantee;
    og1.appendChild(opt);
  });
  sel.appendChild(og1);

  const og2 = document.createElement("optgroup");
  og2.label = "Already covered";
  has.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g.grantee;
    opt.textContent = g.grantee + (g.named_hint ? " ★" : "");
    og2.appendChild(opt);
  });
  sel.appendChild(og2);

  sel.onchange = (e) => setGrantee(e.target.value || null);
}

// ---- Legend ----

const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const d = L.DomUtil.create("div", "legend");
  d.innerHTML =
    "<b>Legend</b><br/>" +
    "<span class='swatch tract'></span>Selected grantee tracts<br/>" +
    "<span class='swatch tract-other'></span>Other grantee tracts<br/>" +
    "<span class='swatch jc'></span>Judy Center (in tract)<br/>" +
    "<span class='swatch jc-near'></span>Judy Center (catchment overlap)<br/>" +
    "<span class='swatch cand'></span>Candidate elementary<br/>" +
    "<span class='swatch cand-line'></span>Catchment polygons";
  return d;
};
legend.addTo(map);

// ---- Toggle ----

document.getElementById("toggle-all-schools").addEventListener("change", (e) => {
  state.showAllSchools = e.target.checked;
  drawSchools(state.selectedGrantee);
});

// ---- Boot ----

Promise.all([
  fetch("data/grantee_tracts.geojson").then((r) => r.json()),
  fetch("data/schools.geojson").then((r) => r.json()),
  fetch("data/catchments.geojson").then((r) => r.json()),
  fetch("data/grantees.json").then((r) => r.json()),
]).then(([tracts, schools, catchments, grantees]) => {
  state.rawTracts = tracts;
  state.rawSchools = schools;
  state.rawCatchments = catchments;
  grantees.forEach((g) => { state.granteeIndex[g.grantee] = g; });
  buildDropdown(grantees);
  drawTracts(null);
}).catch((err) => {
  document.getElementById("grantee-pane").innerHTML =
    '<div class="meta" style="color:#a32;">Failed to load data: ' +
    escape(err.message) + "</div>";
  console.error(err);
});
