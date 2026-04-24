const fileInput = document.getElementById("fileInput");
const searchInput = document.getElementById("searchInput");
const downloadBtn = document.getElementById("downloadBtn");
const optionsContainer = document.getElementById("optionsContainer");
const fileNameEl = document.getElementById("fileName");
const statsEl = document.getElementById("stats");
const optionRowTemplate = document.getElementById("optionRowTemplate");

let currentFileName = "";
let options = [];
let openOptionName = null;

fileInput.addEventListener("change", handleFileUpload);
searchInput.addEventListener("input", renderOptions);
downloadBtn.addEventListener("click", downloadUpdatedFile);

function handleFileUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = () => {
    const content = String(reader.result || "");
    options = parseTrc(content);
    currentFileName = file.name.replace(/\.[^.]+$/, "");
    fileNameEl.textContent = `Loaded: ${file.name}`;
    downloadBtn.disabled = options.length === 0;
    renderOptions();
  };
  reader.readAsText(file);
}

function parseTrc(content) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const parsed = [];
  let current = null;

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r/g, "");
    const trimmed = line.trim();
    if (!trimmed) continue;

    const isValueLine = /^\s/.test(line);
    if (!isValueLine) {
      if (current) parsed.push(current);
      current = { name: trimmed, values: [], selectedIndex: 0 };
      continue;
    }

    if (current) {
      current.values.push(trimmed);
    }
  }

  if (current) parsed.push(current);

  for (const item of parsed) {
    classifyAndNormalizeOption(item);
  }

  return parsed;
}

/**
 * valueMode "wert": en az bir wert_* değeri var → UI'da sadece wert_01..wert_04.
 * valueMode "aktiv": wert yok, aktiv/nicht_aktiv (veya boş) → UI'da sadece bu ikisi.
 * valueMode "full": diğer tüm kombinasyonlar → tüm değerler listelenir.
 * originalValues: TRC'deki tüm alternatif satırlar (dışa aktarımda seçilenden sonra korunur).
 */
function classifyAndNormalizeOption(item) {
  const raw = [...item.values];

  if (raw.length === 0) {
    item.valueMode = "aktiv";
    item.originalValues = ["nicht_aktiv", "aktiv"];
    item.values = ["nicht_aktiv", "aktiv"];
    item.selectedIndex = 0;
    return;
  }

  const hasWert = raw.some((v) => /^wert_\d+$/i.test(v));
  if (hasWert) {
    item.valueMode = "wert";
    item.originalValues = raw;
    item.values = ["wert_01", "wert_02", "wert_03", "wert_04"];
    const first = raw[0] || "wert_01";
    const m = /^wert_(\d+)$/i.exec(first);
    let idx = 0;
    if (m) {
      const n = parseInt(m[1], 10);
      if (n >= 1 && n <= 4) idx = n - 1;
      else if (n > 4) idx = 3;
      else idx = 0;
    }
    item.selectedIndex = idx;
    return;
  }

  const hasAktiv = raw.some((v) => v === "aktiv" || v === "nicht_aktiv");
  if (hasAktiv) {
    item.valueMode = "aktiv";
    item.originalValues = raw;
    item.values = ["nicht_aktiv", "aktiv"];
    const first = raw[0];
    item.selectedIndex = first === "aktiv" ? 1 : 0;
    return;
  }

  item.valueMode = "full";
  item.originalValues = null;
  item.selectedIndex = 0;
}

function renderOptions() {
  optionsContainer.innerHTML = "";

  const query = searchInput.value.trim().toLowerCase();
  const filtered = options.filter((item) =>
    item.name.toLowerCase().includes(query)
  );

  statsEl.textContent = `${filtered.length} / ${options.length} options shown`;

  if (filtered.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No matching options.";
    optionsContainer.appendChild(empty);
    return;
  }

  for (const option of filtered) {
    const row = optionRowTemplate.content.firstElementChild.cloneNode(true);
    const head = row.querySelector(".option-head");
    const nameEl = row.querySelector(".option-name");
    const currentEl = row.querySelector(".option-current");
    const selectEl = row.querySelector(".option-select");

    nameEl.textContent = option.name;
    currentEl.textContent = option.values[option.selectedIndex] || "-";

    option.values.forEach((value, index) => {
      const selectOption = document.createElement("option");
      selectOption.value = String(index);
      selectOption.textContent = value;
      selectEl.appendChild(selectOption);
    });
    selectEl.value = String(option.selectedIndex);

    if (openOptionName === option.name) {
      row.classList.add("open");
    }

    head.addEventListener("click", () => {
      openOptionName = openOptionName === option.name ? null : option.name;
      renderOptions();
    });

    selectEl.addEventListener("change", (event) => {
      option.selectedIndex = Number(event.target.value);
      renderOptions();
    });

    optionsContainer.appendChild(row);
  }
}

function serializeTrc() {
  const lines = [];
  for (const option of options) {
    lines.push(option.name);
    if (option.values.length > 0) {
      const selectedValue = option.values[option.selectedIndex];
      lines.push(`\t${selectedValue}`);
      if (
        option.originalValues &&
        (option.valueMode === "wert" || option.valueMode === "aktiv")
      ) {
        for (const v of option.originalValues) {
          if (v !== selectedValue) lines.push(`\t${v}`);
        }
      } else {
        for (let i = 0; i < option.values.length; i += 1) {
          if (i === option.selectedIndex) continue;
          lines.push(`\t${option.values[i]}`);
        }
      }
    }
  }
  return `${lines.join("\n")}\n`;
}

function downloadUpdatedFile() {
  const output = serializeTrc();
  const blob = new Blob([output], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${currentFileName || "updated"}_updated.TRC`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  renderOptions();
}
