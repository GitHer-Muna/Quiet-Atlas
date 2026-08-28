(() => {
  "use strict";

  const configuredUrl = window.QUIET_ATLAS_CONFIG && window.QUIET_ATLAS_CONFIG.apiUrl;
  const API_URL = (configuredUrl || "").replace(/\/$/, "");
  const state = { items: [], nextCursor: null };
  const $ = (selector) => document.querySelector(selector);

  function setStatus(message) { $("#status").textContent = message || ""; }
  function escDate(value) {
    if (!value) return "Undated page";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
  }
  function weather(entry) { return entry.weatherSnapshot || {}; }
  function temperature(entry) {
    const value = weather(entry).temperatureC;
    return value === undefined || value === null ? "—" : `${Number(value).toFixed(1)}°C`;
  }
  function weatherLine(entry) {
    const data = weather(entry);
    const wind = data.windKmh === undefined ? "—" : `${Number(data.windKmh).toFixed(0)} km/h wind`;
    const humidity = data.humidityPercent === undefined ? "—" : `${data.humidityPercent}% humidity`;
    return `${wind} · ${humidity}`;
  }
  function coordinates(entry) {
    if (entry.lat === undefined || entry.lon === undefined) return "Coordinates kept in the ledger";
    return `${Number(entry.lat).toFixed(4)}° N · ${Number(entry.lon).toFixed(4)}° E · population ${entry.population || "unknown"}`;
  }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function currentFeatured() {
    const today = new Date().toISOString().slice(0, 10);
    return state.items.find((entry) => entry.source === "daily" && (entry.createdAt || "").slice(0, 10) === today) || state.items[0] || null;
  }
  function renderWeather(entry) {
    const data = weather(entry);
    const card = make("aside", "weather-card");
    card.append(make("span", "weather-label", "Weather, observed"));
    card.append(make("div", "temperature", temperature(entry)));
    card.append(make("div", "condition", data.condition || "conditions unrecorded"));
    card.append(make("p", "weather-details", `${weatherLine(entry)}\n${data.observedAt || "observation time unknown"}`));
    return card;
  }
  function renderFeatured(entry) {
    const root = $("#featured");
    root.replaceChildren();
    root.dataset.source = entry ? (entry.source || "daily") : "";
    if (!entry) {
      root.append(make("p", "empty", API_URL ? "The keeper is looking for the first page." : "Add the deployed API URL in frontend/config.js to open the atlas."));
      return;
    }
    const meta = make("div", "entry-meta");
    meta.append(make("h2", "place", entry.placeName || "Unnamed place"));
    meta.append(make("span", "country", entry.country || "Unknown country"));
    root.append(meta, make("p", "coordinates", coordinates(entry)));
    const body = make("div", "featured-body");
    const prose = make("div");
    prose.append(make("p", "keeper-text", entry.keeperEntry || "The keeper left a blank page."));
    prose.append(make("p", "source", `${entry.source === "requested" ? "Requested page" : "Daily page"} · ${escDate(entry.createdAt)}`));
    body.append(prose, renderWeather(entry));
    root.append(body);
  }
  function renderCard(entry) {
    const card = make("article", "entry-card");
    const meta = make("div", "entry-meta");
    meta.append(make("h3", "place", entry.placeName || "Unnamed place"));
    meta.append(make("span", "country", entry.country || ""));
    card.append(meta);
    card.append(make("p", "card-text", entry.keeperEntry || "A page awaiting its keeper."));
    const bottom = make("div", "card-bottom", `${temperature(entry)} · ${weather(entry).condition || "weather unknown"}\n${escDate(entry.createdAt)}`);
    card.append(bottom);
    return card;
  }
  function renderEntries() {
    const grid = $("#entries");
    grid.replaceChildren();
    const featured = currentFeatured();
    const rest = state.items.filter((entry) => !featured || entry.entryId !== featured.entryId);
    if (!rest.length) { grid.append(make("p", "empty", "More pages will gather here as the atlas turns its daily leaf.")); }
    else rest.forEach((entry) => grid.append(renderCard(entry)));
    $("#more-button").hidden = !state.nextCursor;
  }
  async function getEntries(cursor) {
    if (!API_URL) { renderFeatured(null); renderEntries(); return; }
    const params = new URLSearchParams({ limit: "13" });
    if (cursor) params.set("cursor", cursor);
    const response = await fetch(`${API_URL}/entries?${params}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || "The atlas is resting.");
    state.items = cursor ? state.items.concat(data.items || []) : (data.items || []);
    state.nextCursor = data.nextCursor || null;
    renderFeatured(currentFeatured());
    renderEntries();
  }
  async function submitPlace(event) {
    event.preventDefault();
    const input = $("#place-name");
    const button = $("#ask-button");
    const message = $("#form-message");
    message.className = "form-message";
    if (!API_URL) { message.textContent = "The atlas has not been connected to its API yet."; return; }
    button.disabled = true;
    button.textContent = "Consulting the keeper…";
    message.textContent = "";
    try {
      const response = await fetch(`${API_URL}/entries/request`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ placeName: input.value.trim() }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "The atlas is resting. Please try again.");
      state.items.unshift(data.entry);
      renderFeatured(currentFeatured());
      renderEntries();
      input.value = "";
      message.textContent = "A new page has joined the ledger.";
    } catch (error) {
      message.className = "form-message error";
      message.textContent = error.message;
    } finally {
      button.disabled = false;
      button.innerHTML = 'Open a page <span aria-hidden="true">→</span>';
    }
  }
  $("#ask-form").addEventListener("submit", submitPlace);
  $("#more-button").addEventListener("click", async () => {
    try { setStatus("Turning back through the ledger…"); await getEntries(state.nextCursor); setStatus(""); }
    catch (error) { setStatus(error.message); }
  });
  getEntries().catch((error) => { setStatus(error.message); renderFeatured(null); renderEntries(); });
})();
