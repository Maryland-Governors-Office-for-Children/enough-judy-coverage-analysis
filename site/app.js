// ENOUGH x Judy Center coverage map
// Tabs: overview / map / grantees / methodology
// Categories: in_tract (green/blue), catchment_overlap (blue), needs_jc (red)
// Map symbols: purple = JC (default), blue = JC in selected tract,
//   green = JC catchment overlaps selected tract, red = candidate elementary,
//   red dashed-circle = closest-elementary fallback.

const MD_BOUNDS = [[37.85, -79.55], [39.75, -75.00]];

const CATEGORIES = ["in_tract", "catchment_overlap", "needs_jc"];
const CATEGORY_LABELS = {
  in_tract: "Already covered",
  catchment_overlap: "Catchment overlap",
  needs_jc: "Needs Judy Center",
};

const state = {
  activeTab: "overview",
  selectedGrantee: null,
  categoryFilter: "all",
  payload: null,           // { dashboard, grantees }
  granteeIndex: {},        // grantee name -> grantee object
  rawTracts: null,
  rawSchools: null,
  rawCatchments: null,
  map: null,
  layers: { tracts: null, schools: null, catchments: null, closest: null },
};

// ============= Boot =============

Promise.all([
  fetch("data/grantee_tracts.geojson").then((r) => r.json()),
  fetch("data/schools.geojson").then((r) => r.json()),
  fetch("data/catchments.geojson").then((r) => r.json()),
  fetch("data/grantees.json").then((r) => r.json()),
]).then(([tracts, schools, catchments, grantees]) => {
  state.rawTracts = tracts;
  state.rawSchools = schools;
  state.rawCatchments = catchments;
  state.payload = grantees;
  grantees.grantees.forEach((g) => { state.granteeIndex[g.grantee] = g; });
  initTabs();
  renderDashboard();
  renderCategorySummary();
  renderGranteeList();
  renderGranteeBlocks();
  renderMap();
  setupFilterPills();
  setupCardClicks();
}).catch((err) => {
  document.body.insertAdjacentHTML("afterbegin",
    `<div style="padding:20px;color:#a32;font-family:monospace;">Failed to load data: ${escapeHtml(err.message)}</div>`);
  console.error(err);
});

// ============= Tabs =============

function initTabs() {
  document.querySelectorAll(".tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

function activateTab(name) {
  state.activeTab = name;
  document.querySelectorAll(".tabs .tab").forEach((btn) => {
    btn.setAttribute("aria-selected", btn.dataset.tab === name ? "true" : "false");
  });
  document.querySelectorAll(".tabpane").forEach((p) => {
    p.classList.toggle("active", p.dataset.tab === name);
  });
  if (name === "map" && state.map) {
    setTimeout(() => state.map.invalidateSize(), 50);
  }
}

// ============= Dashboard =============

function renderDashboard() {
  const d = state.payload.dashboard;
  const grid = document.getElementById("dashboard-grid");
  const cards = [
    { num: d.n_grantees, label: "ENOUGH grantees", cls: "is-static" },
    { num: d.n_in_tract, label: "Already covered (JC in tract)", cls: "cat-in_tract", cat: "in_tract" },
    { num: d.n_catchment_overlap, label: "Catchment overlap (coordination opportunity)", cls: "cat-catchment_overlap", cat: "catchment_overlap" },
    { num: d.n_needs_jc, label: "Needs Judy Center", cls: "cat-needs_jc", cat: "needs_jc" },
    { num: d.n_judy_centers_total, label: "Judy Centers statewide", cls: "is-static" },
    { num: d.n_judy_centers_in_grantee_tracts, label: "JCs in ENOUGH grantee tracts", cls: "is-static" },
    { num: d.n_judy_centers_catchment_overlap_only, label: "JCs with catchment-only overlap", cls: "is-static" },
    { num: d.n_prospective_elementaries, label: "Prospective elementary schools", cls: "is-static" },
  ];
  grid.innerHTML = cards.map((c) =>
    `<div class="dash-card ${c.cls}" ${c.cat ? `data-cat="${c.cat}"` : ""}>
       <div class="num">${c.num}</div>
       <div class="label">${c.label}</div>
     </div>`
  ).join("");

  // Category summary counts
  CATEGORIES.forEach((cat) => {
    const el = document.getElementById(`cat-count-${cat}`);
    if (el) el.textContent = state.payload.dashboard[`n_${cat}`];
  });
}

function renderCategorySummary() {
  // Filter pill counts on Map and Grantees tabs
  CATEGORIES.forEach((cat) => {
    const n = state.payload.dashboard[`n_${cat}`];
    const a = document.getElementById(`fp-${cat}`);
    const b = document.getElementById(`fp2-${cat}`);
    if (a) a.textContent = n;
    if (b) b.textContent = n;
  });
}

function setupCardClicks() {
  // Dashboard category cards -> Grantees tab with filter
  document.querySelectorAll(".dash-card[data-cat]").forEach((card) => {
    card.addEventListener("click", () => {
      setCategoryFilter(card.dataset.cat);
      activateTab("grantees");
    });
  });
  // "Browse these grantees" links on Overview category blocks
  document.querySelectorAll(".cat-link[data-cat]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setCategoryFilter(btn.dataset.cat);
      activateTab("grantees");
    });
  });
}

// ============= Filter pills =============

function setupFilterPills() {
  document.querySelectorAll(".filter-pill[data-cat]").forEach((btn) => {
    btn.addEventListener("click", () => setCategoryFilter(btn.dataset.cat));
  });
}

function setCategoryFilter(cat) {
  state.categoryFilter = cat;
  document.querySelectorAll(".filter-pill").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.cat === cat);
  });
  renderGranteeList();
  renderGranteeBlocks();
  if (state.layers.tracts) drawTracts(state.selectedGrantee);
}

function passesFilter(g) {
  return state.categoryFilter === "all" || g.category === state.categoryFilter;
}

// ============= Grantee list (Map tab sidebar) =============

function renderGranteeList() {
  const list = document.getElementById("grantee-list");
  const visible = state.payload.grantees.filter(passesFilter);
  const byCat = {};
  CATEGORIES.forEach((c) => { byCat[c] = []; });
  visible.forEach((g) => byCat[g.category].push(g));

  let html = "";
  CATEGORIES.forEach((cat) => {
    const grantees = byCat[cat];
    if (grantees.length === 0) return;
    html += `<div class="cat-section" data-cat="${cat}">`;
    html += `<div class="cat-header ${cat}"><span>${CATEGORY_LABELS[cat]}</span><span class="count">${grantees.length}</span></div>`;
    html += `<div class="cat-list">`;
    grantees.forEach((g) => {
      const sub = subForGrantee(g);
      html += `<button class="grantee-button${state.selectedGrantee === g.grantee ? " selected" : ""}" data-grantee="${escapeAttr(g.grantee)}">`;
      html += `<span class="gname">${escapeHtml(g.grantee)}</span>`;
      if (sub) html += `<span class="gsub">${escapeHtml(sub)}</span>`;
      html += `</button>`;
    });
    html += `</div></div>`;
  });
  list.innerHTML = html || `<div style="padding:16px;color:var(--muted);font-size:13px;">No grantees in this filter.</div>`;
  list.querySelectorAll(".grantee-button").forEach((btn) => {
    btn.addEventListener("click", () => selectGrantee(btn.dataset.grantee));
  });
}

function subForGrantee(g) {
  if (g.category === "in_tract" && g.jcs_in_tracts.length) {
    return g.jcs_in_tracts[0] + (g.jcs_in_tracts.length > 1 ? ` (+${g.jcs_in_tracts.length - 1})` : "");
  }
  if (g.category === "catchment_overlap" && g.jcs_near_miss.length) {
    return g.jcs_near_miss[0].school_name.trim();
  }
  return g.county;
}

// ============= Grantee detail (Map sidebar bottom) =============

function renderGranteeDetail(g) {
  const pane = document.getElementById("grantee-detail");
  if (!g) {
    pane.innerHTML = `<div class="detail-empty">Pick a grantee from the list above.</div>`;
    return;
  }
  let h = "";
  h += `<div class="gtitle">${escapeHtml(g.grantee)}${g.named_hint ? " ★" : ""}</div>`;
  h += `<div class="gmeta">${escapeHtml(g.county)} · ${g.n_tracts} served tract${g.n_tracts === 1 ? "" : "s"} · `;
  h += `<span class="gpill ${g.category}" style="margin-left:4px;">${CATEGORY_LABELS[g.category]}</span></div>`;

  if (g.jcs_in_tracts.length) {
    h += `<h4>Judy Centers in served tracts</h4>`;
    h += `<ul>${g.jcs_in_tracts.map((s) => `<li>${escapeHtml(s.trim())}</li>`).join("")}</ul>`;
  }
  if (g.jcs_near_miss.length) {
    h += `<h4>Judy Centers nearby (catchment overlap)</h4>`;
    h += `<ul>${g.jcs_near_miss.map((nm) =>
      `<li>${escapeHtml(nm.school_name.trim())} <span style="color:var(--muted);font-size:12px;">(${fmtPct(nm.pct_of_tract)} of tract)</span></li>`
    ).join("")}</ul>`;
  }
  if (g.candidates.length) {
    const nonJc = g.candidates.filter((c) => !c.is_jc);
    if (nonJc.length) {
      h += `<h4>Candidate elementary schools</h4>`;
      h += `<table class="cands"><thead><tr><th>School</th><th class="cpg">CPG</th></tr></thead><tbody>`;
      nonJc.slice(0, 8).forEach((c) => {
        h += `<tr><td>${escapeHtml(c.school_name.trim())}</td><td class="cpg">${fmtCpg(c.cpg)}</td></tr>`;
      });
      h += `</tbody></table>`;
    }
  }
  if (g.closest_elementary) {
    const ce = g.closest_elementary;
    const km = (ce.distance_m / 1000).toFixed(1);
    h += `<div class="closest-callout"><b>Closest non-JC elementary:</b> `;
    h += `${escapeHtml(ce.school_name)} (CPG ${fmtCpg(ce.cpg)}, ~${km} km from tract). `;
    h += `Used as a fallback because no traditional elementary or combined school exists in the served tract.</div>`;
  }
  h += `<div class="recco"><b>Recommendation:</b> ${escapeHtml(g.recommendation_short)}</div>`;
  pane.innerHTML = h;
}

// ============= Grantees tab =============

function renderGranteeBlocks() {
  const wrap = document.getElementById("grantee-blocks");
  const visible = state.payload.grantees.filter(passesFilter);
  const sorted = [...visible].sort((a, b) =>
    CATEGORIES.indexOf(a.category) - CATEGORIES.indexOf(b.category) ||
    a.grantee.localeCompare(b.grantee)
  );
  let html = "";
  let lastCat = null;
  sorted.forEach((g) => {
    if (g.category !== lastCat) {
      html += `<h2 class="section-title" style="margin-top:24px;">${CATEGORY_LABELS[g.category]} (${state.payload.dashboard["n_" + g.category]})</h2>`;
      lastCat = g.category;
    }
    html += renderGranteeBlock(g);
  });
  wrap.innerHTML = html || `<p style="color:var(--muted);">No grantees in this filter.</p>`;
  wrap.querySelectorAll(".view-on-map").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectGrantee(btn.dataset.grantee);
      activateTab("map");
    });
  });
}

function renderGranteeBlock(g) {
  let h = `<div class="gblock ${g.category}">`;
  h += `<div class="ghead">`;
  h += `<div class="gtitle-row">`;
  h += `<h3>${escapeHtml(g.grantee)}${g.named_hint ? " ★" : ""}</h3>`;
  h += `<div class="gmeta">${escapeHtml(g.county)} · ${g.n_tracts} served tract${g.n_tracts === 1 ? "" : "s"}</div>`;
  h += `</div>`;
  h += `<div class="right">`;
  h += `<div class="gpill">${CATEGORY_LABELS[g.category]}</div><br/>`;
  h += `<button class="view-on-map" data-grantee="${escapeAttr(g.grantee)}" style="margin-top:6px;">View on map →</button>`;
  h += `</div></div>`;

  h += `<div class="gbody">`;
  // Left col: JCs / catchment overlap
  h += `<div>`;
  if (g.jcs_in_tracts.length) {
    h += `<h4>Judy Centers in served tracts</h4>`;
    h += `<ul class="compact">${g.jcs_in_tracts.map((s) => `<li>${escapeHtml(s.trim())}</li>`).join("")}</ul>`;
  }
  if (g.jcs_near_miss.length) {
    h += `<h4>Judy Centers nearby (catchment overlap)</h4>`;
    h += `<ul class="compact">${g.jcs_near_miss.map((nm) =>
      `<li>${escapeHtml(nm.school_name.trim())} <span style="color:var(--muted);font-size:12px;">(${fmtPct(nm.pct_of_tract)} of tract)</span></li>`
    ).join("")}</ul>`;
  }
  if (g.jcs_in_tracts.length === 0 && g.jcs_near_miss.length === 0) {
    h += `<h4>Judy Centers</h4>`;
    h += `<p style="color:var(--muted);font-size:13px;margin:0;">None in served tracts; no catchment overlaps.</p>`;
  }
  h += `</div>`;
  // Right col: candidate elementary table
  h += `<div>`;
  const nonJc = g.candidates.filter((c) => !c.is_jc);
  if (nonJc.length) {
    h += `<h4>Candidate elementary schools (CPG-sorted)</h4>`;
    h += `<table class="cands"><thead><tr><th>School</th><th>Type</th><th class="cpg">CPG</th></tr></thead><tbody>`;
    nonJc.forEach((c) => {
      h += `<tr><td>${escapeHtml(c.school_name.trim())}</td><td>${escapeHtml(c.school_type)}</td><td class="cpg">${fmtCpg(c.cpg)}</td></tr>`;
    });
    h += `</tbody></table>`;
  } else if (g.candidates.length) {
    h += `<h4>Candidate elementary schools</h4>`;
    h += `<p style="color:var(--muted);font-size:13px;margin:0;">All elementaries in served tracts are already Judy Centers.</p>`;
  } else {
    h += `<h4>Candidate elementary schools</h4>`;
    h += `<p style="color:var(--muted);font-size:13px;margin:0;">No traditional elementary or combined school in the served tract.</p>`;
  }
  h += `</div>`;
  if (g.closest_elementary) {
    const ce = g.closest_elementary;
    const km = (ce.distance_m / 1000).toFixed(1);
    h += `<div class="closest-callout">`;
    h += `<b>Closest non-JC elementary (fallback):</b> ${escapeHtml(ce.school_name)} (CPG ${fmtCpg(ce.cpg)}, ~${km} km from tract). `;
    h += `Used because no traditional elementary or combined school is inside the served tract.`;
    h += `</div>`;
  }
  h += `</div></div>`;
  return h;
}

// ============= Map =============

function renderMap() {
  state.map = L.map("map", {
    zoomControl: true,
    scrollWheelZoom: true,
  }).fitBounds(MD_BOUNDS);
  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 18,
    }
  ).addTo(state.map);

  drawTracts(null);
  drawSchools(null);
  drawLegend();
}

function drawTracts(grantee) {
  if (state.layers.tracts) state.layers.tracts.remove();
  state.layers.tracts = L.geoJSON(state.rawTracts, {
    style: (f) => {
      const tractGrantees = f.properties.grantees || [];
      if (grantee && tractGrantees.includes(grantee)) {
        return { fillColor: "#fde0c1", fillOpacity: 0.55, color: "#d4731c", weight: 1.6 };
      }
      // No grantee selected: shade by category if filter is active
      if (!grantee && state.categoryFilter !== "all") {
        const inFilter = tractGrantees.some((gn) => {
          const g = state.granteeIndex[gn];
          return g && g.category === state.categoryFilter;
        });
        if (inFilter) {
          const c = state.categoryFilter;
          if (c === "in_tract") return { fillColor: "#d8efdd", fillOpacity: 0.5, color: "#2f8f4d", weight: 1.0 };
          if (c === "catchment_overlap") return { fillColor: "#d6def4", fillOpacity: 0.5, color: "#1c4ed4", weight: 1.0 };
          if (c === "needs_jc") return { fillColor: "#f5cdc8", fillOpacity: 0.5, color: "#c0392b", weight: 1.0 };
        }
      }
      // Default: dim grey
      return { fillColor: "#e6e6e1", fillOpacity: 0.18, color: "#a8a8a4", weight: 0.6 };
    },
    onEachFeature: (f, layer) => {
      const grantees = f.properties.grantees || [];
      const tip = `<b>Tract ${f.properties.geoid20}</b><br/>${grantees.join(", ")}`;
      layer.bindTooltip(tip, { sticky: true });
    },
  }).addTo(state.map);
}

function drawSchools(grantee) {
  if (state.layers.schools) state.layers.schools.remove();
  if (state.layers.catchments) state.layers.catchments.remove();
  if (state.layers.closest) state.layers.closest.remove();

  const features = [];
  const catchmentNcesids = new Set();

  // Always show Judy Centers (state-wide context).
  // Their styling depends on whether the selected grantee gives them a role.
  state.rawSchools.features.forEach((f) => {
    const p = f.properties;
    if (p.is_judy_center) {
      const role = grantee ? (p.roles && p.roles[grantee]) : null;
      f._role = role || null;  // jc_in_tract | jc_near_miss | null
      features.push(f);
      if (role === "jc_in_tract" || role === "jc_near_miss") {
        catchmentNcesids.add(p.ncessch_id);
      }
    } else {
      // Candidate elementary -- only render when its grantee is selected
      if (grantee && p.roles && p.roles[grantee] === "candidate") {
        f._role = "candidate";
        features.push(f);
      }
    }
  });

  // Catchment polygons for blue/green schools
  const cats = state.rawCatchments.features.filter((f) =>
    catchmentNcesids.has(f.properties.ncessch_id)
  );
  state.layers.catchments = L.geoJSON(
    { type: "FeatureCollection", features: cats },
    {
      style: (f) => {
        const school = state.rawSchools.features.find(
          (s) => s.properties.ncessch_id === f.properties.ncessch_id
        );
        const role = school && grantee ? school.properties.roles[grantee] : null;
        if (role === "jc_in_tract") {
          return { color: "#1c4ed4", weight: 1.4, fillColor: "#1c4ed4", fillOpacity: 0.08 };
        }
        if (role === "jc_near_miss") {
          return { color: "#2f8f4d", weight: 1.4, fillColor: "#2f8f4d",
                   fillOpacity: 0.07, dashArray: "4,4" };
        }
        return { color: "#aaa", weight: 0.4, fillOpacity: 0 };
      },
      interactive: false,
    }
  ).addTo(state.map);

  state.layers.schools = L.geoJSON(
    { type: "FeatureCollection", features },
    {
      pointToLayer: (f, latlng) => {
        return L.circleMarker(latlng, schoolStyleFor(f, grantee));
      },
      onEachFeature: (f, layer) => bindSchoolPopup(f, layer, grantee),
    }
  ).addTo(state.map);

  // Closest-elementary fallback (only when a Cat 3 zero-candidate grantee is selected)
  if (grantee) {
    const g = state.granteeIndex[grantee];
    if (g && g.closest_elementary) {
      const ce = g.closest_elementary;
      state.layers.closest = L.layerGroup();
      const ring = L.circleMarker([ce.lat, ce.lon], {
        radius: 18, fillColor: "transparent", color: "#c0392b",
        weight: 1.4, dashArray: "5,4", fillOpacity: 0,
      });
      const dot = L.circleMarker([ce.lat, ce.lon], {
        radius: 7, fillColor: "#c0392b", color: "#7a221a",
        weight: 1.2, fillOpacity: 0.9,
      });
      const tip = `<b>${escapeHtml(ce.school_name)}</b><br/>` +
                  `Closest non-JC elementary (fallback)<br/>` +
                  `CPG ${fmtCpg(ce.cpg)} · ~${(ce.distance_m / 1000).toFixed(1)} km`;
      dot.bindTooltip(tip, { className: "school-tip", sticky: true });
      ring.bindTooltip(tip, { className: "school-tip", sticky: true });
      ring.addTo(state.layers.closest);
      dot.addTo(state.layers.closest);
      state.layers.closest.addTo(state.map);
    }
  }
}

function schoolStyleFor(f, grantee) {
  const p = f.properties;
  const role = f._role;
  if (role === "jc_in_tract") {
    return { radius: 9, fillColor: "#1c4ed4", color: "#0d2b85",
             weight: 1.5, fillOpacity: 0.95 };
  }
  if (role === "jc_near_miss") {
    return { radius: 9, fillColor: "#2f8f4d", color: "#1f6536",
             weight: 1.5, fillOpacity: 0.9 };
  }
  if (role === "candidate") {
    const cpg = p.cpg || 0;
    return { radius: 5 + Math.round(cpg * 8),
             fillColor: "#c0392b", color: "#7a221a",
             weight: 1.2, fillOpacity: 0.9 };
  }
  // Default: state-wide JC purple
  return { radius: 5, fillColor: "#7d4ec3", color: "#5a3795",
           weight: 1.0, fillOpacity: 0.8 };
}

function bindSchoolPopup(f, layer, grantee) {
  const p = f.properties;
  const role = f._role;
  const roleLabel =
    role === "jc_in_tract" ? "Judy Center (in selected grantee's tract)" :
    role === "jc_near_miss" ? "Judy Center (catchment overlaps selected grantee's tracts)" :
    role === "candidate" ? "Candidate elementary school" :
    "Judy Center (statewide)";
  const tip = `<b>${escapeHtml((p.school_name || "").trim())}</b><br/>` +
              (p.cpg !== null && p.cpg !== undefined ? `CPG ${fmtCpg(p.cpg)} · ` : "") +
              `<span style="color:#666;font-size:11px;">${escapeHtml(roleLabel)}</span>`;
  layer.bindTooltip(tip, { className: "school-tip", sticky: true });
  const popup =
    `<b>${escapeHtml((p.school_name || "").trim())}</b><br/>` +
    `${escapeHtml(p.school_type || "")} · tract ${p.geoid20 || "—"}<br/>` +
    `${escapeHtml(p.address || "")}<br/>` +
    (p.cpg !== null && p.cpg !== undefined ? `CPG: <b>${fmtCpg(p.cpg)}</b><br/>` : "") +
    `<span style="color:#666;font-size:12px;">${escapeHtml(roleLabel)}</span>`;
  layer.bindPopup(popup);
}

function drawLegend() {
  const el = document.getElementById("map-legend");
  el.innerHTML =
    `<div class="legend-title">Legend</div>` +
    `<div class="row"><span class="swatch orange"></span>Selected grantee tracts</div>` +
    `<div class="row"><span class="swatch grey"></span>Other grantee tracts</div>` +
    `<div class="row"><span class="swatch dot purple"></span>Judy Center (statewide)</div>` +
    `<div class="row"><span class="swatch dot blue"></span>JC in selected tract</div>` +
    `<div class="row"><span class="swatch dot green"></span>JC catchment overlap</div>` +
    `<div class="row"><span class="swatch dot red"></span>Candidate elementary (sized by CPG)</div>` +
    `<div class="row"><span class="swatch dashed-circle"></span>Closest non-JC elem. fallback</div>`;
}

function selectGrantee(name) {
  state.selectedGrantee = name;
  document.querySelectorAll(".grantee-button").forEach((btn) => {
    btn.classList.toggle("selected", btn.dataset.grantee === name);
  });
  const g = state.granteeIndex[name];
  renderGranteeDetail(g);
  drawTracts(name);
  drawSchools(name);
  if (g) {
    const sel = state.rawTracts.features.filter((f) =>
      f.properties.grantees.includes(name)
    );
    if (sel.length) {
      const layer = L.geoJSON({ type: "FeatureCollection", features: sel });
      state.map.fitBounds(layer.getBounds().pad(0.15));
    }
  }
}

// ============= Helpers =============

function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  const f = v <= 1 ? v : v / 100;
  return Math.round(f * 100) + "%";
}
function fmtCpg(v) {
  if (v === null || v === undefined) return "—";
  return Math.round(v * 100) + "%";
}
function escapeHtml(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}
function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }
