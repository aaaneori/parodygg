/* Shared between index.html and champion.html: Data Dragon access,
   champion icons, role labels. Loaded before each page's own script. */

const DDRAGON_FALLBACK_VERSION = "14.16.1";

const ROLE_LABELS = {
  TOP: "Top",
  JUNGLE: "Jungle",
  MIDDLE: "Mid",
  BOTTOM: "ADC",
  UTILITY: "Support"
};

// Data Dragon changes once per patch, roughly every two weeks, but champion.json
// is a few hundred KB and both pages were refetching it on every single load.
// A day is well under a patch cycle, so the data is effectively always current.
const DDRAGON_CACHE_KEY = "ddragon_cache_v1";
const DDRAGON_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

function roleLabelOf(role) {
  return ROLE_LABELS[role] || role;
}

function round2(num) {
  return Math.round(num * 100) / 100;
}

function championIconUrl(championName, version) {
  return `https://ddragon.leagueoflegends.com/cdn/${version}/img/champion/${championName}.png`;
}

// className/id differ per page: a small round icon in the table, a large
// rounded square on the champion page.
function championIconHtml(championName, version, className, id) {
  const initials = championName.replace(/[a-z']/g, '').slice(0, 2) || championName.slice(0, 2).toUpperCase();
  const idAttr = id ? ` id="${id}"` : "";
  const fallbackId = id ? ` id=&quot;${id}&quot;` : "";
  return `<img${idAttr} class="${className}" src="${championIconUrl(championName, version)}" alt="${championName}" loading="lazy"
    onerror="this.outerHTML='<div${fallbackId} class=&quot;${className} icon-fallback&quot;>${initials}</div>'">`;
}

async function fetchDdragonVersion() {
  const res = await fetch("https://ddragon.leagueoflegends.com/api/versions.json");
  const versions = await res.json();
  return versions[0];
}

async function fetchChampionNameMap(version) {
  const res = await fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/en_US/champion.json`);
  const data = await res.json();
  const map = {};
  for (const champKey in data.data) {
    map[champKey] = data.data[champKey].name;
  }
  return map;
}

function readDdragonCache() {
  // localStorage can throw (private mode, quota, disabled storage) - a cache
  // miss is always survivable, so swallow and refetch.
  try {
    const raw = localStorage.getItem(DDRAGON_CACHE_KEY);
    if (!raw) return null;

    const cached = JSON.parse(raw);
    if (!cached.version || !cached.nameMap) return null;
    if (Date.now() - cached.savedAt > DDRAGON_CACHE_TTL_MS) return null;

    return { version: cached.version, nameMap: cached.nameMap };
  } catch (err) {
    return null;
  }
}

function writeDdragonCache(version, nameMap) {
  try {
    localStorage.setItem(DDRAGON_CACHE_KEY, JSON.stringify({
      savedAt: Date.now(), version, nameMap
    }));
  } catch (err) {
    // Not being able to cache is not a reason to fail the page.
  }
}

/**
 * { version, nameMap } for Data Dragon, from localStorage when fresh.
 * Never throws: on a network failure it falls back to a pinned version and
 * an empty name map, so the page still renders with raw champion keys.
 */
async function loadDdragonData() {
  const cached = readDdragonCache();
  if (cached) return cached;

  try {
    const version = await fetchDdragonVersion();
    const nameMap = await fetchChampionNameMap(version);
    writeDdragonCache(version, nameMap);
    return { version, nameMap };
  } catch (err) {
    return { version: DDRAGON_FALLBACK_VERSION, nameMap: {} };
  }
}
