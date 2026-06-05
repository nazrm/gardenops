import { clearChildren } from "../core/sanitize";

export interface ChipInputOptions<T> {
  label: string;
  placeholder: string;
  items: T[];
  getKey: (item: T) => string;
  getLabel: (item: T) => string;
  getSearchText?: (item: T) => string;
  selected: string[];
}

export interface ChipInputResult {
  container: HTMLElement;
  getSelectedKeys: () => string[];
  destroy: () => void;
}

export function createChipInput<T>(options: ChipInputOptions<T>): ChipInputResult {
  const {
    label,
    placeholder,
    items,
    getKey,
    getLabel,
    selected: initialSelected,
  } = options;
  const getSearch = options.getSearchText ?? ((item: T) => getLabel(item).toLowerCase());

  const selectedKeys = new Set<string>(initialSelected);

  // Root container
  const container = document.createElement("div");
  container.className = "chip-input";

  // Label
  const labelEl = document.createElement("label");
  labelEl.className = "chip-input__label";
  labelEl.textContent = label;
  container.appendChild(labelEl);

  // Chips area
  const chipsArea = document.createElement("div");
  chipsArea.className = "chip-input__chips";
  container.appendChild(chipsArea);

  // Input wrapper (input + dropdown)
  const inputWrapper = document.createElement("div");
  inputWrapper.className = "chip-input__input-wrapper";
  inputWrapper.style.position = "relative";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "chip-input__field";
  input.placeholder = placeholder;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-autocomplete", "list");
  input.autocomplete = "off";
  inputWrapper.appendChild(input);

  const dropdown = document.createElement("div");
  dropdown.className = "chip-input__dropdown";
  dropdown.setAttribute("role", "listbox");
  dropdown.hidden = true;
  inputWrapper.appendChild(dropdown);

  container.appendChild(inputWrapper);

  let focusIndex = -1;
  let blurTimeout: ReturnType<typeof setTimeout> | null = null;

  function renderChips(): void {
    clearChildren(chipsArea);
    for (const key of selectedKeys) {
      const item = items.find((i) => getKey(i) === key);
      if (!item) continue;

      const chip = document.createElement("span");
      chip.className = "chip-input__chip";
      chip.setAttribute("role", "group");
      chip.setAttribute("aria-label", getLabel(item));

      const chipLabel = document.createElement("span");
      chipLabel.className = "chip-input__chip-label";
      chipLabel.textContent = getLabel(item);
      chip.appendChild(chipLabel);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "chip-input__chip-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.setAttribute("aria-label", `Remove ${getLabel(item)}`);
      removeBtn.addEventListener("click", () => {
        selectedKeys.delete(key);
        renderChips();
        updateDropdown();
      });
      chip.appendChild(removeBtn);

      chipsArea.appendChild(chip);
    }
  }

  function getFilteredItems(): T[] {
    const query = input.value.trim().toLowerCase();
    return items.filter((item) => {
      if (selectedKeys.has(getKey(item))) return false;
      if (!query) return true;
      return getSearch(item).toLowerCase().includes(query);
    });
  }

  function updateDropdown(): void {
    const filtered = getFilteredItems();
    clearChildren(dropdown);
    focusIndex = -1;

    if (filtered.length === 0 || !document.activeElement || document.activeElement !== input) {
      dropdown.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      return;
    }

    const visible = filtered.slice(0, 6);
    for (let i = 0; i < visible.length; i++) {
      const item = visible[i] as T;
      const option = document.createElement("button");
      option.type = "button";
      option.className = "chip-input__option";
      option.setAttribute("role", "option");
      option.id = `chip-opt-${i}`;
      option.textContent = getLabel(item);
      option.addEventListener("mousedown", (e) => {
        e.preventDefault(); // prevent blur
        selectItem(item);
      });
      dropdown.appendChild(option);
    }

    dropdown.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function selectItem(item: T): void {
    selectedKeys.add(getKey(item));
    input.value = "";
    renderChips();
    updateDropdown();
    input.focus();
  }

  function highlightOption(index: number): void {
    const options = dropdown.querySelectorAll<HTMLElement>(".chip-input__option");
    options.forEach((opt, i) => {
      opt.classList.toggle("focused", i === index);
    });
    if (index >= 0 && index < options.length) {
      input.setAttribute("aria-activedescendant", `chip-opt-${index}`);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  // Event handlers
  input.addEventListener("input", () => {
    updateDropdown();
  });

  input.addEventListener("focus", () => {
    updateDropdown();
  });

  input.addEventListener("blur", () => {
    blurTimeout = setTimeout(() => {
      dropdown.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      focusIndex = -1;
    }, 150);
  });

  input.addEventListener("keydown", (e) => {
    const filtered = getFilteredItems().slice(0, 6);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (dropdown.hidden) { updateDropdown(); return; }
      focusIndex = Math.min(focusIndex + 1, filtered.length - 1);
      highlightOption(focusIndex);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusIndex = Math.max(focusIndex - 1, 0);
      highlightOption(focusIndex);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (focusIndex >= 0 && focusIndex < filtered.length) {
        selectItem(filtered[focusIndex] as T);
      }
    } else if (e.key === "Escape") {
      dropdown.hidden = true;
      input.setAttribute("aria-expanded", "false");
      focusIndex = -1;
    } else if (e.key === "Backspace" && input.value === "") {
      // Remove last chip
      const keys = [...selectedKeys];
      if (keys.length > 0) {
        selectedKeys.delete(keys[keys.length - 1] as string);
        renderChips();
        updateDropdown();
      }
    }
  });

  // Initial render
  renderChips();

  return {
    container,
    getSelectedKeys: () => [...selectedKeys],
    destroy: () => {
      if (blurTimeout) clearTimeout(blurTimeout);
    },
  };
}
